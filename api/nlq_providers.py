"""
자연어 질의 LLM 어댑터 (Claude / Gemini)

두 어댑터는 같은 계약을 따른다.
- run(user_text, run_tool, max_rounds) -> NLQResult
- 모델이 도구 호출을 요청하면 run_tool(name, args) -> (ok, payload)로 실행해 결과를 돌려준다.
- 도구 호출 라운드는 최대 max_rounds번. 그 뒤 마지막 요청은 도구 사용을 막고(tool_choice none) 답변만 받는다.

API 키는 환경변수(ANTHROPIC_API_KEY / GEMINI_API_KEY)로만 받는다. 코드나 로그에 키를 남기지 않는다.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, Tuple

from nlq_tools import TOOL_SPECS

KST = timezone(timedelta(hours=9), "KST")  # 한국은 서머타임이 없어 고정 오프셋으로 충분

SYSTEM_PROMPT = """너는 제조 MES(mini-mes)의 데이터 조회 도우미다. 설비 생산실적·가동률(OEE)·불량·이상탐지 데이터를 도구로 조회해 한국어로 답한다.

- 수치와 사실은 도구 결과에서만 가져온다. 도구 결과에 없는 내용은 추측하지 말고 조회할 수 없다고 답한다.
- "오늘", "어제", "최근 N시간" 같은 기간은 사용자 메시지의 현재 시각(Asia/Seoul) 기준으로 start/end를 계산한다. 하루는 00:00부터 다음 날 00:00까지다.
- 설비 이름이나 라인만 언급되면 list_equipment로 설비 ID를 먼저 확인한다.
- 도구가 오류를 돌려주면 오류 내용을 보고 인자를 고쳐 다시 호출하거나, 고칠 수 없으면 이유를 답한다.
- 답변은 짧게: 결론을 먼저 말하고, 조회한 기간과 설비를 함께 밝힌다. 데이터가 시뮬레이터가 만든 가상 데이터라는 점은 사용자가 물을 때만 언급한다."""

RunTool = Callable[[str, dict], Tuple[bool, Any]]


@dataclass
class NLQResult:
    provider: str
    model: str
    answer: str
    stop: str                       # "answer" | "max_rounds" | "max_tokens" | "refusal"
    tool_calls: List[dict] = field(default_factory=list)


class NLQProviderError(Exception):
    """LLM 호출 실패 (네트워크, 인증, 요청 형식 등). 메시지에 키를 넣지 않는다.
    status: 제공자가 돌려준 HTTP 상태 (429면 /query도 429로 - 사용자가 잠시 뒤 다시 시도할 수 있는 오류)"""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def gemini_quota_message(details: Any) -> str:
    """Gemini 429 응답에서 한도 종류(분당/하루)와 재시도 대기 시간을 꺼내 안내 문구로.
    응답 형식: {"error": {"details": [{"violations": [{"quotaId": "...PerDay..."}]}, {"retryDelay": "37s"}]}}"""
    err = details.get("error", details) if isinstance(details, dict) else {}
    kinds, retry = [], None
    for d in err.get("details") or []:
        if not isinstance(d, dict):
            continue
        for v in d.get("violations") or []:
            quota_id = str(v.get("quotaId", ""))
            kind = "하루" if "PerDay" in quota_id else "분당" if "PerMinute" in quota_id else None
            if kind and kind not in kinds:
                kinds.append(kind)
        retry = d.get("retryDelay") or retry
    if "하루" in kinds:
        return "Gemini 무료 한도 초과 (하루 요청 수). 태평양 시간 자정(KST 16~17시)에 초기화됩니다"
    wait = f" {retry} 뒤에" if retry else " 잠시 뒤"
    return f"Gemini 요청 한도 초과 ({'분당 요청 수' if '분당' in kinds else '요청 수'}).{wait} 다시 시도해 주세요"


def provider_status_message(provider: str, status: int) -> str:
    """제공자 HTTP 오류를 방문자용 문구로. 5xx(503 과부하 등)는 제공자 쪽 일시 장애라 다시 시도하면 되는 경우가 많다"""
    if status >= 500:
        return f"{provider} 서버가 일시적으로 혼잡하거나 장애 중입니다 (HTTP {status}). 잠시 뒤 다시 시도해 주세요"
    return f"{provider} API 오류 (HTTP {status})"


def user_message(question: str, now: Optional[datetime] = None) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(KST)
    return f"현재 시각: {now.isoformat(timespec='seconds')} (Asia/Seoul)\n\n질문: {question}"


def _record(calls: List[dict], name: str, args: dict, ok: bool, payload: Any) -> None:
    calls.append({"name": name, "input": args, "ok": ok, "result": payload if ok else None,
                  "error": None if ok else payload})


class ClaudeProvider:
    """Anthropic Messages API tool use (수동 루프)"""

    name = "claude"
    # Opus 5의 안전 분류기가 거절하면 서버가 추천 모델로 같은 요청을 다시 실행 (기본 활성화)
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, api_key: str, model: str, timeout: float, client: Any = None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.client = client
        self.model = model
        self.tools = [
            {"name": s["name"], "description": s["description"], "input_schema": s["parameters"], "strict": True}
            for s in TOOL_SPECS
        ]

    def _create(self, messages: list, final_round: bool):
        import anthropic
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=self.tools,
            messages=messages,
            betas=[self.FALLBACK_BETA],
            fallbacks="default",
        )
        if final_round:
            kwargs["tool_choice"] = {"type": "none"}
        try:
            return self.client.beta.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            raise NLQProviderError(provider_status_message("Claude", e.status_code), e.status_code) from e
        except anthropic.APIConnectionError as e:
            raise NLQProviderError("Claude API 연결 실패") from e

    def run(self, user_text: str, run_tool: RunTool, max_rounds: int) -> NLQResult:
        messages: list = [{"role": "user", "content": user_text}]
        calls: List[dict] = []
        for round_no in range(max_rounds + 1):
            final_round = round_no == max_rounds
            resp = self._create(messages, final_round)
            if resp.stop_reason == "refusal":
                return NLQResult(self.name, resp.model, "모델이 이 질문에 대한 답변을 거절했습니다.", "refusal", calls)
            if resp.stop_reason == "tool_use" and not final_round:
                messages.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if block.type != "tool_use":
                        continue
                    ok, payload = run_tool(block.name, block.input)
                    _record(calls, block.name, block.input, ok, payload)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(payload if ok else {"error": payload}, ensure_ascii=False),
                        "is_error": not ok,
                    })
                messages.append({"role": "user", "content": results})  # 병렬 호출 결과는 한 메시지로
                continue
            answer = "".join(b.text for b in resp.content if b.type == "text").strip()
            if resp.stop_reason == "max_tokens":
                stop = "max_tokens"
            elif final_round and calls:
                stop = "max_rounds"
            else:
                stop = "answer"
            return NLQResult(self.name, resp.model, answer, stop, calls)
        raise AssertionError("unreachable")


class GeminiProvider:
    """google-genai function calling (자동 함수 호출을 끄고 수동 루프)"""

    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float, client: Any = None):
        from google.genai import types
        if client is None:
            from google import genai
            # Claude SDK 기본값(재시도 2회)과 맞춰 408/429/5xx는 최대 3번 시도 (Gemini 일시 과부하 503 대응)
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
                timeout=int(timeout * 1000), retry_options=types.HttpRetryOptions(attempts=3),
            ))
        self.client = client
        self.model = model
        self.types = types
        declarations = [
            types.FunctionDeclaration(name=s["name"], description=s["description"], parameters_json_schema=s["parameters"])
            for s in TOOL_SPECS
        ]
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        self.final_config = self.config.model_copy(update={
            "tool_config": types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="NONE")),
        })

    def _generate(self, contents: list, final_round: bool):
        from google.genai import errors
        try:
            return self.client.models.generate_content(
                model=self.model, contents=contents, config=self.final_config if final_round else self.config,
            )
        except errors.APIError as e:
            if e.code == 429:
                raise NLQProviderError(gemini_quota_message(e.details), 429) from e
            raise NLQProviderError(provider_status_message("Gemini", e.code), e.code) from e
        except OSError as e:
            raise NLQProviderError("Gemini API 연결 실패") from e

    def run(self, user_text: str, run_tool: RunTool, max_rounds: int) -> NLQResult:
        types = self.types
        contents: list = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]
        calls: List[dict] = []
        for round_no in range(max_rounds + 1):
            final_round = round_no == max_rounds
            resp = self._generate(contents, final_round)
            model = getattr(resp, "model_version", None) or self.model
            function_calls = resp.function_calls or []
            if function_calls and not final_round:
                contents.append(resp.candidates[0].content)
                parts = []
                for fc in function_calls:
                    args = dict(fc.args or {})
                    ok, payload = run_tool(fc.name, args)
                    _record(calls, fc.name, args, ok, payload)
                    parts.append(types.Part.from_function_response(
                        name=fc.name, response={"result": payload} if ok else {"error": payload},
                    ))
                # SDK README 예제는 role="tool"이지만 Gemini Developer API는 400(Role 'tool' is not supported)을
                # 돌려준다 - 실제 API가 허용하는 "user"로 함수 결과를 보낸다 (2026-09-28 gemini-flash-latest로 확인)
                contents.append(types.Content(role="user", parts=parts))
                continue
            candidate = resp.candidates[0] if resp.candidates else None
            finish = str(getattr(candidate, "finish_reason", "") or "")
            answer = (resp.text or "").strip() if candidate else ""
            if not answer and (candidate is None or "SAFETY" in finish or "PROHIBITED" in finish or "BLOCK" in finish):
                return NLQResult(self.name, model, "모델이 이 질문에 대한 답변을 거절했습니다.", "refusal", calls)
            if "MAX_TOKENS" in finish:
                stop = "max_tokens"
            elif final_round and calls:
                stop = "max_rounds"
            else:
                stop = "answer"
            return NLQResult(self.name, model, answer, stop, calls)
        raise AssertionError("unreachable")


class OpenAICompatProvider:
    """OpenAI Chat Completions 호환 API의 tools (수동 루프)

    같은 형식을 쓰는 로컬 Ollama(`http://<host>:11434/v1`), OpenAI, Groq, OpenRouter 등을
    base_url·model 설정만 바꿔 쓴다. SDK 없이 requests로 호출한다 (의존성 추가 없음).
    """

    name = "openai_compat"
    # 추론 모델(Qwen3 등)이 답변 앞에 붙이는 생각 과정은 답변에서 뺀다
    _THINK = re.compile(r"<think>.*?</think>", re.DOTALL)

    def __init__(self, base_url: str, model: str, timeout: float, api_key: str = "", session: Any = None):
        import requests
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout = timeout
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.session = session or requests.Session()
        self.tools = [
            {"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": s["parameters"]}}
            for s in TOOL_SPECS
        ]

    def _create(self, messages: list, final_round: bool) -> dict:
        import requests
        body = {"model": self.model, "messages": messages, "tools": self.tools,
                "tool_choice": "none" if final_round else "auto"}
        try:
            resp = self.session.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise NLQProviderError("OpenAI 호환 API 연결 실패") from e
        if resp.status_code >= 400:
            raise NLQProviderError(provider_status_message("OpenAI 호환", resp.status_code), resp.status_code)
        return resp.json()

    def run(self, user_text: str, run_tool: RunTool, max_rounds: int) -> NLQResult:
        messages: list = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}]
        calls: List[dict] = []
        for round_no in range(max_rounds + 1):
            final_round = round_no == max_rounds
            data = self._create(messages, final_round)
            model = data.get("model") or self.model
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            finish = choice.get("finish_reason")
            tool_calls = message.get("tool_calls") or []
            if tool_calls and not final_round:
                messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = None
                    if isinstance(args, dict):
                        ok, payload = run_tool(name, args)
                    else:
                        args, ok, payload = {}, False, "인자가 올바른 JSON 객체가 아닙니다"
                    _record(calls, name, args, ok, payload)
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                     "content": json.dumps(payload if ok else {"error": payload}, ensure_ascii=False)})
                continue
            if message.get("refusal"):
                return NLQResult(self.name, model, "모델이 이 질문에 대한 답변을 거절했습니다.", "refusal", calls)
            answer = self._THINK.sub("", message.get("content") or "").strip()
            if finish == "length":
                stop = "max_tokens"
            elif final_round and calls:
                stop = "max_rounds"
            else:
                stop = "answer"
            return NLQResult(self.name, model, answer, stop, calls)
        raise AssertionError("unreachable")
