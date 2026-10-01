"""
자연어 질의 테스트 - 실제 LLM은 호출하지 않는다 (CI에 키가 없음).
- 도구 인자 검증·실행 (실제 DB)
- /query 엔드포인트: 키 없을 때 503, 가짜 제공자로 도구 호출·결과 전달
- Claude/Gemini 어댑터의 수동 루프: 가짜 클라이언트로 요청 형식·라운드 제한·거절 처리 확인
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import main
from database import SessionLocal
from nlq_providers import KST, ClaudeProvider, GeminiProvider, NLQProviderError, NLQResult, gemini_quota_message, user_message
from nlq_quota import DailyQuota
from nlq_tools import ToolError, execute_tool

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=KST)
RANGE = {"start": (NOW - timedelta(days=1)).isoformat(), "end": NOW.isoformat()}


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------- 도구 ----------

def test_tool_uses_all_equipment_when_ids_empty(db):
    result = execute_tool(db, "get_oee", {"equipment_ids": [], **RANGE})
    assert {"EQ-001", "EQ-002", "EQ-003"} <= {r["equipment_id"] for r in result["results"]}


def test_list_equipment_tool(db):
    ids = [e["equipment_id"] for e in execute_tool(db, "list_equipment", {})["equipment"]]
    assert {"EQ-001", "EQ-002", "EQ-003"} <= set(ids)


@pytest.mark.parametrize("args, message", [
    ({"equipment_ids": ["EQ-999"], **RANGE}, "등록되지 않은 설비 ID"),
    ({"equipment_ids": [], "start": "2026-09-27T00:00:00", "end": RANGE["end"]}, "시간대가 없습니다"),
    ({"equipment_ids": [], "start": RANGE["end"], "end": RANGE["start"]}, "start는 end보다 앞"),
    ({"equipment_ids": [], "start": (NOW - timedelta(days=31)).isoformat(), "end": RANGE["end"]}, "최대 30일"),
    ({"equipment_ids": [], "start": "어제", "end": RANGE["end"]}, "형식이 잘못"),
])
def test_tool_rejects_invalid_arguments(db, args, message):
    with pytest.raises(ToolError, match=message):
        execute_tool(db, "get_defect_summary", args)


def test_defect_summary_tool_labels_units(db):
    """불량 수량(개)을 건수로 말하던 문제로, 도구 결과 필드 이름에 단위를 드러낸다 (조회 API 형식은 그대로)"""
    import models
    log = models.ProductionLog(equipment_id="EQ-002", ts=NOW - timedelta(hours=1), qty_good=0, qty_defect=3,
                               cycle_time_sec=10, planned_time_sec=60)
    db.add(log)
    db.flush()
    db.add(models.QualityEvent(equipment_id="EQ-002", ts=NOW - timedelta(hours=1), defect_type="burr",
                               production_log_id=log.log_id))
    db.commit()

    rows = execute_tool(db, "get_defect_summary", {"equipment_ids": ["EQ-002"], **RANGE})["results"]
    burr = next(r for r in rows if r["defect_type"] == "burr")
    assert set(burr) == {"equipment_id", "defect_type", "품질이벤트_건수", "불량수량_개"}
    assert (burr["품질이벤트_건수"], burr["불량수량_개"]) >= (1, 3)


def test_drift_alarms_tool_labels_units(db):
    import models
    raised = NOW - timedelta(hours=3)
    db.add(models.DriftAlarm(equipment_id="EQ-003", raised_ts=raised, raised_score=1.4, threshold=1.0,
                             cleared_ts=raised + timedelta(minutes=5)))
    db.commit()

    result = execute_tool(db, "get_drift_alarms", {"equipment_ids": ["EQ-003"], **RANGE})["results"]
    assert [r["equipment_id"] for r in result] == ["EQ-003"]
    alarm = next(a for a in result[0]["경보_목록"] if a["시작_점수"] == 1.4)
    assert alarm["지속시간_초"] == 300.0 and alarm["경보_중"] is False
    assert result[0]["경보_횟수"] >= 1


def test_tool_results_use_kst_timestamps(db):
    """DB 시각(UTC)을 KST로 돌려준다 - 09-30 평가에서 UTC 시각을 KST 기간 밖으로 오해해 "경보 없음"이라고 답함"""
    import models
    raised = NOW - timedelta(hours=2)
    db.add(models.DriftAlarm(equipment_id="EQ-003", raised_ts=raised, raised_score=1.7, threshold=1.0,
                             cleared_ts=raised + timedelta(minutes=1)))
    db.commit()

    result = execute_tool(db, "get_drift_alarms", {"equipment_ids": ["EQ-003"], **RANGE})["results"]
    alarm = next(a for a in result[0]["경보_목록"] if a["시작_점수"] == 1.7)
    assert alarm["시작_시각"].endswith("+09:00") and alarm["해제_시각"].endswith("+09:00")
    assert datetime.fromisoformat(alarm["시작_시각"]) == raised


def test_daily_reports_tool_lists_missing_dates(db):
    from datetime import date

    import models
    from sqlalchemy import text
    db.add(models.DailyReport(report_date=date(2020, 1, 15), equipment_id="EQ-001", availability=0.5, quality_rate=0.9,
                              oee=0.45, total_qty=100, total_defect=10, top_defect_type="burr", top_defect_events=4,
                              top_defect_qty=6, anomaly_scored=50, anomaly_count=2, drift_alarms=1, drift_alarm_sec=120))
    db.commit()
    try:
        out = execute_tool(db, "get_daily_reports",
                           {"equipment_ids": ["EQ-001"], "start_date": "2020-01-15", "end_date": "2020-01-16"})
        assert out["리포트_없는_날짜"] == ["2020-01-16"]
        row = out["results"][0]
        assert (row["report_date"], row["OEE"], row["총생산_개"], row["최다불량_수량_개"]) == ("2020-01-15", 0.45, 100, 6)
        assert row["열화경보_지속_초"] == 120.0
    finally:
        db.execute(text("DELETE FROM daily_report WHERE report_date = '2020-01-15'"))
        db.commit()


@pytest.mark.parametrize("args, message", [
    ({"equipment_ids": [], "start_date": "어제", "end_date": "2020-01-15"}, "형식이 잘못"),
    ({"equipment_ids": [], "start_date": "2020-01-16", "end_date": "2020-01-15"}, "늦을 수 없습니다"),
    ({"equipment_ids": [], "start_date": "2020-01-01", "end_date": "2020-02-15"}, "최대 31일"),
])
def test_daily_reports_tool_rejects_invalid_dates(db, args, message):
    with pytest.raises(ToolError, match=message):
        execute_tool(db, "get_daily_reports", args)


def test_unknown_tool_is_rejected(db):
    with pytest.raises(ToolError, match="알 수 없는 도구"):
        execute_tool(db, "drop_table", {})


def test_user_message_carries_current_time_in_kst():
    msg = user_message("어제 불량은?", now=datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc))
    assert "2026-09-28T15:00:00+09:00" in msg and "어제 불량은?" in msg


# ---------- /query 엔드포인트 ----------

@pytest.fixture()
def no_keys(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "NLQ_PROVIDER", "OPENAI_COMPAT_BASE_URL", "OPENAI_COMPAT_MODEL",
                "NLQ_ALLOWED_PROVIDERS"):
        monkeypatch.delenv(key, raising=False)


def test_query_is_disabled_without_api_keys(client, no_keys):
    res = client.post("/query", json={"question": "오늘 OEE는?"})
    assert res.status_code == 503
    assert "API_KEY" in res.json()["detail"]


def test_provider_selection_follows_available_key(no_keys, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert isinstance(main.get_nlq_provider(None), GeminiProvider)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert isinstance(main.get_nlq_provider(None), ClaudeProvider)      # 둘 다 있으면 claude
    assert isinstance(main.get_nlq_provider("gemini"), GeminiProvider)  # 요청이 우선


def test_openai_compat_provider_needs_base_url_and_model(no_keys, monkeypatch):
    from fastapi import HTTPException

    from nlq_providers import OpenAICompatProvider
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://host.docker.internal:11434/v1")
    with pytest.raises(HTTPException) as e:
        main.get_nlq_provider("openai_compat")  # 모델 없으면 비활성
    assert e.value.status_code == 503
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "qwen3:4b")
    provider = main.get_nlq_provider(None)  # 키 없이 base_url·model만으로 설정된 제공자
    assert isinstance(provider, OpenAICompatProvider) and provider.model == "qwen3:4b"


def test_query_returns_answer_with_tool_calls(client, monkeypatch):
    class FakeProvider:
        name = "fake"

        def run(self, user_text, run_tool, max_rounds):
            ok1, data = run_tool("list_equipment", {})
            ok2, err = run_tool("get_oee", {"equipment_ids": ["EQ-999"], **RANGE})
            calls = [
                {"name": "list_equipment", "input": {}, "ok": ok1, "result": data, "error": None},
                {"name": "get_oee", "input": {}, "ok": ok2, "result": None, "error": err},
            ]
            return NLQResult("fake", "fake-model", f"설비 {len(data['equipment'])}대", "answer", calls)

    monkeypatch.setattr(main, "get_nlq_provider", lambda requested: FakeProvider())
    body = client.post("/query", json={"question": "설비 몇 대?"}).json()
    assert body["answer"].startswith("설비")
    assert [c["ok"] for c in body["tool_calls"]] == [True, False]
    assert "등록되지 않은 설비 ID" in body["tool_calls"][1]["error"]


def test_query_validates_question(client):
    assert client.post("/query", json={"question": ""}).status_code == 422
    assert client.post("/query", json={"question": "x", "provider": "gpt"}).status_code == 422


# ---------- 공개 서버용 남용 방지 (하루 상한, 허용 제공자) ----------

class EchoProvider:
    name = "fake"

    def run(self, user_text, run_tool, max_rounds):
        return NLQResult("fake", "fake-model", "ok", "answer", [])


def test_daily_quota_counts_per_kst_day():
    quota = DailyQuota(2)
    t = datetime(2026, 9, 28, 14, 59, tzinfo=timezone.utc)  # KST 23:59
    assert quota.try_acquire(t) and quota.try_acquire(t)
    assert not quota.try_acquire(t)
    status = quota.status(t)
    assert (status["used"], status["remaining"]) == (2, 0)
    assert status["resets_at"] == datetime(2026, 9, 29, 0, 0, tzinfo=KST)
    assert quota.try_acquire(t + timedelta(minutes=1))  # KST 자정이 지나면 초기화
    assert DailyQuota(0).status(t)["limit"] is None


def test_query_returns_429_after_daily_limit(client, monkeypatch):
    monkeypatch.setattr(main, "get_nlq_provider", lambda requested: EchoProvider())
    monkeypatch.setattr(main, "NLQ_QUOTA", DailyQuota(1))
    assert client.post("/query", json={"question": "설비 목록"}).status_code == 200
    res = client.post("/query", json={"question": "설비 목록"})
    assert res.status_code == 429 and "1회" in res.json()["detail"]
    assert client.get("/query/quota").json() | {"resets_at": None} == {"limit": 1, "used": 1, "remaining": 0, "resets_at": None}


def test_quota_is_not_used_when_provider_unavailable(client, no_keys, monkeypatch):
    monkeypatch.setattr(main, "NLQ_QUOTA", DailyQuota(1))
    assert client.post("/query", json={"question": "설비 목록"}).status_code == 503
    assert client.get("/query/quota").json()["used"] == 0


def test_allowed_providers_rejects_others(no_keys, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("NLQ_ALLOWED_PROVIDERS", "gemini")
    monkeypatch.setenv("NLQ_PROVIDER", "gemini")
    assert isinstance(main.get_nlq_provider(None), GeminiProvider)
    with pytest.raises(HTTPException) as e:
        main.get_nlq_provider("claude")  # 방문자가 요청 본문으로 다른 제공자를 고를 수 없게
    assert e.value.status_code == 403


def test_chat_page_is_served(client):
    res = client.get("/chat")
    assert res.status_code == 200 and "/query" in res.text and "__GRAFANA_URL__" not in res.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/chat"


# ---------- Claude 어댑터 ----------

def _block(**kw):
    return SimpleNamespace(**kw)


class FakeAnthropic:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def _claude_resp(stop_reason, *content):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), model="claude-opus-5")


def test_claude_loop_executes_tools_and_returns_answer():
    fake = FakeAnthropic([
        _claude_resp("tool_use", _block(type="text", text="조회합니다"),
                     _block(type="tool_use", id="tu_1", name="get_oee", input={"equipment_ids": [], **RANGE})),
        _claude_resp("end_turn", _block(type="text", text="OEE는 0.88입니다.")),
    ])
    executed = []
    result = ClaudeProvider("k", "claude-opus-5", 5, client=fake).run(
        "질문", lambda name, args: (executed.append(name) or True, {"oee": 0.88}), max_rounds=3)

    assert result.answer == "OEE는 0.88입니다." and result.stop == "answer"
    assert executed == ["get_oee"] and result.tool_calls[0]["ok"] is True
    first = fake.calls[0]
    assert first["model"] == "claude-opus-5" and first["fallbacks"] == "default"
    assert first["betas"] == ["server-side-fallback-2026-07-01"]
    assert all(t["strict"] for t in first["tools"]) and "tool_choice" not in first
    tool_result = fake.calls[1]["messages"][2]["content"][0]
    assert tool_result["tool_use_id"] == "tu_1" and tool_result["is_error"] is False


def test_claude_loop_stops_calling_tools_after_max_rounds():
    tool_use = _claude_resp("tool_use", _block(type="tool_use", id="t", name="list_equipment", input={}))
    fake = FakeAnthropic([tool_use, tool_use, _claude_resp("end_turn", _block(type="text", text="요약"))])
    result = ClaudeProvider("k", "m", 5, client=fake).run("질문", lambda n, a: (False, "오류"), max_rounds=2)
    assert len(fake.calls) == 3
    assert fake.calls[-1]["tool_choice"] == {"type": "none"}
    assert result.stop == "max_rounds" and len(result.tool_calls) == 2
    assert fake.calls[1]["messages"][2]["content"][0]["is_error"] is True


def test_claude_refusal_is_reported():
    fake = FakeAnthropic([_claude_resp("refusal")])
    result = ClaudeProvider("k", "m", 5, client=fake).run("질문", lambda n, a: (True, {}), max_rounds=3)
    assert result.stop == "refusal" and "거절" in result.answer


# ---------- Gemini 어댑터 ----------

class FakeGemini:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        return self.responses.pop(0)


def _gemini_resp(function_calls=None, text=None, finish="STOP"):
    from google.genai import types
    content = types.Content(role="model", parts=[types.Part.from_text(text=text or "...")])
    return SimpleNamespace(function_calls=function_calls, text=text, model_version="gemini-test",
                           candidates=[SimpleNamespace(content=content, finish_reason=finish)])


def test_gemini_loop_sends_function_responses_as_user_role():
    fake = FakeGemini([
        _gemini_resp(function_calls=[SimpleNamespace(name="get_anomalies", args={"equipment_ids": ["EQ-001"], **RANGE})]),
        _gemini_resp(text="이상 3건입니다."),
    ])
    result = GeminiProvider("k", "gemini-flash-latest", 5, client=fake).run(
        "질문", lambda n, a: (True, {"anomaly_count": 3}), max_rounds=3)

    assert result.answer == "이상 3건입니다." and result.stop == "answer"
    assert result.tool_calls[0]["input"]["equipment_ids"] == ["EQ-001"]
    followup = fake.calls[1]["contents"]
    assert followup[-1].role == "user"  # role="tool"은 실제 API가 400으로 거부
    assert followup[-1].parts[0].function_response.name == "get_anomalies"
    assert followup[-1].parts[0].function_response.response == {"result": {"anomaly_count": 3}}
    assert fake.calls[0]["config"].automatic_function_calling.disable is True


def test_gemini_final_round_disables_function_calling():
    call = _gemini_resp(function_calls=[SimpleNamespace(name="list_equipment", args={})])
    fake = FakeGemini([call, _gemini_resp(text="요약")])
    result = GeminiProvider("k", "m", 5, client=fake).run("질문", lambda n, a: (True, {}), max_rounds=1)
    assert fake.calls[-1]["config"].tool_config.function_calling_config.mode == "NONE"
    assert result.stop == "max_rounds"


def test_gemini_blocked_response_is_reported_as_refusal():
    fake = FakeGemini([_gemini_resp(text=None, finish="SAFETY")])
    result = GeminiProvider("k", "m", 5, client=fake).run("질문", lambda n, a: (True, {}), max_rounds=3)
    assert result.stop == "refusal"


# ---------- OpenAI 호환 어댑터 (로컬 Ollama 등) ----------

class FakeSession:
    def __init__(self, responses, status=200):
        self.responses = list(responses)
        self.status = status
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append({"url": url, "body": json, "headers": headers, "messages": list(json["messages"])})
        return SimpleNamespace(status_code=self.status, json=lambda: self.responses.pop(0))


def _oa_resp(content=None, tool_calls=None, finish="stop"):
    return {"model": "qwen3:4b", "choices": [{"finish_reason": finish, "message": {
        "role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}}]}


def _oa_call(name, arguments, id_="call_1"):
    import json as _json
    return {"id": id_, "type": "function",
            "function": {"name": name, "arguments": arguments if isinstance(arguments, str) else _json.dumps(arguments)}}


def test_openai_compat_loop_executes_tools_and_strips_thinking():
    from nlq_providers import OpenAICompatProvider
    session = FakeSession([
        _oa_resp(tool_calls=[_oa_call("get_oee", {"equipment_ids": ["EQ-001"], **RANGE})], finish="tool_calls"),
        _oa_resp(content="<think>기간을 계산하면...</think>\n\nEQ-001 OEE는 0.88입니다."),
    ])
    result = OpenAICompatProvider("http://ollama:11434/v1/", "qwen3:4b", 5, session=session).run(
        "질문", lambda n, a: (True, {"oee": 0.88}), max_rounds=3)

    assert result.answer == "EQ-001 OEE는 0.88입니다." and result.stop == "answer" and result.model == "qwen3:4b"
    assert result.tool_calls[0]["input"]["equipment_ids"] == ["EQ-001"]
    first = session.calls[0]
    assert first["url"] == "http://ollama:11434/v1/chat/completions" and first["headers"] == {}
    assert first["body"]["tool_choice"] == "auto" and first["messages"][0]["role"] == "system"
    tool_msg = session.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1" and '"oee": 0.88' in tool_msg["content"]


def test_openai_compat_invalid_arguments_are_returned_as_tool_error():
    from nlq_providers import OpenAICompatProvider
    session = FakeSession([
        _oa_resp(tool_calls=[_oa_call("get_oee", "{not json")], finish="tool_calls"),
        _oa_resp(content="인자를 고칠 수 없습니다."),
    ])
    executed = []
    result = OpenAICompatProvider("http://x/v1", "m", 5, api_key="k", session=session).run(
        "질문", lambda n, a: (executed.append(n) or True, {}), max_rounds=3)
    assert executed == [] and result.tool_calls[0]["ok"] is False
    assert session.calls[0]["headers"] == {"Authorization": "Bearer k"}
    assert "error" in session.calls[1]["messages"][-1]["content"]


def test_openai_compat_final_round_disables_tools_and_http_error_is_reported():
    from nlq_providers import NLQProviderError, OpenAICompatProvider
    call = _oa_resp(tool_calls=[_oa_call("list_equipment", {})], finish="tool_calls")
    session = FakeSession([call, _oa_resp(content="요약")])
    result = OpenAICompatProvider("http://x/v1", "m", 5, session=session).run("질문", lambda n, a: (True, {}), max_rounds=1)
    assert session.calls[-1]["body"]["tool_choice"] == "none" and result.stop == "max_rounds"

    with pytest.raises(NLQProviderError, match="HTTP 500"):
        OpenAICompatProvider("http://x/v1", "m", 5, session=FakeSession([{}], status=500)).run(
            "질문", lambda n, a: (True, {}), max_rounds=1)


# ---------- 제공자 한도 초과 (429) ----------

def _quota_error(quota_id, retry="37s"):
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry},
    ]}}


@pytest.mark.parametrize("quota_id, expected", [
    ("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "분당 요청 수). 37s 뒤에"),
    ("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "하루 요청 수"),
    ("Unknown", "(요청 수). 37s 뒤에"),
])
def test_gemini_quota_message_tells_which_limit(quota_id, expected):
    assert expected in gemini_quota_message(_quota_error(quota_id))
    assert "잠시 뒤" in gemini_quota_message(None)


def test_gemini_429_becomes_provider_error_with_status():
    from google.genai import errors

    class RateLimited:
        models = SimpleNamespace(generate_content=lambda **kw: (_ for _ in ()).throw(
            errors.APIError(429, _quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))))

    with pytest.raises(NLQProviderError) as e:
        GeminiProvider("k", "m", 5, client=RateLimited()).run("질문", lambda n, a: (True, {}), max_rounds=1)
    assert e.value.status == 429 and "분당" in str(e.value)


def test_query_returns_429_when_provider_rate_limited(client, monkeypatch):
    class Limited:
        name = "fake"

        def run(self, user_text, run_tool, max_rounds):
            raise NLQProviderError("Gemini 요청 한도 초과 (분당 요청 수). 37s 뒤에 다시 시도해 주세요", 429)

    monkeypatch.setattr(main, "get_nlq_provider", lambda requested: Limited())
    res = client.post("/query", json={"question": "설비 목록"})
    assert res.status_code == 429 and "분당" in res.json()["detail"]
