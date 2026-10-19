"""PDF -> ordered text blocks using docling. Pages without a text layer are OCR'd with Tesseract."""
import re

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel, ListItem, SectionHeaderItem, TableItem

from . import config

KIND = {
    DocItemLabel.TITLE: "heading",
    DocItemLabel.SECTION_HEADER: "heading",
    DocItemLabel.TEXT: "paragraph",
    DocItemLabel.PARAGRAPH: "paragraph",
    DocItemLabel.LIST_ITEM: "list_item",
    DocItemLabel.CAPTION: "caption",
    DocItemLabel.FOOTNOTE: "footnote",
    DocItemLabel.TABLE: "table",
    DocItemLabel.CODE: "code",
    DocItemLabel.FORMULA: "formula",
}
VERBATIM = {"code", "formula"}  # copied into the output untranslated
# Page headers/footers, pictures and anything else not in KIND are dropped.

# "experi- ment" left over from words hyphenated across a line break (only lowercase-to-lowercase,
# so "COVID- 19" or "self- Assessment" style breaks are left alone).
LINE_HYPHEN = re.compile(r"([a-z])- ([a-z])")

_converters = {}


def _converter(force_ocr):
    # Building a converter loads the layout and table models, so keep one per OCR mode.
    if force_ocr not in _converters:
        options = PdfPipelineOptions(artifacts_path=config.DOCLING_MODELS)
        options.do_ocr = True
        options.do_table_structure = True
        options.ocr_options = TesseractCliOcrOptions(lang=["eng"], force_full_page_ocr=force_ocr)
        _converters[force_ocr] = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
    return _converters[force_ocr]


def extract(path, force_ocr=False):
    """Returns (blocks, page_count). Each block: kind, text, page, and level/marker/verbatim when relevant."""
    doc = _converter(force_ocr).convert(str(path)).document
    blocks = []
    for item, _depth in doc.iterate_items():
        kind = KIND.get(item.label)
        if kind is None:
            continue
        if isinstance(item, TableItem):
            text = item.export_to_markdown(doc=doc).strip()
        else:
            text = LINE_HYPHEN.sub(r"\1\2", (item.text or "").strip())
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
