"""Build an editable Word report from the shared report content."""

import json
import re
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
BLACK = RGBColor(0, 0, 0)
STYLE_NAMES = {
    "Body": "Normal", "Heading": "Heading 1", "Subheading": "Heading 2",
    "Small": "Report Small", "Title": "Title", "Caption": "Caption",
}


def set_font(style, size, chinese="宋体", latin="Times New Roman", bold=False):
    style.font.name = latin
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = BLACK
    properties = style.element.get_or_add_rPr()
    fonts = properties.get_or_add_rFonts()
    fonts.set(qn("w:eastAsia"), chinese)
    for name in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        fonts.attrib.pop(qn(f"w:{name}"), None)


def configure_document(document):
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.left_margin = section.right_margin = Pt(50)
    section.top_margin, section.bottom_margin = Pt(42), Pt(43)
    section.header_distance = section.footer_distance = Pt(20)
    for style in document.styles:
        if style.type in (WD_STYLE_TYPE.PARAGRAPH, WD_STYLE_TYPE.CHARACTER):
            style.font.color.rgb = BLACK
        for border in style.element.xpath(".//w:pBdr"):
            border.getparent().remove(border)
    for name in ("Report Small", "Report Code", "Report Table", "Report Figure"):
        if name not in document.styles:
            document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    normal = document.styles["Normal"]
    set_font(normal, 12)
    normal.paragraph_format.line_spacing = Pt(17.2)
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.widow_control = True
    snap = OxmlElement("w:snapToGrid")
    snap.set(qn("w:val"), "0")
    normal.element.get_or_add_pPr().append(snap)
    for name, size in (("Title", 22), ("Heading 1", 17), ("Heading 2", 13)):
        style = document.styles[name]
        set_font(style, size, "黑体", bold=False)
        style.paragraph_format.space_before = Pt(0)
        style.paragraph_format.space_after = Pt(14 if name != "Heading 2" else 8)
        style.paragraph_format.line_spacing = 1.2
        style.paragraph_format.keep_with_next = True
    document.styles["Title"].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for name in ("Report Small", "Caption", "Report Table"):
        style = document.styles[name]
        set_font(style, 9.5)
        style.paragraph_format.line_spacing = 1.15
        style.paragraph_format.space_after = Pt(5)
    document.styles["Caption"].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.styles["Caption"].paragraph_format.keep_with_next = False
    code = document.styles["Report Code"]
    set_font(code, 8.5, latin="Courier New")
    code.paragraph_format.line_spacing = Pt(12)
    code.paragraph_format.space_after = Pt(4)
    code.paragraph_format.keep_with_next = True
    figure = document.styles["Report Figure"]
    figure.paragraph_format.space_after = Pt(3)
    figure.paragraph_format.line_spacing = 1
    figure.paragraph_format.keep_with_next = True
    figure.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer = section.footer.paragraphs[0]
    footer.style = document.styles["Report Small"]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    document.core_properties.title = "无线链路调制与编码选择调查 Agent"
    document.core_properties.author = ""
    document.core_properties.last_modified_by = ""


def add_table(document, block):
    rows, widths = block["rows"], block["widths"]
    table = document.add_table(rows=len(rows), cols=len(widths))
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    properties = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{side}")
        for key, value in {"val": "single", "sz": "4", "color": "D9D9D9"}.items():
            element.set(qn(f"w:{key}"), value)
        borders.append(element)
    properties.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for side, value in (("top", "95"), ("bottom", "95"), ("left", "120"), ("right", "120")):
        element = OxmlElement(f"w:{side}")
        element.set(qn("w:w"), value)
        element.set(qn("w:type"), "dxa")
        margins.append(element)
    properties.append(margins)
    for column, width in zip(table.columns, widths):
        column.width = Pt(width)
    for row_index, values in enumerate(rows):
        row = table.rows[row_index]
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for cell, value, width in zip(row.cells, values, widths):
            cell.width = Pt(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            paragraph = cell.paragraphs[0]
            paragraph.style = document.styles["Report Table"]
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.keep_with_next = row_index == 0
            paragraph.add_run(str(value))
            if re.fullmatch(r"[\d.,%/ ]+", str(value)):
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if row_index == 0:
                shading = OxmlElement("w:shd")
                shading.set(qn("w:fill"), "E7EFF4")
                cell._tc.get_or_add_tcPr().append(shading)
        if row_index == 0:
            row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))


def build():
    blocks = json.loads((ROOT / "docs/report_content.json").read_text(encoding="utf-8"))
    document = Document()
    configure_document(document)
    counts = {str(number): 0 for number in range(8)}
    new_page = False
    for block in blocks:
        kind = block["kind"]
        if kind == "paragraph":
            paragraph = document.add_paragraph(block["text"], STYLE_NAMES[block["style"]])
            paragraph.paragraph_format.page_break_before = new_page
            new_page = False
            if block["style"] == "Body":
                counts[block["section"]] += len(re.findall(r"[\u4e00-\u9fff]", block["text"]))
            if block["style"] == "Caption":
                paragraph.paragraph_format.space_before = Pt(5)
        elif kind == "table":
            add_table(document, block)
        elif kind == "image":
            paragraph = document.add_paragraph(style="Report Figure")
            picture = paragraph.add_run().add_picture(str(ROOT / block["path"]),
                                                     width=Pt(block["width"]), height=Pt(block["height"]))
            picture._inline.docPr.set("descr", block["label"])
        elif kind == "code":
            document.add_paragraph(block["text"], "Report Code")
        elif kind == "page_break":
            new_page = True
    statistics = json.loads((ROOT / "results/report_word_count.json").read_text(encoding="utf-8"))
    assert counts.pop("0") == statistics["abstract"]
    assert counts == statistics["chapters"]
    destination = ROOT / "report.docx"
    document.save(destination)
    print(json.dumps({"document": str(destination), "body_han_characters": sum(counts.values()),
                      "chapters": counts, "tables": len(document.tables),
                      "figures": len(document.inline_shapes)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
