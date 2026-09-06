from pathlib import Path
from datetime import datetime
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from openpyxl import Workbook

from source.agent.llm import create_llm_provider
from source.agent.ollama_provider import OllamaProvider
from source.agent.router import select_skill
from source.agent.runtime import _write_review_reports, manual_calculation_text, requires_review_text
from source.core.config import ROOT, load_agent_config, load_llm_config
from source.core.models import QuoteItem
from source.core.naming import job_folder_name, quote_filename, slug
from source.memory.knowledge import KnowledgeBase, initialize_knowledge
from source.tools.pdf import create_quote_pdf
from source.tools.pricing import local_price_file, price_items, vertical_price, vertical_price_suggestions
from source.tools.reviewer import _contains_temporary_service, review_quote
from source.tools.tz_parser import extract_records, parse_tz
from source.ui.dialogue import apply_supported_choice, build_options


def silent(*_args, **_kwargs):
    return None


def test_format_extractors() -> None:
    examples = ROOT / "ПримерыТЗ"
    solar = extract_records(examples / "шторы Солнечногорск.xlsx")
    assert len(solar) == 30
    assert solar[0]["width_m"] == 0.86 and solar[0]["height_m"] == 2.64 and solar[0]["quantity"] == 3

    vertical = extract_records(examples / "ЖАЛЮЗИ 1.docx")
    # 27 изделий; доставка и монтаж из исходного бланка намеренно не импортируются.
    assert len(vertical) == 27
    assert vertical[0]["area_m2"] == 9.25 and vertical[0]["quantity"] == 2

    procurement = extract_records(examples / "ТЗ_рулонные шторы 2026 (2) (1).docx")
    assert len(procurement) == 10
    assert procurement[0]["width_m"] == 1.51 and procurement[0]["height_m"] == 1.775

    technical = extract_records(examples / "Техническое_задание_на_изготовление_рулонных_штор_коррект.pdf")
    assert len(technical) >= 30


def test_structured_xlsx_inherits_product_fields_without_qwen() -> None:
    class NoLLM:
        def __init__(self) -> None:
            self.calls: list[list[dict]] = []

        def extract_items(self, records, _context):
            self.calls.append(records)
            return []

    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "сложное ТЗ.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["помещение", "наименование изделий", "прозрачность материала", "материал", "ширина, м", "высота, м", "кол-во"])
        sheet.append(["Холл", "вертикальные жалюзи", "50% затемнения", "Мальта белый", 2.0, 1.5, 1])
        sheet.append([None, None, None, None, 2.1, 1.6, 2])
        sheet.append(["Окно", "рулонная штора АМГ 32", "100% затемнения", "Альфа Black-Out белая", 1.0, 1.5, 1])
        workbook.save(path)
        records = extract_records(path)
        assert len(records) == 3
        assert records[1]["name"] == "вертикальные жалюзи"
        assert records[1]["fabric"] == "Мальта белый"
        assert records[1]["inherited_name"] is True
        assert all(record["structured"] for record in records)

        db = KnowledgeBase()
        try:
            initialize_knowledge(db)
            llm = NoLLM()
            items = parse_tz(path, llm, db)
            assert len(items) == 3
            assert llm.calls == [[]]
            assert items[1].system == ""
            assert items[2].system == "AMG"
            priced, unresolved, invalid = price_items(items, load_agent_config(), db, logger=silent)
            assert len(priced) == 3
            assert not unresolved
            assert not invalid
        finally:
            db.close()


def test_public_tools_package_has_no_circular_import() -> None:
    from source.tools import extract_records as public_extract_records

    assert public_extract_records is extract_records


