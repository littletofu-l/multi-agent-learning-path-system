"""OpenAI 兼容的 LLM 客户端。无 key、探针失败或调用异常时返回 None，由各 Agent 走规则降级。"""
import asyncio
import json
import re
import time
from typing import Any

import httpx

from .config import settings

TIMEOUT = httpx.Timeout(settings.llm_timeout, connect=5.0)
RECHECK_AFTER = 300.0  # 熔断后 5 分钟允许重探，避免瞬时网络故障造成永久假熔断


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


class LLMClient:
    def __init__(self) -> None:
        self.enabled = settings.llm_enabled
        self.model = settings.llm_model
        self._ready: bool | None = None
        self._checked_at: float = 0.0
        self._probe_lock: asyncio.Lock | None = None

    @property
    def mode(self) -> str:
        if not self.enabled:
            return "规则引擎（未配置 LLM）"
        if self._ready is False:
            return "规则引擎（LLM 不可用）"
        return f"LLM · {self.model}"

    async def available(self) -> bool:
        """带缓存的可用性判断，首次调用会执行一次探针；熔断后超时可重试。"""
        return await self._ensure_ready()

    async def _ensure_ready(self) -> bool:
        if not self.enabled:
            return False
        if self._ready is True:
            return True
        if self._ready is False and (time.monotonic() - self._checked_at) < RECHECK_AFTER:
            return False
        if self._probe_lock is None:
            self._probe_lock = asyncio.Lock()
        async with self._probe_lock:
            if self._ready is None or (time.monotonic() - self._checked_at) >= RECHECK_AFTER:
                self._ready = await self._probe()
                self._checked_at = time.monotonic()
        return self._ready

    async def _probe(self) -> bool:
        """启动时做一次极小的连通性/鉴权探针，失败即熔断，避免每个 Agent 都卡超时。"""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(
                    f"{settings.llm_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 16,
                    },
                )
                return resp.status_code < 400
        except Exception:
            return False

    async def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 2400,
    ) -> str | None:
        if not await self._ensure_ready():
            return None
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{settings.llm_base_url}/chat/completions"
        for body in ({**payload, "response_format": {"type": "json_object"}}, payload):
            for attempt in (1, 2):  # 网络抖动重试一次
                try:
                    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                        resp = await client.post(url, json=body, headers=headers)
                        if resp.status_code >= 400:
                            break  # 换下一种请求体（如服务商不支持 json_object）
                        return resp.json()["choices"][0]["message"]["content"]
                except Exception:
                    if attempt == 1:
                        await asyncio.sleep(1.0)
                    continue
        return None

    async def chat_json(self, system: str, user: str, **kwargs: Any) -> dict | None:
        text = await self.chat(system, user, **kwargs)
        return _extract_json(text) if text else None


llm = LLMClient()
