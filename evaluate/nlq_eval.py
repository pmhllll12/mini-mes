"""
자연어 질의(/query) 평가 - 실제 LLM을 호출하므로 API 비용이 든다.

질문 세트(nlq_questions.json)를 제공자별로 /query에 보내고 채점한다.
- 도구 선택: 기대한 도구가 성공(ok)으로 한 번 이상 호출됐는가
- 인자: 그 호출의 equipment_ids(빈 배열 = 전체)와 기간(start/end, 일일 리포트는 start_date/end_date)이 기대와 맞는가
    today = 오늘 00:00(KST)부터 현재(또는 내일 00:00)까지, yesterday = 어제 00:00~오늘 00:00,
    last_Nh = 현재-N시간~현재 (허용 오차 10분)
- 근거(truth): 설비·기간이 맞게 호출된 도구 결과의 정답(최다 불량 유형, 이상 최다 설비)이 답변에 그대로 들어 있는가
    (데이터가 없으면 "없다"고 답해야 함). 도구 결과를 지어내거나 바꿔 말하지 않았는지 확인한다.
- 답변 문구(answer_mentions_any): 등록되지 않은 설비, 조회 불가 항목, 범위 밖 질문을 추측 없이 안내하는가
- 단위(units, 불량 집계 질문): 답변의 "숫자+건"/"숫자+개"가 도구 결과의 품질이벤트_건수/불량수량_개와 맞는가.
    불량 수량을 "건"으로, 이벤트 건수를 "개"로 말하면 실패 (두 값이 같으면 어느 쪽도 허용, 숫자가 없으면 통과)

사용법 (API가 키를 갖고 떠 있어야 함):
    python3 evaluate/nlq_eval.py --providers claude gemini --api-url http://localhost:8001
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")
TOLERANCE = timedelta(minutes=10)
NO_DATA_WORDS = ["없", "0건", "기록이 없"]


def expected_range(kind: str, now: datetime):
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "today":
        return today, (now, today + timedelta(days=1))
    if kind == "yesterday":
        return today - timedelta(days=1), (today, today)
    hours = int(kind.removeprefix("last_").removesuffix("h"))
    return now - timedelta(hours=hours), (now, now)


def range_ok(args: dict, kind: str, now: datetime) -> bool:
    try:
        start, end = datetime.fromisoformat(args["start"]), datetime.fromisoformat(args["end"])
    except (KeyError, TypeError, ValueError):
        return False
    exp_start, (end_lo, end_hi) = expected_range(kind, now)
    return abs(start - exp_start) <= TOLERANCE and end_lo - TOLERANCE <= end <= end_hi + TOLERANCE


def dates_ok(args: dict, kind: str, now: datetime) -> bool:
    """일일 리포트 날짜 인자 (KST 날짜). yesterday = 어제 하루"""
    expected = {"yesterday": (now - timedelta(days=1)).date(), "today": now.date()}[kind]
    return args.get("start_date") == args.get("end_date") == expected.isoformat()


def equipment_ok(args: dict, expected: list) -> bool:
    got = set(args.get("equipment_ids") or [])
    return got == set(expected) or (not expected and got == {"EQ-001", "EQ-002", "EQ-003"})


def truth_from_result(kind: str, result: dict):
    """올바른 설비·기간으로 호출된 도구 결과에서 정답을 뽑는다. 데이터가 없으면 None."""
    rows = result.get("results", [])
    if kind == "top_defect_type":
        return max(rows, key=lambda r: (r["품질이벤트_건수"], r["불량수량_개"]))["defect_type"] if rows else None
    if kind == "top_anomaly_equipment":
        rows = [r for r in rows if r["anomaly_count"] > 0]
        return max(rows, key=lambda r: r["anomaly_count"])["equipment_id"] if rows else None
    raise ValueError(kind)


UNIT_NUMBER = re.compile(r"(\d[\d,]*)\s*(건|개)")


def units_ok(answer: str, rows: list) -> bool:
    """답변의 숫자+단위가 불량 집계 결과의 이벤트 건수(건)·불량 수량(개)과 어긋나지 않는가.
    한쪽 값에만 있는 숫자에 다른 쪽 단위를 붙이면 실패. 결과에 없는 숫자(다른 계산 등)는 판단하지 않는다."""
    events = {r["품질이벤트_건수"] for r in rows}
    qtys = {r["불량수량_개"] for r in rows}
    for number, unit in UNIT_NUMBER.findall(answer):
        n = int(number.replace(",", ""))
        if unit == "건" and n in qtys and n not in events:
            return False
        if unit == "개" and n in events and n not in qtys:
            return False
    return True


def grade(item: dict, body: dict, now: datetime) -> dict:
    expect = item["expect"]
    calls = body.get("tool_calls", [])
    ok_calls = [c for c in calls if c["ok"]]
    checks = {}
    if "tools" in expect:
        matching = [c for c in ok_calls if c["name"] in expect["tools"]]
        checks["tool"] = bool(matching)
        if "equipment_ids" in expect:
            checks["equipment"] = any(equipment_ok(c["input"], expect["equipment_ids"]) for c in matching)
        if "range" in expect:
            checks["range"] = any(range_ok(c["input"], expect["range"], now) for c in matching)
        if "dates" in expect:  # 표에서는 기간 인자와 같은 칸으로 집계
            checks["range"] = any(dates_ok(c["input"], expect["dates"], now) for c in matching)
    answer = body.get("answer", "")
    if "truth" in expect:
        # 근거: 설비·기간이 맞게 호출된 도구 결과의 정답이 답변에 그대로 들어 있는가 (없으면 "없다"고 답해야 함)
        correct = [c for c in ok_calls if c["name"] in expect["tools"]
                   and equipment_ok(c["input"], expect["equipment_ids"]) and range_ok(c["input"], expect["range"], now)]
        if correct:
            truth = truth_from_result(expect["truth"], correct[-1]["result"])
            checks["grounded"] = (truth in answer) if truth else any(w in answer for w in NO_DATA_WORDS)
            checks["_truth"] = truth
            if expect["truth"] == "top_defect_type":
                checks["units"] = units_ok(answer, correct[-1]["result"].get("results", []))
        else:
            checks["grounded"] = False
    if "answer_mentions_any" in expect:
        checks["answer"] = any(w in answer for w in expect["answer_mentions_any"])
    checks["completed"] = body.get("stop") == "answer"
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--providers", nargs="+", default=["claude", "gemini"])
    parser.add_argument("--questions", default=os.path.join(os.path.dirname(__file__), "nlq_questions.json"))
    parser.add_argument("--out", default=None, help="질문별 응답·채점 결과를 JSON으로 저장할 경로")
    parser.add_argument("--delay", type=float, default=0, help="질문 사이 대기(초) - 무료 등급의 분당 요청 한도 대응")
    parser.add_argument("--only", nargs="+", default=None, help="이 id의 질문만 평가 - 무료 등급 하루 한도 안에서 나눠 평가")
    args = parser.parse_args()
    import requests  # 실제 호출할 때만 필요 (채점 함수 단위 테스트는 requests 없이)

    items = json.load(open(args.questions, encoding="utf-8"))
    if args.only:
        unknown = set(args.only) - {item["id"] for item in items}
        if unknown:
            parser.error(f"없는 질문 id: {', '.join(sorted(unknown))}")
        items = [item for item in items if item["id"] in args.only]
    report = []
    for provider in args.providers:
        for i, item in enumerate(items):
            if i and args.delay:
                time.sleep(args.delay)
            now = datetime.now(KST)
            t0 = time.time()
            res = requests.post(f"{args.api_url}/query", timeout=300,
                                json={"question": item["question"], "provider": provider})
            elapsed = time.time() - t0
            body = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
            checks = grade(item, body, now) if res.status_code == 200 else {"http": False}
            passed = all(v for k, v in checks.items() if not k.startswith("_"))
            report.append({"provider": provider, "id": item["id"], "status": res.status_code, "passed": passed,
                           "checks": checks, "seconds": round(elapsed, 1), "model": body.get("model"),
                           "tools": [(c["name"], c["ok"]) for c in body.get("tool_calls", [])],
                           "tool_calls": body.get("tool_calls", []),  # 채점 규칙을 바꿔도 다시 채점할 수 있게
                           "answer": body.get("answer", body.get("detail"))})
            print(f"[{provider}] {item['id']:<20} {'PASS' if passed else 'FAIL'} {elapsed:5.1f}s "
                  f"{ {k: v for k, v in checks.items() if not k.startswith('_')} }")

    print("\n| 제공자 | 모델 | 통과 | 도구 선택 | 설비 인자 | 기간 인자 | 근거(정답 포함) | 단위 | 안내 문구 | 평균 응답(초) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for provider in args.providers:
        rows = [r for r in report if r["provider"] == provider]

        def rate(key):
            vals = [r["checks"][key] for r in rows if key in r["checks"]]
            return f"{sum(vals)}/{len(vals)}" if vals else "-"
        model = next((r["model"] for r in rows if r["model"]), "-")
        print(f"| {provider} | {model} | {sum(r['passed'] for r in rows)}/{len(rows)} | {rate('tool')} | {rate('equipment')} | "
              f"{rate('range')} | {rate('grounded')} | {rate('units')} | {rate('answer')} | {sum(r['seconds'] for r in rows) / len(rows):.1f} |")
    if args.out:
        json.dump(report, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