def test_pricing_without_delivery_or_installation() -> None:
    agent_config = load_agent_config()
    llm_config = {**load_llm_config("ollama"), "url": "http://127.0.0.1:1/api/chat", "timeout_seconds": 1}
    db = KnowledgeBase()
    try:
        initialize_knowledge(db)
        client = create_llm_provider(llm_config, logger=silent)
        items = parse_tz(ROOT / "ПримерыТЗ" / "шторы Солнечногорск.xlsx", client, db)
        priced, unresolved, invalid = price_items(items, agent_config, db, logger=silent)
        assert len(priced) == 29
        assert not unresolved
        assert len(invalid) == 1
        assert sum(item.line_total for item in priced) == 862325
        assert "quote_template" not in agent_config
        assert "discount_percent" not in agent_config
        assert "installation" not in agent_config
        assert "delivery" not in agent_config
        assert agent_config["warranty_text"] == "Гарантия на изделия 1 год."
        assert agent_config["production_days"] == 10
    finally:
        db.close()


def test_vertical_blinds_area_pricing() -> None:
    agent_config = load_agent_config()
    llm_config = {**load_llm_config("ollama"), "url": "http://127.0.0.1:1/api/chat", "timeout_seconds": 1}
    db = KnowledgeBase()
    try:
        initialize_knowledge(db)
        items = parse_tz(ROOT / "ПримерыТЗ" / "ЖАЛЮЗИ 1.docx", create_llm_provider(llm_config, logger=silent), db)
        priced, unresolved, invalid = price_items(items, agent_config, db, logger=silent)
        assert len(priced) == 27
        assert not unresolved
        assert not invalid
        assert all(item.price_source for item in priced)
        assert sum(item.category == "E" for item in priced) == 26
        assert sum(item.category == "комплектация" and item.price_rub == 0 for item in priced) == 1
    finally:
        db.close()


def test_vertical_price_matches_material_inside_catalog_cell() -> None:
    source = local_price_file()
    malta = QuoteItem("test:1", "вертикальные жалюзи", 1, area_m2=3.1, fabric="Мальта белый")
    plain_blackout = QuoteItem("test:2", "вертикальные жалюзи", 1, area_m2=3.1, fabric="Плэйн В/О бежевый")
    _, malta_category, _ = vertical_price(source, malta, 81)
    _, plain_category, _ = vertical_price(source, plain_blackout, 81)
    assert malta_category == "E"
    assert plain_category == "4"


def test_partial_vertical_match_requires_user_choice() -> None:
    source = local_price_file()
    exact_item = QuoteItem(
        "docx:1:1",
        "Жалюзи Тканевые ЛАЙН II, бежевый",
        1,
        area_m2=2.0,
        fabric="Жалюзи Тканевые",
        color="ЛАЙН II, бежевый",
    )
    exact_price, exact_category, _ = vertical_price(source, exact_item, 81)
    assert exact_price is not None and exact_category == "E"

    item = QuoteItem(
        "docx:1:2",
        "Жалюзи Тканевые Лайн 32, т.бежевый NEW",
        2,
        area_m2=9.25,
        fabric="Жалюзи Тканевые",
        color="Лайн 32, т.бежевый NEW",
    )
    price, category, _ = vertical_price(source, item, 81)
    assert price is None and category is None

    suggestions = vertical_price_suggestions(source, item)
    assert suggestions[0]["collection"] == "ЛАЙН II"
    item.raw["vertical_pricing_query"] = "ЛАЙН 32, Т.БЕЖЕВЫЙ NEW"
    item.raw["vertical_pricing_original"] = "Лайн 32, т.бежевый NEW"
    item.raw["pricing_suggestions"] = suggestions
    options = build_options([item])
    assert options[0]["label"].startswith("Рассчитать как «ЛАЙН II»")
    assert options[-1]["key"] == "defer"

    assert apply_supported_choice([item], options[0]["key"]) == 1
    assert item.raw["customer_replacement"] == {
        "original": "Лайн 32, т.бежевый NEW",
        "replacement": "ЛАЙН II",
    }
    price, category, source_text = vertical_price(source, item, 81)
    assert price == 13870
    assert category == "E"
    assert "выбор пользователя" in source_text


