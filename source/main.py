from __future__ import annotations

import sys
import time
from pathlib import Path

from .agent.router import SUPPORTED_TZ_SUFFIXES, select_skill
from .agent.runtime import log, process_file
from .core.config import INPUT_DIR, OUTPUT_DIR, TASKS_DIR, list_llm_providers, load_agent_config, load_llm_config
from .ui.terminal_menu import choose_option


def select_llm_provider() -> str | None:
    """Let the operator pick a model interactively; fall back to the config default otherwise."""
    providers = list_llm_providers()
    if len(providers) <= 1 or not sys.stdin.isatty():
        return None
    print("Выберите модель для расчёта ТЗ:")
    index = choose_option([display_name for _, display_name in providers])
    if index is None:
        return None
    return providers[index][0]


def main() -> None:
    for directory in (INPUT_DIR, OUTPUT_DIR, TASKS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    agent_config = load_agent_config()
    llm_config = load_llm_config(select_llm_provider())
    log("Агент запущен. Ожидаю XLSX, DOCX или PDF в папке input. Остановка: Ctrl+C.")
    log(f"{llm_config.get('display_name', 'LLM')}: {llm_config['model']}; монтаж и доставка отключены.")
    seen: dict[Path, int] = {}
    try:
        while True:
            files = sorted(path for path in INPUT_DIR.iterdir() if path.suffix.lower() in SUPPORTED_TZ_SUFFIXES and not path.name.startswith("~$"))
            for path in files:
                version = path.stat().st_mtime_ns
                if seen.get(path) == version:
                    continue
                log(f"Сценарий: {select_skill(path)}")
                process_file(path, agent_config, llm_config)
                if path.exists():
                    seen[path] = version
            time.sleep(int(agent_config.get("poll_seconds", 3)))
    except KeyboardInterrupt:
        log("Агент остановлен.")


if __name__ == "__main__":
    main()
