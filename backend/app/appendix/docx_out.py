"""A Word document written directly (it is a zip of XML parts), so no library is needed."""

# ruff: noqa: E501  (the XML parts below are fixed text, not code)

import io
import zipfile
from xml.sax.saxutils import escape

from app.appendix.model import (
    INTRO,
    OVERVIEW_LABEL,
    OVERVIEW_NOTE,
    OVERVIEW_UNAVAILABLE,
    RECORD_NOTE,
    SNAPSHOT_NOTE,
    SPEAKER_LABELS,
    SUMMARY_NOTE,
    AppendixInput,
    display_value,
)

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
</Types>"""
_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
</Relationships>"""
_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {_NS}>
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial" w:eastAsia="Arial"/><w:sz w:val="21"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="C8501E"/><w:sz w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="C8501E"/><w:sz w:val="25"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Label"><w:name w:val="Label"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="40"/></w:pPr><w:rPr><w:b/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="5F6470"/><w:sz w:val="18"/></w:rPr></w:style>
</w:styles>"""
_FOOTER = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr {_NS}><w:p><w:pPr><w:jc w:val="center"/></w:pPr>
<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:t xml:space="preserve">Page </w:t></w:r>
<w:fldSimple w:instr=" PAGE "><w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:t>1</w:t></w:r></w:fldSimple>
<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:t xml:space="preserve"> of </w:t></w:r>
<w:fldSimple w:instr=" NUMPAGES "><w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:t>1</w:t></w:r></w:fldSimple>
</w:p></w:ftr>"""
_PAGE = (
    '<w:sectPr><w:footerReference w:type="default" r:id="rId2"/>'
    '<w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" w:gutter="0"/>'
    "</w:sectPr>"
)


def _paragraph(text: str, style: str = "Normal", *, muted: bool = False) -> str:
    # Newlines inside an answer become line breaks, so an answer stays one paragraph.
    runs = "<w:br/>".join(
        f'<w:t xml:space="preserve">{escape(line)}</w:t>' for line in text.split("\n")
    )
    colour = '<w:rPr><w:color w:val="5F6470"/></w:rPr>' if muted else ""
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r>{colour}{runs}</w:r></w:p>'


def render_docx(appendix: AppendixInput) -> bytes:
    body = [
        _paragraph("Plan on a Page: Appendix", "Title"),
        _paragraph(INTRO, "Note"),
        _paragraph(
            f"Source revision {appendix.source_revision} · {appendix.included_turns} conversation turns",
            "Note",
        ),
        _paragraph(SNAPSHOT_NOTE, "Note"),
        _paragraph("Summary", "Heading1"),
    ]
    if appendix.summary.overview:
        body += [
            _paragraph(OVERVIEW_LABEL, "Label"),
            _paragraph(appendix.summary.overview),
            _paragraph(OVERVIEW_NOTE, "Note"),
        ]
    else:
        body.append(_paragraph(OVERVIEW_UNAVAILABLE, "Note"))
    body.append(_paragraph(SUMMARY_NOTE, "Note"))
    for label, text in appendix.summary.facts:
        body += [_paragraph(label, "Label"), _paragraph(text)]
    body.append(_paragraph("Key answers", "Heading2"))
    for label, text in appendix.summary.key_answers:
        body += [_paragraph(label, "Label"), _paragraph(text)]
    body.append(_paragraph("Part A. Full answers", "Heading1"))
    for section in appendix.sections:
        body.append(_paragraph(section.title, "Heading2"))
        for answer in section.answers:
            body.append(_paragraph(answer.label, "Label"))
            body.append(_paragraph(display_value(answer), muted=answer.value is None))
    body.append(_paragraph("Part B. Conversation record", "Heading1"))
    body.append(_paragraph(RECORD_NOTE, "Note"))
    for turn in appendix.turns:
        body.append(_paragraph(f"Turn {turn.number} · {SPEAKER_LABELS[turn.speaker]}", "Label"))
        body.append(_paragraph(turn.text))

    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_NS}><w:body>{''.join(body)}{_PAGE}</w:body></w:document>"
    )
    parts = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "word/document.xml": document,
        "word/_rels/document.xml.rels": _DOC_RELS,
        "word/styles.xml": _STYLES,
        "word/footer1.xml": _FOOTER,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))  # same bytes every time
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()
