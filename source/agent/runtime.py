from __future__ import annotations

import json
import shutil
import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.config import INPUT_DIR, OUTPUT_DIR, TASKS_DIR
from ..core.naming import job_folder_name
from ..memory.knowledge import KnowledgeBase, initialize_knowledge
from ..tools.pdf import create_quote_pdf
from ..tools.pricing import price_items
from ..tools.reviewer import review_quote
from ..tools.tz_parser import parse_tz
from ..ui.dialogue import ask_user
from .llm import create_llm_provider


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


class TaskContext:
    def __init__(self, source: Path) -> None:
        created_at = datetime.now()
        self.path = TASKS_DIR / job_folder_name(source, created_at, uuid.uuid4().hex[:6])
        self.path.mkdir(parents=True, exist_ok=False)
        self.output_dir = OUTPUT_DIR / self.path.name
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.save("task.json", {"source": source.name, "created_at": created_at.isoformat()})

    def save(self, name: str, payload: Any) -> None:
        (self.path / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _review_report_paths(context: TaskContext) -> tuple[Path, Path]:
    return context.output_dir / "Требуется_уточнение.txt", context.output_dir / "Требуется_уточнение.json"


def _manual_report_paths(context: TaskContext) -> tuple[Path, Path]:
    return context.output_dir / "Ручной_расчет.txt", context.output_dir / "Ручной_расчет.json"


def _item_size_for_report(item: Any) -> str:
    if item.raw.get("variant") == "angular_unverified":
        raw_text = str(item.raw.get("raw_text", ""))
        upper = raw_text.split("(верхний край)")[0].split()[-1] if "(верхний край)" in raw_text else "—"
        lower = raw_text.split("(нижний край)")[0].split()[-1] if "(нижний край)" in raw_text else "—"
        return f"верх {upper} мм, низ {lower} мм, высота {round(float(item.height_m or 0) * 1000)} мм"
    if item.area_m2 is not None and not (item.width_m and item.height_m):
        return f"площадь {item.area_m2:g} м²"
    if item.width_m and item.height_m:
        return f"{round(item.width_m * 1000)} × {round(item.height_m * 1000)} мм"
    return "размер не указан"


def _required_action(item: Any) -> str:
    if item.raw.get("variant") == "angular_unverified":
        return (
            "Укажите правило расчёта: считать как обычную AMG по максимальной ширине "
            "720 мм или применять отдельную систему/наценку."
        )
    if "ценовая категория" in item.note:
        return "Укажите проверенный аналог из нашего прайса и его ценовую категорию."
    if "система" in item.note:
        return "Укажите точную систему изделия из нашего прайса."
    if "сетка" in item.note:
        return "Проверьте размеры или укажите систему, допускающую такой габарит."
    return "Добавьте или подтвердите правило расчёта для этой позиции."


def requires_review_text(source: Path, priced: list[Any], unresolved: list[Any], invalid: list[Any], dialogue: dict[str, Any] | None = None) -> str:
    lines = [
        "КП НЕ СФОРМИРОВАНО: ТРЕБУЕТСЯ УТОЧНЕНИЕ",
        "",
        f"ТЗ: {source.name}",
        f"Рассчитано позиций: {len(priced)}.",
        f"Требуют уточнения: {len(unresolved)}.",
        f"Исключено без размеров: {len(invalid)}.",
        "",
    ]
    if not priced and not unresolved and not invalid:
        lines.extend([
            "ПРИЧИНА",
            "В документе не удалось распознать ни одной позиции для расчёта.",
            "Проверьте, что позиции имеют наименование, количество и размеры.",
            "",
        ])
    if len(unresolved) > 12:
        lines.append("ГРУППЫ ПОЗИЦИЙ, ТРЕБУЮЩИЕ РЕШЕНИЯ")
        groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for item in unresolved:
            groups[(item.name, item.note)].append(item)
        for index, ((name, note), group) in enumerate(groups.items(), 1):
            materials = list(dict.fromkeys(item.fabric for item in group if item.fabric))
            sizes = list(dict.fromkeys(_item_size_for_report(item) for item in group))
            references = [item.source_ref for item in group]
            material_text = ", ".join(materials[:4]) or "не указаны"
            if len(materials) > 4:
                material_text += f" и ещё {len(materials) - 4}"
            size_text = ", ".join(sizes[:3]) or "не указаны"
            if len(sizes) > 3:
                size_text += f" и ещё {len(sizes) - 3}"
            reference_text = ", ".join(references[:8])
            if len(references) > 8:
                reference_text += f" и ещё {len(references) - 8}"
            lines.extend([
                "",
                f"{index}. {name}",
                f"   Строк ТЗ: {len(group)}; всего изделий: {sum(item.quantity for item in group)} шт.",
                f"   Материалы: {material_text}.",
                f"   Примеры размеров: {size_text}.",
                f"   Строки источника: {reference_text}.",
                f"   Причина: {note}.",
                f"   Что нужно сделать: {_required_action(group[0])}",
            ])
        lines.extend(["", "Полный перечень строк и размеров находится в файле Требуется_уточнение.json."])
    elif unresolved:
        lines.append("ПОЗИЦИИ, ТРЕБУЮЩИЕ РЕШЕНИЯ")
        for index, item in enumerate(unresolved, 1):
            lines.extend([
                "",
                f"{index}. {item.name}",
                f"   Количество: {item.quantity} шт.",
                f"   Размер: {_item_size_for_report(item)}.",
                f"   Комплектация: {item.system or 'не определена'}; {item.fabric or 'ткань не определена'}; {item.color or 'цвет не указан'}.",
                f"   Причина: {item.note}.",
                f"   Что нужно сделать: {_required_action(item)}",
            ])
    if invalid:
        lines.extend(["", "ИСКЛЮЧЕННЫЕ ПОЗИЦИИ"])
        for item in invalid:
            lines.append(f"- {item.name}: {item.note}.")
    if dialogue and dialogue.get("selected"):
        lines.extend(["", "ВЫБОР ПОЛЬЗОВАТЕЛЯ", str(dialogue["selected"].get("label", ""))])
    lines.extend([
        "",
        "После уточнения перезапустите агента. Исходное ТЗ остаётся в папке input.",
    ])
    return "\n".join(lines) + "\n"


def manual_calculation_text(source: Path, priced: list[Any], manual_items: list[Any]) -> str:
    lines = [
        "КП СФОРМИРОВАНО ЧАСТИЧНО: НУЖНА РУЧНАЯ КОРРЕКТИРОВКА",
        "",
        f"ТЗ: {source.name}",
        f"Автоматически рассчитано позиций: {len(priced)}.",
        f"Требуют ручного расчёта: {len(manual_items)}.",
        "",
        "ВНИМАНИЕ: перечисленные ниже позиции не включены в сумму и таблицу сформированного КП.",
        "Менеджеру необходимо рассчитать их вручную и добавить в КП перед отправкой клиенту.",
        "",
        "ПОЗИЦИИ ДЛЯ РУЧНОГО РАСЧЁТА",
    ]
    for index, item in enumerate(manual_items, 1):
        lines.extend([
            "",
            f"{index}. {item.name}",
            f"   Строка ТЗ: {item.source_ref}.",
            f"   Количество: {item.quantity} шт.",
            f"   Размер: {_item_size_for_report(item)}.",
            f"   Комплектация: {item.system or 'не определена'}; {item.fabric or 'ткань не определена'}; {item.color or 'цвет не указан'}.",
            f"   Причина: {item.note}.",
        ])
    return "\n".join(lines) + "\n"


def _write_review_reports(context: TaskContext, report: dict[str, Any], text: str) -> tuple[Path, Path]:
    # Папку output могут очистить вручную, пока агент рассчитывает ТЗ.
    # В этом случае отчёт всё равно обязан сохраниться.
    context.output_dir.mkdir(parents=True, exist_ok=True)
    text_path, json_path = _review_report_paths(context)
    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return text_path, json_path


def _write_manual_reports(context: TaskContext, report: dict[str, Any], text: str) -> tuple[Path, Path]:
    context.output_dir.mkdir(parents=True, exist_ok=True)
    text_path, json_path = _manual_report_paths(context)
    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return text_path, json_path


def process_file(path: Path, agent_config: dict[str, Any], llm_config: dict[str, Any]) -> bool:
    context = TaskContext(path)
    db = KnowledgeBase()
    try:
        log(f"Обрабатываю ТЗ: {path.name}")
        knowledge_stats = initialize_knowledge(db)
        context.save("knowledge.json", knowledge_stats)
        llm = create_llm_provider(llm_config, logger=log)
        items = parse_tz(path, llm, db)
        context.save("parsed_items.json", [asdict(item) for item in items])
        log(f"Извлечено позиций: {len(items)}")
        priced, unresolved, invalid = price_items(items, agent_config, db, logger=log)
        context.save("calculation.json", {
            "priced": [asdict(item) for item in priced],
            "unresolved": [asdict(item) for item in unresolved],
            "invalid": [asdict(item) for item in invalid],
        })
        dialogue = None
        manual_items: list[Any] = []
        if unresolved and path.parent.resolve() == INPUT_DIR.resolve() and agent_config.get("interactive_clarifications", True):
            dialogue = ask_user(unresolved, llm, log)
            if dialogue:
                context.save("clarification_dialogue.json", dialogue)
                if dialogue["resolved_count"]:
                    log(f"Применено подтверждённых решений: {dialogue['resolved_count']}. Пересчитываю ТЗ.")
                    priced, unresolved, invalid = price_items(items, agent_config, db, logger=log)
                    context.save("calculation.json", {
                        "priced": [asdict(item) for item in priced],
                        "unresolved": [asdict(item) for item in unresolved],
                        "invalid": [asdict(item) for item in invalid],
                    })
                elif dialogue.get("selected", {}).get("key") == "defer" and priced:
                    manual_items = list(unresolved)
                    unresolved = []
                    context.save("calculation.json", {
                        "priced": [asdict(item) for item in priced],
                        "manual": [asdict(item) for item in manual_items],
                        "unresolved": [],
                        "invalid": [asdict(item) for item in invalid],
                    })
                    log(f"Пропущено позиций для ручного расчёта: {len(manual_items)}. Формирую КП по остальным позициям.")

        if unresolved or not priced:
            no_recognized_items = not items
            reason = (
                "В документе не найдено позиций для расчёта"
                if no_recognized_items
                else "Есть позиции без проверенного ценового правила"
            )
            report = {
                "status": "requires_review",
                "source": path.name,
                "reason": reason,
                "unresolved": [asdict(item) for item in unresolved],
                "invalid": [asdict(item) for item in invalid],
            }
            text_path, _ = _write_review_reports(context, report, requires_review_text(path, priced, unresolved, invalid, dialogue))
            context.save("result.json", report)
            if no_recognized_items:
                log("КП не создано: в документе не распознано позиций для расчёта. Исходное ТЗ сохранено.")
            else:
                log("КП не создано: требуется проверенное ценовое правило. Исходное ТЗ сохранено.")
            log(f"Понятный отчёт для менеджера: {text_path.relative_to(OUTPUT_DIR.parent)}")
            return False
        output = create_quote_pdf(path, priced, agent_config, context.output_dir, manual_items=manual_items)
        review = review_quote(output, priced, unresolved, invalid, agent_config, manual_items=manual_items)
        context.save("review.json", asdict(review))
        if not review.ok:
            rejected = context.path / f"rejected_{output.name}"
            shutil.move(str(output), rejected)
            report = {"status": "rejected", "quote": str(rejected), **asdict(review)}
            text = (
                "КП НЕ ВЫДАНО ИЗ-ЗА ТЕХНИЧЕСКОЙ ПРОВЕРКИ\n\n"
                f"ТЗ: {path.name}\n"
                "Исходное ТЗ оставлено в папке input. Ничего не исправляйте вручную: "
                "передайте этот отчёт техническому специалисту.\n\n"
                "Причина:\n- " + "\n- ".join(review.errors) + "\n"
            )
            _write_review_reports(context, report, text)
            context.save("result.json", report)
            log(f"Проверяющий отклонил КП: {len(review.errors)} ошибок. ТЗ сохранено.")
            return False
        result_status = "success_with_manual_items" if manual_items else "success"
        result: dict[str, Any] = {"status": result_status, "quote": str(output), "review": asdict(review)}
        if manual_items:
            manual_report = {
                "status": "manual_calculation_required",
                "source": path.name,
                "quote": str(output),
                "manual_items": [asdict(item) for item in manual_items],
            }
            manual_text_path, manual_json_path = _write_manual_reports(
                context,
                manual_report,
                manual_calculation_text(path, priced, manual_items),
            )
            result["manual_report"] = str(manual_text_path)
            result["manual_report_json"] = str(manual_json_path)
            result["manual_items"] = [asdict(item) for item in manual_items]
            log(f"Требуется ручная корректировка КП: {manual_text_path.relative_to(OUTPUT_DIR.parent)}")
        context.save("result.json", result)
        log(f"КП создано и прошло проверку: {output.relative_to(OUTPUT_DIR.parent)}")
        if path.parent.resolve() == INPUT_DIR.resolve():
            path.unlink()
            log(f"Исходное ТЗ удалено из input после успеха: {path.name}")
        return True
    except Exception as error:
        context.save("result.json", {"status": "error", "error": f"{type(error).__name__}: {error}"})
        log(f"Ошибка: {type(error).__name__}: {error}")
        return False
    finally:
        db.close()
