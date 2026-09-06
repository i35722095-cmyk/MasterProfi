from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


# Зарезервированные имена устройств Windows: недопустимы как имя файла/папки
# целиком, независимо от регистра или расширения.
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{digit}" for digit in range(1, 10)),
    *(f"LPT{digit}" for digit in range(1, 10)),
}


def slug(value: str, limit: int = 70) -> str:
    """Create a readable, filesystem-safe Russian/Latin name."""
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "-", value).strip("-")
    result = cleaned[:limit].rstrip("-") or "ТЗ"
    if result.upper() in _WINDOWS_RESERVED_NAMES:
        result += "-ТЗ"
    return result


def job_folder_name(source: Path, created_at: datetime, short_id: str) -> str:
    return f"{created_at.strftime('%Y-%m-%d_%H-%M')}_{slug(source.stem)}_{short_id}"


def quote_filename(source: Path) -> str:
    return f"КП_{slug(source.stem)}.pdf"
