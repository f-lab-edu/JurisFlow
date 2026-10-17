import asyncio
import json
from collections.abc import Sequence

import httpx

from backend.app.core.config import ChatSettings


class LLMError(RuntimeError):
    def __init__(self, message="모델 서버를 사용할 수 없습니다.", status_code=None):
        super().__init__(message)
        self.status_code = status_code


class ContextTooLong(ValueError):
    pass


class VLLMClient:
    def __init__(self, settings: ChatSettings, transport=None):
        self.settings = settings
        self.transport = transport
        self.usage = None
        self.native_tools = settings.tool_mode == "native"

    # 인증 헤더로 vLLM을 호출하고 일시적 오류만 재시도
    async def request(self, method, path, payload=None):
        s = self.settings
        if not s.token.get_secret_value():
            raise LLMError("VLLM_TOKEN 설정이 필요합니다.")
        headers = {"Authorization": f"Bearer {s.token.get_secret_value()}"}
        async with httpx.AsyncClient(
            base_url=s.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=s.timeout,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            for attempt in range(s.retries + 1):
                try:
                    response = await client.request(method, path, json=payload)
                except httpx.TimeoutException, httpx.NetworkError:
                    if attempt == s.retries:
                        raise LLMError() from None
                else:
                    if response.is_success:
                        try:
                            return response.json()
                        except ValueError:
                            raise LLMError(
                                "모델 응답 형식이 올바르지 않습니다."
                            ) from None
                    if (
                        response.status_code not in {408, 429, 500, 502, 503, 504}
                        or attempt == s.retries
                    ):
                        raise LLMError(status_code=response.status_code)
                await asyncio.sleep(0.25 * (2**attempt))

    # 서버의 모델명과 native tool calling 지원 여부 확인
    async def prepare(self):
        models = await self.request("GET", "models")
        if self.settings.model not in {m.get("id") for m in models.get("data", [])}:
            raise LLMError("VLLM_MODEL이 서버의 모델 목록과 일치하지 않습니다.")
        if self.settings.tool_mode != "auto":
            return
        tool = {
            "type": "function",
            "function": {
                "name": "capability_probe",
                "description": "Return ok=true.",
                "parameters": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
            },
        }
        try:
            message = await self.complete(
                [{"role": "user", "content": "Call capability_probe with ok=true."}],
                tools=[tool],
                max_tokens=128,
            )
            calls = message.get("tool_calls", [])
            self.native_tools = (
                len(calls) == 1
                and calls[0]["function"]["name"] == "capability_probe"
                and json.loads(calls[0]["function"]["arguments"]) == {"ok": True}
            )
        except LLMError as exc:
            if exc.status_code not in {400, 422}:
                raise
            self.native_tools = False
        except KeyError, TypeError, IndexError, ValueError:
            self.native_tools = False

    # 입력 한도에 맞춰 오래된 대화부터 제외
    def fit_messages(self, messages, max_tokens, tools=None):
        fitted = [dict(m) for m in messages]
        budget = self.settings.context_tokens - max_tokens - 256
        tool_cost = len(json.dumps(tools, ensure_ascii=False).encode()) if tools else 0

        def cost():
            return tool_cost + sum(
                len(str(m.get("content", "")).encode()) + 32 for m in fitted
            )

        while (
            cost() > budget
            and len(fitted) >= 4
            and fitted[1].get("role") == "user"
            and fitted[2].get("role") == "assistant"
        ):
            del fitted[1:3]
        if cost() > budget:
            raise ContextTooLong(
                "현재 질문과 필수 문맥이 모델 입력 한도를 초과합니다. 질문을 줄여 주세요."
            )
        return fitted

    # 채팅 완성을 요청하고 응답 형식과 사용량 확인
    async def complete(self, messages, *, max_tokens=None, tools=None):
        max_tokens = max_tokens or self.settings.max_tokens
        payload = {
            "model": self.settings.model,
            "messages": self.fit_messages(messages, max_tokens, tools),
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        data = await self.request("POST", "chat/completions", payload)
        usage = data.get("usage")
        if isinstance(usage, dict) and all(
            isinstance(usage.get(k), int)
            for k in ("prompt_tokens", "completion_tokens")
        ):
            self.usage = self.usage or {"inputTokens": 0, "outputTokens": 0}
            self.usage["inputTokens"] += usage["prompt_tokens"]
            self.usage["outputTokens"] += usage["completion_tokens"]
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise LLMError("모델 응답이 출력 한도에서 잘렸습니다.")
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError
            return message
        except KeyError, IndexError, TypeError:
            raise LLMError("모델 응답 형식이 올바르지 않습니다.") from None

    # 모델 응답에서 비어 있지 않은 답변 텍스트 추출
    async def text(self, messages, *, max_tokens=None):
        result = await self.complete(messages, max_tokens=max_tokens)
        content = result.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMError("모델이 빈 답변을 반환했습니다.")
        return content.strip()


# 기존 호출 방식으로 vLLM 답변 생성
async def generate_reply(
    messages: Sequence[dict[str, str]], *, max_tokens: int = 512
) -> str:
    return await VLLMClient(ChatSettings()).text(list(messages), max_tokens=max_tokens)
