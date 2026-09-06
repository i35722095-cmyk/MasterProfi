"""Разбор JSON-ответа облачных моделей, которые не гарантируют чистый JSON."""

from __future__ import annotations

import json
from typing import Any


def parse_json_content(content: str) -> Any:
    """Parse a model reply as JSON, tolerating a ```json fenced block."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)