def test_customer_replacement_is_highlighted_and_reviewed_in_pdf() -> None:
    item = QuoteItem(
        "docx:1:2",
        "Жалюзи Тканевые Лайн 32, т.бежевый NEW",
        2,
        area_m2=9.25,
        fabric="Жалюзи Тканевые",
        color="Лайн 32, т.бежевый NEW",
        category="E",
        price_rub=13870,
        price_source="Локальный прайс / Вертикальные / ЛАЙН II / выбор пользователя",
        raw={
            "customer_replacement": {
                "original": "Лайн 32, т.бежевый NEW",
                "replacement": "ЛАЙН II",
            }
        },
    )
    with TemporaryDirectory() as temporary:
        pdf = create_quote_pdf(Path("ТЗ.docx"), [item], load_agent_config(), Path(temporary))
        review = review_quote(pdf, [item], [], [], load_agent_config())
        assert review.ok, review.errors
        assert review.checks["replacement_items"] == 1
        assert any("замены" in warning for warning in review.warnings)


def test_procurement_docx_requires_only_angular_rule() -> None:
    agent_config = load_agent_config()
    llm_config = {**load_llm_config("ollama"), "url": "http://127.0.0.1:1/api/chat", "timeout_seconds": 1}
    db = KnowledgeBase()
    try:
        initialize_knowledge(db)
        items = parse_tz(
            ROOT / "ПримерыТЗ" / "ТЗ_рулонные шторы 2026 (2) (1).docx",
            create_llm_provider(llm_config, logger=silent),
            db,
        )
        priced, unresolved, invalid = price_items(items, agent_config, db, logger=silent)
        assert len(items) == 10
        assert len(priced) == 9
        assert len(unresolved) == 1
        assert not invalid
        assert unresolved[0].name == "Штора (угловая)"
        assert unresolved[0].width_m == 0.72
        assert "угловой шторы" in unresolved[0].note
        text = requires_review_text(ROOT / "ПримерыТЗ" / "ТЗ_рулонные шторы 2026 (2) (1).docx", priced, unresolved, invalid)
        assert "КП НЕ СФОРМИРОВАНО" in text
        assert "верх 590 мм, низ 720 мм, высота 1720 мм" in text
        assert "Укажите правило расчёта" in text
    finally:
        db.close()


def test_manual_calculation_report_identifies_skipped_item() -> None:
    item = QuoteItem(
        "DOCX:10",
        "Штора (угловая)",
        1,
        width_m=0.72,
        height_m=1.72,
        system="AMG",
        note="Нет отдельного правила расчёта",
        raw={"variant": "angular_unverified", "raw_text": "590 (верхний край) 720 (нижний край)"},
    )
    text = manual_calculation_text(Path("ТЗ.docx"), [QuoteItem("DOCX:1", "AMG", 1)], [item])
    assert "КП СФОРМИРОВАНО ЧАСТИЧНО" in text
    assert "Штора (угловая)" in text
    assert "DOCX:10" in text
    assert "не включены в сумму" in text
    assert "добавить в КП перед отправкой" in text


def test_bnt_electrics_pdf_pricing() -> None:
    agent_config = load_agent_config()
    llm_config = {**load_llm_config("ollama"), "url": "http://127.0.0.1:1/api/chat", "timeout_seconds": 1}
    db = KnowledgeBase()
    try:
        initialize_knowledge(db)
        items = parse_tz(ROOT / "ПримерыТЗ" / "ЗН Восток.pdf", create_llm_provider(llm_config, logger=silent), db)
        priced, unresolved, invalid = price_items(items, agent_config, db, logger=silent)
        assert len(items) == 8
        assert len(priced) == 8
        assert not unresolved
        assert not invalid
        assert items[0].raw["component_widths_m"] == [1.447, 1.496, 0.898]
        assert items[2].raw["component_widths_m"] == [1.496, 1.496, 0.87]
        assert [item.price_rub for item in priced] == [78856, 69414, 78896, 59305, 63318, 69705, 3413, 2560]
        assert sum(item.line_total for item in priced) == 425467
        assert all(item.price_source for item in priced)
    finally:
        db.close()


