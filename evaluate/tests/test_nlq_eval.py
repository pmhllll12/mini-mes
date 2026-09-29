"""자연어 질의 채점 단위 테스트 - 실제 LLM 답변(평가에서 나온 문장)으로 단위 검사를 확인 (LLM 호출 없음)"""
from datetime import datetime

from nlq_eval import KST, grade, units_ok


def _rows(*pairs):
    return [{"equipment_id": "EQ-002", "defect_type": t, "품질이벤트_건수": e, "불량수량_개": q} for t, e, q in pairs]


def test_quantity_called_kun_fails():
    # 2026-09-29 오후 실제 답변 (이벤트 119건, 불량 수량 125개)
    rows = _rows(("dimension_out", 119, 125), ("burr", 66, 69))
    assert not units_ok("오늘(2026-09-29) EQ-002에서 가장 많이 발생한 불량 유형은 dimension_out으로, 125건이 집계되었습니다.", rows)


def test_quantity_called_kun_fails_when_two_types_share_quantity():
    # 2026-09-28 실제 답변 요지 (dimension_out 213건·217개, scratch 204건·217개)
    rows = _rows(("dimension_out", 213, 217), ("scratch", 204, 217))
    assert not units_ok("최다 불량은 dimension_out 217건이며, scratch도 217건으로 동일합니다.", rows)


def test_quantity_with_gae_passes():
    # 필드 이름 수정 후 실제 답변 (이벤트 121건, 불량 수량 127개)
    rows = _rows(("dimension_out", 121, 127), ("burr", 66, 69))
    assert units_ok("오늘(2026-09-29) EQ-002에서 가장 많이 발생한 불량 유형은 'dimension_out'으로 127개 입니다.", rows)
    assert units_ok("dimension_out으로, 127개의 불량품이 발생했습니다.", rows)


def test_event_count_with_kun_passes_and_with_gae_fails():
    rows = _rows(("dimension_out", 121, 127))
    assert units_ok("dimension_out 121건 (불량 수량 127개)", rows)
    assert not units_ok("dimension_out 121개", rows)


def test_same_value_either_unit_and_no_number():
    rows = _rows(("burr", 5, 5))
    assert units_ok("burr 5건", rows) and units_ok("burr 5개", rows)
    assert units_ok("가장 많은 불량 유형은 burr입니다.", rows)


def test_numbers_with_commas():
    rows = _rows(("scratch", 1200, 1234))
    assert not units_ok("scratch 1,234건", rows)
    assert units_ok("scratch 1,234개", rows)


def test_grade_adds_units_check_for_defect_question():
    now = datetime(2026, 9, 29, 16, 10, tzinfo=KST)
    item = {"expect": {"tools": ["get_defect_summary"], "equipment_ids": ["EQ-002"], "range": "today",
                       "truth": "top_defect_type"}}
    call = {"name": "get_defect_summary", "ok": True,
            "input": {"equipment_ids": ["EQ-002"], "start": "2026-09-29T00:00:00+09:00", "end": "2026-09-30T00:00:00+09:00"},
            "result": {"results": _rows(("dimension_out", 119, 125), ("burr", 66, 69))}}
    body = {"tool_calls": [call], "stop": "answer", "answer": "dimension_out으로, 125건이 집계되었습니다."}

    checks = grade(item, body, now)
    assert checks["grounded"] is True   # 정답 유형은 들어 있음 (기존 채점은 통과)
    assert checks["units"] is False     # 새 검사가 단위 오류를 잡음


def test_dates_check_for_daily_report_question():
    now = datetime(2026, 9, 29, 1, 0, tzinfo=KST)  # KST 새벽이라 UTC로는 아직 9/28
    item = {"expect": {"tools": ["get_daily_reports"], "equipment_ids": [], "dates": "yesterday"}}

    def body(start_date, end_date):
        return {"stop": "answer", "answer": "", "tool_calls": [{
            "name": "get_daily_reports", "ok": True, "result": {},
            "input": {"equipment_ids": [], "start_date": start_date, "end_date": end_date}}]}

    assert grade(item, body("2026-09-28", "2026-09-28"), now)["range"] is True
    assert grade(item, body("2026-09-27", "2026-09-27"), now)["range"] is False
    assert grade(item, body("2026-09-28", "2026-09-29"), now)["range"] is False


def test_language_check_rejects_chinese_answer():
    from nlq_eval import language_ok
    # 2026-09-29 로컬 qwen2.5:7b-instruct의 실제 답변 앞부분 (anomaly_top_24h)
    assert not language_ok("感谢提供的数据。以下是对这些数据的解读： 1. 设备编号为 “EQ-001”的异常报告: 分析期间共得到1324个指标得分，发现457个异常。")
    assert language_ok("최근 24시간 동안 이상이 가장 많이 탐지된 설비는 EQ-003입니다.")
    assert language_ok("불량(不良) 유형은 burr가 가장 많습니다.")  # 가끔 쓰는 한자어는 허용
    assert not language_ok("")


def test_ids_check_rejects_wrong_or_invented_equipment():
    from nlq_eval import ids_ok
    question = "LINE-B 설비들의 최근 1시간 OEE는?"
    # 2026-09-29 로컬 모델의 실제 답변 (by_line, 실제 LINE-B는 EQ-003뿐)
    wrong = '등록되지 않은 설비 ID가 있어 조회할 수 없습니다. 등록된 설비 중 LINE-B 설비는 "EQ-001", "EQ-002", "EQ-003"입니다.'
    assert not ids_ok(wrong, question, ["EQ-003"])
    assert not ids_ok("EQ-B1의 OEE는 0.8입니다.", question, ["EQ-003"])      # 지어낸 ID
    assert ids_ok("LINE-B의 포장 설비 1호(EQ-003) OEE는 0.8713입니다.", question, ["EQ-003"])
    assert ids_ok("EQ-001은 0.9, EQ-003은 0.8입니다.", "오늘 전체 설비 OEE 알려줘", [])  # 전체면 등록된 설비 허용
    assert ids_ok("EQ-009는 등록되지 않은 설비 ID입니다.", "EQ-009의 오늘 OEE 알려줘", [])  # 질문에 나온 ID 허용
