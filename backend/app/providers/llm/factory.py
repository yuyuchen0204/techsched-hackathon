from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.providers.llm.base import ComplaintClassification, InterpretedRequest, LLMError, LLMProvider
from app.providers.llm.mock import MockLLMProvider

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def mock_provider() -> MockLLMProvider:
    return MockLLMProvider()


def build_provider() -> LLMProvider:
    s = get_settings()
    if s.llm_mode == "real":
        if s.llm_provider == "openai_compat":
            from app.providers.llm.openai_compat import OpenAICompatLLMProvider
            return OpenAICompatLLMProvider(api_key=s.llm_api_key, model=s.llm_model, base_url=s.llm_base_url,
                                           timeout=s.llm_timeout_seconds)
        if s.llm_provider == "anthropic":
            from app.providers.llm.anthropic_provider import AnthropicLLMProvider
            return AnthropicLLMProvider(api_key=s.llm_api_key, model=s.llm_model or "claude-opus-5",
                                        base_url=s.llm_base_url or None, timeout=s.llm_timeout_seconds)
        raise LLMError(f"unknown LLM_PROVIDER '{s.llm_provider}' (expected anthropic | openai_compat)")
    return mock_provider()


class DegradableLLM:
    """Calls the configured provider; on failure degrades to the mock and records the degradation explicitly."""

    def __init__(self) -> None:
        self.last: dict[str, Any] = {"provider": None, "degraded": False, "reason": None}
        try:
            self.provider: LLMProvider = build_provider()
        except LLMError as exc:
            log.warning("LLM provider unavailable (%s); using mock", exc)
            self.provider = mock_provider()
            self.last = {"provider": "mock", "degraded": True, "reason": str(exc)}

    def _run(self, fn_name: str, *args: Any) -> Any:
        try:
            result = getattr(self.provider, fn_name)(*args)
            self.last = {"provider": self.provider.name, "model": self.provider.model, "degraded": False, "reason": None}
            return result
        except LLMError as exc:
            log.warning("LLM call %s failed (%s); degraded to mock", fn_name, exc)
            self.last = {"provider": "mock", "model": mock_provider().model, "degraded": True, "reason": str(exc)}
            return getattr(mock_provider(), fn_name)(*args)

    def interpret(self, text: str, catalog: list[dict[str, Any]], context: dict[str, Any]) -> InterpretedRequest:
        return self._run("interpret", text, catalog, context)

    def classify_complaint(self, text: str) -> ComplaintClassification:
        return self._run("classify_complaint", text)


@lru_cache(maxsize=1)
def get_llm() -> DegradableLLM:
    return DegradableLLM()
