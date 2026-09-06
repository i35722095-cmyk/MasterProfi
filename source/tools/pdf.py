from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..core.config import OUTPUT_DIR
from ..core.models import QuoteItem
from ..core.naming import quote_filename


# Системный шрифт Arial по платформам: macOS хранит его в /System/Library,
# Windows — в C:\Windows\Fonts. Ни один шрифт не поставляется в репозитории.
_FONT_CANDIDATES: dict[str, tuple[str, str]] = {
    "darwin": ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    "win32": (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
}


def _register_fonts() -> None:
    if "MasterProfiArial" in pdfmetrics.getRegisteredFontNames():
        return
    regular, bold = _FONT_CANDIDATES.get(sys.platform, _FONT_CANDIDATES["darwin"])
    if not Path(regular).exists() or not Path(bold).exists():
        raise RuntimeError(
            f"Не найден шрифт Arial для платформы '{sys.platform}' "
            f"(искал {regular} и {bold}). Установите Arial или укажите путь "
            "к другому TTF-шрифту в source/tools/pdf.py."
        )
    pdfmetrics.registerFont(TTFont("MasterProfiArial", regular))
    pdfmetrics.registerFont(TTFont("MasterProfiArial-Bold", bold))


def _money(value: int | float) -> str:
    return f"{float(value):,.2f}".replace(",", " ")


def _description(item: QuoteItem) -> str:
    if item.raw.get("variant") in {"bnt_m44_mono_electric", "bnt_l65_electric"}:
        return (
            f"Рулонная штора {item.system} с монтажным профилем<br/>"
            f"Электропривод 220В, радиоуправление; комплектация: белая<br/>"
            f"Ткань: {item.fabric} {item.color}, {item.opacity}"
        )
    if not item.system:
        text = item.name
    else:
        hardware_color = str(item.raw.get("hardware_color") or "белая")
        lines = [f"Рулонные шторы {item.system}, комплектация: {hardware_color}"]
        if item.fabric:
            lines.append(f"Ткань: {item.fabric} {item.color}, {item.opacity}")
        lines.append("Ручное управление, пластиковая цепь")
        text = "<br/>".join(lines)
    if item.note.startswith("Аналог:"):
        text += "<br/>" + item.note.split(";")[0]
    replacement = item.raw.get("customer_replacement")
    if replacement:
        original = escape(str(replacement.get("original") or "исходный материал"))
        selected = escape(str(replacement.get("replacement") or "выбранная альтернатива"))
        text += (
            '<br/><font color="#9C5700"><b>ЗАМЕНА МАТЕРИАЛА:</b> '
            f'«{original}» → «{selected}»</font>'
        )
    return text


def _size(item: QuoteItem) -> tuple[str, str]:
    if item.raw.get("size_not_required"):
        return "—", "шт."
    if item.raw.get("display_size"):
        return str(item.raw["display_size"]), "мм"
    if item.category == "комплектация":
        return "—", "шт."
    if item.area_m2 is not None and (item.width_m is None or item.height_m is None):
        return f"{float(item.area_m2):.3f}".rstrip("0").rstrip("."), "м²"
    return f"{round(float(item.width_m or 0) * 1000)}×{round(float(item.height_m or 0) * 1000)}", "мм"


def _manual_item_text(item: QuoteItem) -> str:
    if item.width_m and item.height_m:
        size = f"{round(item.width_m * 1000)}×{round(item.height_m * 1000)} мм"
    elif item.area_m2 is not None:
        size = f"{item.area_m2:g} м²"
    else:
        size = "размер не указан"
    return escape(f"{item.source_ref}: {item.name}; {size}; {item.quantity} шт.")


def create_quote_pdf(
    source: Path,
    items: list[QuoteItem],
    config: dict[str, Any],
    output_dir: Path | None = None,
    manual_items: list[QuoteItem] | None = None,
) -> Path:
    _register_fonts()
    destination = output_dir or OUTPUT_DIR
    destination.mkdir(parents=True, exist_ok=True)
    filename = quote_filename(source)
    if output_dir is None:
        filename = f"{Path(filename).stem}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.pdf"
    output = destination / filename
    document = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=13 * mm,
        title=f"Коммерческое предложение — {source.stem}",
        author="ООО «МастерПрофи»",
    )
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("mp-normal", parent=styles["Normal"], fontName="MasterProfiArial", fontSize=9, leading=11)
    small = ParagraphStyle("mp-small", parent=normal, fontSize=7.4, leading=8.7, alignment=TA_CENTER)
    header = ParagraphStyle("mp-header", parent=normal, fontSize=14, leading=16, alignment=TA_CENTER)
    title = ParagraphStyle("mp-title", parent=normal, fontName="MasterProfiArial-Bold", fontSize=11, leading=13, alignment=TA_CENTER)
    bold = ParagraphStyle("mp-bold", parent=normal, fontName="MasterProfiArial-Bold")
    right = ParagraphStyle("mp-right", parent=normal, alignment=TA_RIGHT)
    footer_small = ParagraphStyle("mp-footer-small", parent=normal, fontSize=7.2, leading=8.2)
    footer_right = ParagraphStyle("mp-footer-right", parent=footer_small, alignment=TA_RIGHT)
    warning = ParagraphStyle(
        "mp-warning",
        parent=normal,
        fontName="MasterProfiArial-Bold",
        textColor=colors.HexColor("#A61B1B"),
        borderColor=colors.HexColor("#A61B1B"),
        borderWidth=0.8,
        borderPadding=6,
        spaceAfter=4 * mm,
    )

    metadata = next((item.raw for item in items if item.raw.get("client") or item.raw.get("address")), {})
    client = str(metadata.get("client") or "клиент не указан в ТЗ")
    address = str(metadata.get("address") or "")
    story: list[Any] = [
        Paragraph(
            "МастерПрофи<br/>Общество с ограниченной ответственностью<br/>"
            "ИНН 7733630768, КПП 773301001<br/>"
            "125310, г. Москва, Пятницкое ш., д.54, к.2, стр.6, оф.107<br/>"
            "тел.: +7 (499) 713-00-32",
            header,
        ),
        Spacer(1, 5 * mm),
        Table(
            [[Paragraph("г. Москва", bold), Paragraph(datetime.now().strftime("%d.%m.%Y"), right)]],
            colWidths=[93 * mm, 93 * mm],
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]),
        ),
        Spacer(1, 6 * mm),
        Table(
            [
                [Paragraph("Кому:", bold), Paragraph(client, normal)],
                [Paragraph("Адрес:", bold), Paragraph(address, normal)],
            ],
            colWidths=[28 * mm, 80 * mm],
            hAlign="RIGHT",
        ),
        Spacer(1, 5 * mm),
        Paragraph("Коммерческое предложение", title),
        Spacer(1, 7 * mm),
        Paragraph("Производственная компания «МастерПрофи» предлагает солнцезащитные системы по предоставленному техническому заданию.", normal),
        Spacer(1, 4 * mm),
        Paragraph("Расчёт произведён по предоставленным размерам.", normal),
        Spacer(1, 4 * mm),
    ]
    if manual_items:
        manual_lines = "<br/>".join(_manual_item_text(item) for item in manual_items)
        story.append(Paragraph(
            "ВНИМАНИЕ: КП требует ручной корректировки. Следующие позиции не рассчитаны "
            "и не включены в итоговую сумму:<br/>" + manual_lines,
            warning,
        ))
    replacement_count = sum(bool(item.raw.get("customer_replacement")) for item in items)
    if replacement_count:
        story.append(Paragraph(
            "ВНИМАНИЕ: в предложении есть согласованные альтернативы. "
            "Замены явно указаны в выделенных строках таблицы.",
            warning,
        ))

    table_data: list[list[Any]] = [[
        Paragraph("№", small),
        Paragraph("Наименование товара", small),
        Paragraph("Размер / площадь", small),
        Paragraph("Ед.", small),
        Paragraph("Кол-во", small),
        Paragraph("Цена за ед., руб.", small),
        Paragraph("Стоимость, руб.", small),
    ]]
    for index, item in enumerate(items, 1):
        size, unit = _size(item)
        table_data.append([
            Paragraph(str(index), small),
            Paragraph(_description(item), small),
            Paragraph(size, small),
            Paragraph(unit, small),
            Paragraph(str(item.quantity), small),
            Paragraph(_money(item.price_rub or 0), small),
            Paragraph(_money(item.line_total), small),
        ])
    products = Table(table_data, colWidths=[9 * mm, 66 * mm, 26 * mm, 12 * mm, 16 * mm, 27 * mm, 29 * mm], repeatRows=1, hAlign="CENTER")
    products.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDF8C5")),
        ("FONTNAME", (0, 0), (-1, 0), "MasterProfiArial-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.45, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    for row_index, item in enumerate(items, 1):
        if item.raw.get("customer_replacement"):
            products.setStyle(TableStyle([
                ("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#FFF2CC")),
                ("BOX", (0, row_index), (-1, row_index), 1.1, colors.HexColor("#C65911")),
            ]))
    story.extend([products, Spacer(1, 3 * mm)])

    total = sum(item.line_total for item in items)
    totals = Table(
        [
            [Paragraph("Стоимость изделий", bold), Paragraph(_money(total), right)],
            [Paragraph("ИТОГО", title), Paragraph(_money(total), right)],
        ],
        colWidths=[43 * mm, 29 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#DDF8C5")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    valid_until = (datetime.now() + timedelta(days=int(config["quote_valid_days"]))).strftime("%d.%m.%Y")
    story.extend([
        totals,
        Spacer(1, 5 * mm),
        Paragraph(str(config["warranty_text"]), bold),
        Paragraph(f"Срок изготовления изделий до {int(config['production_days'])} рабочих дней после оплаты.", bold),
        Paragraph("Стоимость изделий с учётом НДС (22%).", normal),
        Paragraph(f"КП действительно до {valid_until} г.", bold),
        Spacer(1, 1.5 * mm),
        Table(
            [[
                Paragraph("Исполнитель: Михейкина Марина Юрьевна<br/>тел.: +7 (499) 713-00-32 · mas.profi@yandex.ru · www.master-mib.ru", footer_small),
                Paragraph("С уважением,<br/><b>ООО «МастерПрофи»</b>", footer_right),
            ]],
            colWidths=[93 * mm, 93 * mm],
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]),
        ),
    ])

    def add_page_number(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("MasterProfiArial", 7)
        canvas.drawRightString(A4[0] - 12 * mm, 7 * mm, f"Страница {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return output
