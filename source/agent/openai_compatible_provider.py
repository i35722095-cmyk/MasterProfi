"""Адаптер OpenAI-совместимого Chat Completions API (DeepSeek, Qwen через DashScope)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .json_utils import parse_json_content
from .prompt import CLARIFICATION_SYSTEM_PROMPT, EXTRACTION_SYSTEM_PROMPT

_RETRYABLE_ERRORS = (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, json.JSONDecodeError)


class OpenAICompatibleProvider:
    """Облачная модель через Chat Completions API формата OpenAI (DeepSeek, Qwen)."""

    def __init__(self, settings: dict[str, Any], logger: Callable[[str], None] = print) -> None:
        self.settings = settings
        self.log = logger
        self.label = str(settings.get("display_name") or settings.get("model") or "LLM")
        api_key_env = str(settings.get("api_key_env", ""))
        self.api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        if not self.api_key:
            raise ValueError(
                f"Не найден API-ключ для {self.label}: задайте переменную окружения "
                f"{api_key_env or '<не указана в конфиге>'}."
            )

    def extract_items(self, records: list[dict[str, Any]], rag_context: str = "") -> list[dict[str, Any]]:
        if not records:
            return []
        result: list[dict[str, Any]] = []
        batch_size = int(self.settings.get("batch_size", 6))
        total_batches = (len(records) + batch_size - 1) // batch_size
        for start in range(0, len(records), batch_size):
            batch = records[start:start + batch_size]
            self.log(
                f"{self.label} думает: разбираю пачку {start // batch_size + 1}/{total_batches} "
                f"({len(batch)} записей)."
            )
            parsed = self._extract_batch(batch, rag_context)
            if not parsed:
                self.log(f"{self.label} не ответил корректно; использую детерминированный разбор для оставшихся позиций.")
                break
            result.extend(parsed)
        return result

    def clarification_question(self, unresolved: list[dict[str, Any]], fallback: str) -> str:
        prompt = (
            "Сформулируй один короткий и вежливый вопрос менеджеру по позициям ТЗ. "
            "Не придумывай цену, наценку, систему или аналог. Если есть явно предложенные варианты, "
            "сохрани их в вопросе. Верни только текст вопроса на русском.\n\n"
            "Позиции без правила:\n" + json.dumps(unresolved, ensure_ascii=False) + "\n\n"
            "Безопасный вариант вопроса, который можно использовать дословно:\n" + fallback
        )
        payload = {
            "model": self.settings["model"],
            "temperature": 0,
            "max_tokens": 200,
            "messages": [
                {"role": "system", "content": CLARIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        started_at = time.monotonic()
        self.log(f"{self.label} думает: формулирую вопрос для пользователя.")
        try:
            content = self._call(payload).strip()
            elapsed = time.monotonic() - started_at
            if content:
                self.log(f"{self.label} сформулировал вопрос за {elapsed:.1f} с.")
                return content
        except _RETRYABLE_ERRORS as error:
            elapsed = time.monotonic() - started_at
            self.log(f"{self.label} не смог сформулировать вопрос за {elapsed:.1f} с: {error}")
        return fallback

    def _extract_batch(self, records: list[dict[str, Any]], rag_context: str) -> list[dict[str, Any]]:
        prompt = """Извлеки позиции ТЗ для расчёта солнцезащитных систем.
Верни JSON-объект с единственным полем items — массивом позиций. Поля каждой позиции:
source_ref, name, quantity, width_m, height_m, area_m2, system, fabric, color, opacity, split_into.
Обязательны name и quantity. Размер может быть ширина+высота ИЛИ площадь.
Не путай подписи ШхВ и ВхШ. Не выдумывай отсутствующие данные. split_into>1 только при явном указании.
Ответ должен быть только JSON-объектом, без пояснений и без markdown-обрамления.
Входные записи:\n""" + json.dumps(records, ensure_ascii=False)
        if rag_context:
            prompt += "\nКраткие прецеденты. Используй только для понимания структуры, не копируй из них числа:\n" + rag_context
        payload = {
            "model": self.settings["model"],
            "temperature": float(self.settings.get("temperature", 0)),
            "max_tokens": int(self.settings.get("max_tokens", 900)),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        started_at = time.monotonic()
        try:
            content = self._call(payload)
            parsed = parse_json_content(content)
            elapsed = time.monotonic() - started_at
            if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
                self.log(f"{self.label} ответил за {elapsed:.1f} с: получено {len(parsed['items'])} позиций.")
                return parsed["items"]
            if isinstance(parsed, list):
                self.log(f"{self.label} ответил за {elapsed:.1f} с: получено {len(parsed)} позиций.")
                return parsed
            if isinstance(parsed, dict) and parsed.get("source_ref"):
                self.log(f"{self.label} ответил за {elapsed:.1f} с: получена 1 позиция.")
                return [parsed]
            self.log(f"{self.label} ответил за {elapsed:.1f} с, но не вернул пригодных позиций.")
            return []
        except _RETRYABLE_ERRORS as error:
            elapsed = time.monotonic() - started_at
            self.log(f"{self.label} не ответил за {elapsed:.1f} с или вернул ошибку: {error}")
            return []

    def _call(self, payload: dict[str, Any]) -> str:
        request = urllib.request.Request(
            self.settings["base_url"],
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=int(self.settings.get("timeout_seconds", 60))) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
