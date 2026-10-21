from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from config import settings

log = logging.getLogger(__name__)


class AIError(RuntimeError):
    pass


def parse_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(raw[start:end + 1])
            if isinstance(value, dict):
                return value
        except Exception as exc:
            raise AIError(f"invalid JSON: {exc}") from exc
    raise AIError("AI returned no JSON object")


class DeepSeek:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.deepseek_base_url,
            timeout=httpx.Timeout(180.0, connect=20.0),
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def json_chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.9,
        max_tokens: int = 8000,
        attempts: int = 2,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.post(
                    "/chat/completions",
                    json={
                        "model": settings.deepseek_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                return parse_json(content)
            except Exception as exc:
                last_error = exc
                log.warning("DeepSeek attempt %s failed: %s", attempt, exc)
        raise AIError(str(last_error) if last_error else "DeepSeek failed")
