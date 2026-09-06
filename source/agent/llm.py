"""Независимый от модели контракт для языковой модели.

Бизнес-логика знает только этот контракт. Конкретные HTTP-форматы Ollama,
облачных API или другой локальной модели остаются в отдельных адаптерах.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class LLMProvider(Protocol):
    """Минимум возможностей LLM, которые нужны агенту сегодня."""

    def extract_items(self, records: list[dict[str, Any]], rag_context: str = "") -> list[dict[str, Any]]:
        """Извлечь нормализованные позиции из неструктурных строк ТЗ."""

    def clarification_question(self, unresolved: list[dict[str, Any]], fallback: str) -> str:
        """Коротко сформулировать вопрос, не принимая ценового решения."""


def create_llm_provider(settings: dict[str, Any], logger: Callable[[str], None] = print) -> LLMProvider:
    """Создать адаптер LLM из конфигурации, не затрагивая расчётный код."""
    provider = str(settings.get("provider", "ollama")).strip().lower()
    if provider == "ollama":
        from .ollama_provider import OllamaProvider

        return OllamaProvider(settings, logger=logger)
    if provider == "claude":
        from .claude_provider import ClaudeProvider

        return ClaudeProvider(settings, logger=logger)
    if provider in {"deepseek", "qwen"}:
        from .openai_compatible_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider(settings, logger=logger)
    raise ValueError(f"Неподдерживаемый LLM-провайдер: {provider}")
