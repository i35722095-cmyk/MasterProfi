from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9–3.10
    import tomli as tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE_DIR = ROOT / "source"
CONFIG_DIR = ROOT / "config"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
DATA_DIR = ROOT / "Локальные данные"
EXAMPLES_DIR = ROOT / "ПримерыТЗ"
REFERENCE_QUOTES_DIR = ROOT / "ПримерыКП"
KNOWLEDGE_DIR = SOURCE_DIR / "knowledge"
TASKS_DIR = ROOT / ".agent_data" / "tasks"
KNOWLEDGE_DB = KNOWLEDGE_DIR / "knowledge.sqlite3"
COMPETITORS_CONFIG = CONFIG_DIR / "competitors.toml"
COMPETITOR_SOURCES = CONFIG_DIR / "competitor_sources.toml"


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def load_agent_config() -> dict[str, Any]:
    return _read_toml(CONFIG_DIR / "agent.toml")


def load_llm_config(provider: str | None = None) -> dict[str, Any]:
    """Load the settings for one LLM provider, flattened for the adapters.

    ``llm.toml`` keeps a top-level ``provider`` default plus one table per
    provider (``[ollama]``, ``[claude]``, ...); pass ``provider`` to override
    the default, e.g. after an interactive menu choice.
    """
    raw = _read_toml(CONFIG_DIR / "llm.toml")
    selected = (provider or str(raw.get("provider", "ollama"))).strip().lower()
    if selected not in raw or not isinstance(raw[selected], dict):
        raise ValueError(f"В llm.toml нет настроек для провайдера '{selected}'.")
    settings = dict(raw[selected])
    settings["provider"] = selected
    return settings


def list_llm_providers() -> list[tuple[str, str]]:
    """Return (provider_key, display_name) pairs for every table in llm.toml."""
    raw = _read_toml(CONFIG_DIR / "llm.toml")
    return [
        (key, str(value.get("display_name", key)))
        for key, value in raw.items()
        if isinstance(value, dict)
    ]
