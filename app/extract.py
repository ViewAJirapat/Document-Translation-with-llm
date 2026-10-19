"""PDF -> ordered text blocks using docling. Pages without a text layer are OCR'd with Tesseract."""
import re

import pypdfium2
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions, TesseractCliOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel, ListItem, SectionHeaderItem, TableItem

from . import config, tables

KIND = {
    DocItemLabel.TITLE: "heading",
    DocItemLabel.SECTION_HEADER: "heading",
    DocItemLabel.TEXT: "paragraph",
    DocItemLabel.PARAGRAPH: "paragraph",
    DocItemLabel.HANDWRITTEN_TEXT: "paragraph",
    DocItemLabel.LIST_ITEM: "list_item",
    DocItemLabel.REFERENCE: "reference",
    DocItemLabel.CAPTION: "caption",
    DocItemLabel.FOOTNOTE: "footnote",
    DocItemLabel.TABLE: "table",
    DocItemLabel.CODE: "code",
    DocItemLabel.FORMULA: "formula",
}
VERBATIM = {"code", "formula", "reference"}  # copied into the output untranslated
# Page headers/footers, pictures and anything else not in KIND are dropped.

# "experi- ment" left over from words hyphenated across a line break (only lowercase-to-lowercase,
# so "COVID- 19" or "self- Assessment" style breaks are left alone).
LINE_HYPHEN = re.compile(r"([a-z])- ([a-z])")
# Bullet glyphs that docling sometimes leaves at either end of a list item's text.
STRAY_BULLET = re.compile(r"^[·•▪◦‣*-]\s+|\s+[·•▪◦‣]$")
# A page with fewer extractable characters than this is treated as an image (scanned) page.
SCANNED_PAGE_CHARS = 30

_converters = {}


def _converter(full_page_ocr):
    # Building a converter loads the layout and table models, so keep one per OCR mode.
    if full_page_ocr not in _converters:
        options = PdfPipelineOptions(artifacts_path=config.DOCLING_MODELS)
        options.do_ocr = True
        options.do_table_structure = True
        # DEFAULT only OCRs image regions of pages that also have real text; on fully scanned pages
        # it loses table columns, so scans get whole-page OCR instead.
        options.ocr_options = TesseractCliOcrOptions(
            lang=["eng"], mode=OcrMode.FULL_PAGE if full_page_ocr else OcrMode.DEFAULT
        )
        _converters[full_page_ocr] = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
    return _converters[full_page_ocr]


def looks_scanned(path):
    """True when most pages have (almost) no text layer."""
    pdf = pypdfium2.PdfDocument(str(path))
    try:
        image_pages = sum(
            1 for page in pdf if len(page.get_textpage().get_text_range().strip()) < SCANNED_PAGE_CHARS
        )
        return image_pages * 2 >= len(pdf)
    finally:
        pdf.close()


def _table_markdown(item):
    def clean(text):
        # OCR reads table borders as "|"; drop them at the edges and keep inner ones from breaking Markdown.
        return text.strip().strip("|").strip().replace("|", "/").replace("\n", " ")

    rows = [[clean(cell.text) for cell in row] for row in item.data.grid]
    return tables.render([r for r in rows if any(r)])


def extract(path, force_ocr=False):
    """Returns (blocks, page_count). Each block: kind, text, page, and level/marker/verbatim when relevant."""
    doc = _converter(force_ocr or looks_scanned(path)).convert(str(path)).document
    blocks = []
    for item, _depth in doc.iterate_items():
        kind = KIND.get(item.label)
        if kind is None:
            continue
        if isinstance(item, TableItem):
            text = _table_markdown(item)
        else:
            text = LINE_HYPHEN.sub(r"\1\2", (item.text or "").strip())
            if kind == "list_item":
                text = STRAY_BULLET.sub("", text)
        if not text:
            continue
        block = {"kind": kind, "text": text, "page": item.prov[0].page_no if item.prov else None}
        if item.label == DocItemLabel.TITLE:
            block["level"] = 1
        elif isinstance(item, SectionHeaderItem):
            block["level"] = min(item.level + 1, 4)
        if isinstance(item, ListItem):
            block["marker"] = (item.marker or "").strip() or None
        if kind in VERBATIM:
            block["verbatim"] = True
        blocks.append(block)
    return blocks, len(doc.pages)