def test_mounting_profile_is_not_installation_service() -> None:
    assert _contains_temporary_service("Рулонная штора с монтажным профилем") is None
    assert _contains_temporary_service("Монтаж изделий — 1 услуга") == "монтаж"


def test_human_readable_result_names() -> None:
    source = Path("ТЗ_рулонные шторы 2026 (2) (1).docx")
    assert slug(source.stem) == "ТЗ-рулонные-шторы-2026-2-1"
    assert job_folder_name(source, datetime(2026, 9, 1, 21, 20), "66fe9f") == (
        "2026-09-01_21-20_ТЗ-рулонные-шторы-2026-2-1_66fe9f"
    )
    assert quote_filename(source) == "КП_ТЗ-рулонные-шторы-2026-2-1.pdf"


def test_slug_avoids_windows_reserved_device_names() -> None:
    assert slug("con") == "con-ТЗ"
    assert slug("COM3") == "COM3-ТЗ"
    assert slug("lpt1") == "lpt1-ТЗ"
    assert slug("NUL") == "NUL-ТЗ"
    assert slug("controller") == "controller"


def test_terminal_menu_defers_posix_imports_so_it_loads_on_windows_too() -> None:
    from source.ui import terminal_menu

    # termios/tty don't exist on Windows; they must be imported lazily inside
    # _choose_option_posix, not at module scope, or the module itself would
    # fail to import there.
    assert not hasattr(terminal_menu, "termios")
    assert not hasattr(terminal_menu, "tty")
    assert terminal_menu.choose_option([]) is None


def test_router_accepts_only_supported_tz_formats() -> None:
    assert select_skill(Path("ТЗ.docx")) == "make_proposal"
    try:
        select_skill(Path("ТЗ.txt"))
    except ValueError:
        pass
    else:
        raise AssertionError("Router должен отклонять неподдерживаемый формат")


def test_llm_provider_is_selected_by_config() -> None:
    provider = create_llm_provider({"provider": "ollama", "model": "test", "url": "http://127.0.0.1:1"}, logger=silent)
    assert isinstance(provider, OllamaProvider)
    assert provider.extract_items([]) == []
    try:
        create_llm_provider({"provider": "unknown"}, logger=silent)
    except ValueError as error:
        assert "Неподдерживаемый" in str(error)
    else:
        raise AssertionError("Неизвестный провайдер должен быть отклонён")


def test_cloud_providers_are_selected_by_config_and_require_api_key() -> None:
    import os

    from source.agent.claude_provider import ClaudeProvider
    from source.agent.openai_compatible_provider import OpenAICompatibleProvider

    env_var = "MASTERPROFI_TEST_MISSING_KEY"
    os.environ.pop(env_var, None)
    for provider_name, expected_class in (("claude", ClaudeProvider), ("openai", OpenAICompatibleProvider), ("deepseek", OpenAICompatibleProvider), ("qwen", OpenAICompatibleProvider)):
        try:
            create_llm_provider({"provider": provider_name, "model": "test", "api_key_env": env_var}, logger=silent)
        except ValueError as error:
            assert "API-ключ" in str(error)
        else:
            raise AssertionError(f"{provider_name} без ключа API должен быть отклонён")

    os.environ[env_var] = "test-key"
    try:
        claude = create_llm_provider(
            {"provider": "claude", "model": "test", "api_key_env": env_var, "base_url": "http://127.0.0.1:1"},
            logger=silent,
        )
        assert isinstance(claude, ClaudeProvider)
        deepseek = create_llm_provider(
            {"provider": "deepseek", "model": "test", "api_key_env": env_var, "base_url": "http://127.0.0.1:1"},
            logger=silent,
        )
        assert isinstance(deepseek, OpenAICompatibleProvider)
    finally:
        os.environ.pop(env_var, None)


