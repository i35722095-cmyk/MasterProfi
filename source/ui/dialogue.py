from __future__ import annotations

import sys
from dataclasses import asdict
from typing import Any, Callable

from ..agent.llm import LLMProvider
from ..core.models import QuoteItem
from .terminal_menu import choose_option


def _is_angular_amg(item: QuoteItem) -> bool:
    return item.raw.get("variant") == "angular_unverified" and item.system == "AMG"


def _angular_question(item: QuoteItem) -> str:
    width_mm = round(float(item.width_m or 0) * 1000)
    height_mm = round(float(item.height_m or 0) * 1000)
    return (
        f"Как рассчитать угловую штору AMG {width_mm}×{height_mm} мм, "
        "если отдельного правила для неё нет в локальном прайсе?"
    )


def build_options(unresolved: list[QuoteItem]) -> list[dict[str, str]]:
    """Return only business-approved choices; never accept arbitrary price rules."""
    if len(unresolved) == 1 and _is_angular_amg(unresolved[0]):
        width_mm = round(float(unresolved[0].width_m or 0) * 1000)
        return [
            {
                "key": "angular_regular_amg",
                "label": f"Рассчитать как обычную AMG по максимальной ширине {width_mm} мм.",
            },
            {
                "key": "defer",
                "label": "Не рассчитывать: нужно отдельное правило для угловой шторы.",
            },
        ]
    return [{"key": "defer", "label": "Не рассчитывать: требуется подтверждённое правило специалиста."}]


def ask_user(
    unresolved: list[QuoteItem],
    llm: LLMProvider,
    logger: Callable[[str], None],
) -> dict[str, Any] | None:
    """Conduct one terminal clarification without letting the model invent a price rule."""
    if not sys.stdin.isatty():
        logger("Диалог с пользователем пропущен: агент запущен без интерактивного Терминала.")
        return None

    fallback = _angular_question(unresolved[0]) if len(unresolved) == 1 and _is_angular_amg(unresolved[0]) else (
        "Какое подтверждённое правило нужно применить к указанным позициям?"
    )
    options = build_options(unresolved)
    if len(options) == 1 and options[0]["key"] == "defer":
        logger("Нет безопасного варианта выбора; формирую отчёт для специалиста без запроса к модели.")
        return None
    question = llm.clarification_question([asdict(item) for item in unresolved], fallback)
    print("\n--- Уточнение от модели ---", flush=True)
    print(question, flush=True)
    selected_index = choose_option([option["label"] for option in options])
    if selected_index is None:
        logger("Пользователь отменил выбор; оставляю ТЗ на уточнении.")
        return {"question": question, "options": options, "selected": None, "resolved_count": 0}
    choice = options[selected_index]
    resolved_count = apply_supported_choice(unresolved, choice["key"])
    logger(f"Выбран вариант {selected_index + 1}: {choice['label']}")
    return {"question": question, "options": options, "selected": choice, "resolved_count": resolved_count}


def apply_supported_choice(unresolved: list[QuoteItem], choice_key: str) -> int:
    """Apply only a predetermined, auditable rule explicitly selected by the user."""
    if choice_key != "angular_regular_amg":
        return 0
    resolved = 0
    for item in unresolved:
        if _is_angular_amg(item):
            item.raw["variant"] = "angular_as_regular_amg"
            item.note = "По подтверждению пользователя: рассчитано как обычная AMG по максимальной ширине"
            resolved += 1
    return resolved
