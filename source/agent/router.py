from __future__ import annotations

from pathlib import Path


SUPPORTED_TZ_SUFFIXES = {".xlsx", ".docx", ".pdf", ".txt"}


def select_skill(path: Path) -> str:
    """Маршрутизация намеренно детерминированная: пока агент решает одну задачу — КП."""
    if path.suffix.lower() not in SUPPORTED_TZ_SUFFIXES:
        raise ValueError(f"Неподдерживаемый формат ТЗ: {path.suffix}")
    return "make_proposal"
