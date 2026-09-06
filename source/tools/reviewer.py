from __future__ import annotations

import re
from typing import Any

import pdfplumber

from ..core.models import QuoteItem, ReviewResult


FORBIDDEN_TEMPORARY_TERMS = ("скидка", "доставка", "установка")


def _contains_temporary_service(text: str) -> str | None:
    text = text.lower()
    for term in FORBIDDEN_TEMPORARY_TERMS:
        if term in text:
            return term
    # «Монтажный профиль» — штатная комплектация изделия; запрет относится
    # только к самостоятельной услуге монтажа, которая временно не считается.
    if re.search(r"\bмонтаж(?!н)\w*\b", text):
        return "монтаж"
    return None


def review_quote(
    path,
    priced: list[QuoteItem],
    unresolved: list[QuoteItem],
    invalid: list[QuoteItem],
    config: dict[str, Any],
    manual_items: list[QuoteItem] | None = None,
) -> ReviewResult:
    errors: list[str] = []
    warnings: list[str] = []
    if unresolved:
        errors.append(f"Остались позиции без проверенной цены: {len(unresolved)}")
    if not priced:
        errors.append("Нет ни одной рассчитанной позиции")
    for item in priced:
        if not item.price_source:
            errors.append(f"{item.source_ref}: отсутствует происхождение цены")
        if item.note.startswith("Аналог:") and not item.analogue_source:
            errors.append(f"{item.source_ref}: аналог не имеет подтверждённого источника")
        if not item.has_size or not item.quantity:
            errors.append(f"{item.source_ref}: потерян размер/площадь или количество")
    if invalid:
        warnings.append(f"Исключены позиции без валидных размеров: {len(invalid)}")
    if manual_items:
        warnings.append(f"Требуют ручного расчёта и не включены в итог: {len(manual_items)}")

    product_total = sum(item.line_total for item in priced)
    if path.suffix.lower() != ".pdf" or not path.is_file() or path.stat().st_size == 0:
        errors.append("Результат не является непустым PDF")
        text = ""
        page_count = 0
    else:
        try:
            with pdfplumber.open(path) as document:
                page_count = len(document.pages)
                text = " ".join(page.extract_text() or "" for page in document.pages).lower()
        except Exception as error:
            errors.append(f"PDF не читается: {error}")
            text = ""
            page_count = 0
    if "итого" not in text:
        errors.append("В PDF отсутствует итоговая строка")
    if manual_items and "ручн" not in text:
        errors.append("В PDF отсутствует предупреждение о ручном расчёте")
    digits = re.sub(r"\D", "", text)
    expected_total = f"{product_total:.2f}".replace(".", "")
    if expected_total not in digits:
        errors.append("Итоговая сумма PDF не совпадает с расчётом")
    temporary_service = _contains_temporary_service(text)
    if temporary_service:
        errors.append(f"В КП осталась временно запрещённая позиция: {temporary_service}")
    return ReviewResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        checks={
            "priced_items": len(priced),
            "unresolved_items": len(unresolved),
            "invalid_items": len(invalid),
            "manual_items": len(manual_items or []),
            "product_total": product_total,
            "pdf_pages": page_count,
            "discount_absent": "скидка" not in text,
            "delivery_and_installation_absent": _contains_temporary_service(text) is None,
        },
    )