def test_llm_config_defaults_to_openai_and_lists_all_providers() -> None:
    from source.core.config import list_llm_providers

    default_config = load_llm_config()
    assert default_config["provider"] == "openai"
    assert default_config["api_key_env"] == "OPENAI_API_KEY"
    assert "base_url" in default_config

    providers = dict(list_llm_providers())
    assert set(providers) == {"openai", "ollama", "claude", "deepseek", "qwen"}

    claude_config = load_llm_config("claude")
    assert claude_config["provider"] == "claude"
    assert "base_url" in claude_config


def test_report_folder_is_recreated_if_removed_during_calculation() -> None:
    with TemporaryDirectory() as temporary:
        output_dir = Path(temporary) / "result"
        context = SimpleNamespace(output_dir=output_dir)
        text_path, json_path = _write_review_reports(context, {"status": "requires_review"}, "Нужно уточнение\n")
        assert text_path.read_text(encoding="utf-8") == "Нужно уточнение\n"
        assert json_path.exists()


def test_large_review_report_groups_repeated_unresolved_items() -> None:
    unresolved = [
        QuoteItem(f"xlsx:{index}", "римская штора", 1, width_m=2.2, height_m=1.6, fabric="Марсель", note="Не определена поддерживаемая система изделия")
        for index in range(1, 14)
    ]
    text = requires_review_text(Path("большое ТЗ.xlsx"), [], unresolved, [])
    assert "ГРУППЫ ПОЗИЦИЙ" in text
    assert "Строк ТЗ: 13; всего изделий: 13 шт." in text
    assert "Полный перечень строк" in text


def test_user_can_choose_safe_angular_amg_rule() -> None:
    agent_config = load_agent_config()
    db = KnowledgeBase()
    try:
        initialize_knowledge(db)
        items = parse_tz(
            ROOT / "ПримерыТЗ" / "ТЗ_рулонные шторы 2026 (2) (1).docx",
            create_llm_provider({**load_llm_config("ollama"), "url": "http://127.0.0.1:1/api/chat", "timeout_seconds": 1}, logger=silent),
            db,
        )
        _, unresolved, _ = price_items(items, agent_config, db, logger=silent)
        assert len(unresolved) == 1
        options = build_options(unresolved)
        assert [option["key"] for option in options] == ["angular_regular_amg", "defer"]
        assert apply_supported_choice(unresolved, options[0]["key"]) == 1
        priced, unresolved, invalid = price_items(items, agent_config, db, logger=silent)
        assert len(priced) == 10
        assert not unresolved
        assert not invalid
    finally:
        db.close()


if __name__ == "__main__":
    test_format_extractors()
    test_structured_xlsx_inherits_product_fields_without_qwen()
    test_public_tools_package_has_no_circular_import()
    test_pricing_without_delivery_or_installation()
    test_vertical_blinds_area_pricing()
    test_vertical_price_matches_material_inside_catalog_cell()
    test_partial_vertical_match_requires_user_choice()
    test_customer_replacement_is_highlighted_and_reviewed_in_pdf()
    test_procurement_docx_requires_only_angular_rule()
    test_manual_calculation_report_identifies_skipped_item()
    test_bnt_electrics_pdf_pricing()
    test_mounting_profile_is_not_installation_service()
    test_human_readable_result_names()
    test_slug_avoids_windows_reserved_device_names()
    test_terminal_menu_defers_posix_imports_so_it_loads_on_windows_too()
    test_router_accepts_only_supported_tz_formats()
    test_llm_provider_is_selected_by_config()
    test_cloud_providers_are_selected_by_config_and_require_api_key()
    test_llm_config_defaults_to_openai_and_lists_all_providers()
    test_report_folder_is_recreated_if_removed_during_calculation()
    test_large_review_report_groups_repeated_unresolved_items()
    test_user_can_choose_safe_angular_amg_rule()
    print("OK")
