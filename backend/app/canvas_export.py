"""The one-page canvas as an editable PowerPoint slide, laid out and coloured like the screen one.

Built from the stored answers on request and never kept. Native shapes and text (not a picture), so
it can be edited. Colours match frontend/src/styles.css (`--op-*`).
"""

import io
import math
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fpdf import FPDF
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from app.appendix.model import clean_text, shorten
from app.interview.canvas import OPPORTUNITY_TYPES, PRIORITIES

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PDF_MEDIA_TYPE = "application/pdf"
_FONTS = Path(__file__).parent / "appendix" / "fonts"

_BG, _PANEL, _FOOTER = "1E1E1E", "242424", "070F19"
_WHITE, _MUTED, _SOFT = "FFFFFF", "A3A3A3", "D4D4D4"
_ORANGE, _PURPLE, _BLUE, _GREEN = "F37021", "7B61FF", "02B5E2", "00C48C"
_AI, _CANVAS = "BE7DF0", "EC7C30"
_STEPS = (
    ("IDENTIFY", "the problem"),
    ("EXPLORE", "AI opportunity"),
    ("DESIGN", "solution concept"),
    ("VALIDATE", "value & feasibility"),
    ("BUILD", "prototype"),
)
_SIZES = (
    12,
    11,
    10.5,
    10,
    9.5,
    9,
    8.5,
    8,
    7.5,
    7,
    6.5,
    6,
)  # largest that fits, never smaller than 6
_Run = tuple[str, dict[str, Any]]  # (text, {bold, italic, underline, color, size})


def _rgb(hex_colour: str) -> RGBColor:
    return RGBColor.from_string(hex_colour)


def _text(fields: dict[str, Any], key: str) -> str:
    value = fields.get(key)
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    return clean_text(value).strip() if isinstance(value, str) else ""


def _emit(paragraph, runs: Sequence[_Run], size: float) -> None:
    for text, style in runs:
        run = paragraph.add_run()
        run.text = text
        font = run.font
        font.size = Pt(style.get("size", size))
        font.bold = style.get("bold", False)
        font.italic = style.get("italic", False)
        font.underline = style.get("underline", False)
        font.color.rgb = _rgb(style.get("color", _WHITE))
        font.name = "Arial"


def _box(slide, shape_type, x, y, w, h, *, fill=None, line=None, line_pt=1.0, radius=None, name=""):
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.name = name or "shape"
    if radius is not None:
        shape.adjustments[0] = radius
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(fill)
    else:
        shape.fill.background()
    if line:
        shape.line.color.rgb = _rgb(line)
        shape.line.width = Pt(line_pt)
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _write(
    frame,
    paragraphs: Sequence[Sequence[_Run]],
    size: float,
    *,
    gap_pt: float = 3,
    align=PP_ALIGN.LEFT,
):
    frame.word_wrap = True
    for index, runs in enumerate(paragraphs):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        paragraph.space_after = Pt(gap_pt)
        _emit(paragraph, runs, size)


def _fit(paragraphs: Sequence[Sequence[_Run]], width_in: float, height_in: float) -> float:
    """The largest text size at which the paragraphs fit, from a simple width/line estimate."""
    for size in _SIZES:
        per_line = max(1, int(width_in * 72 / (size * 0.52)))
        lines = sum(
            max(1, math.ceil(len(line) / per_line))
            for runs in paragraphs
            for line in "".join(t for t, _ in runs).split("\n")
        )
        if lines * size * 1.2 / 72 + len(paragraphs) * 3 / 72 <= height_in:
            return size
    return _SIZES[-1]


def _item(label: str, value: str, *, underline: bool = True) -> list[list[_Run]]:
    shown: _Run = (value, {}) if value else ("Not provided", {"italic": True, "color": _MUTED})
    return [[(label, {"bold": True, "underline": underline})], [shown]]


def _panel(slide, n: int, title: str, colour: str, x, y, w, h, body: list[list[_Run]]) -> None:
    shape = _box(
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        x,
        y,
        w,
        h,
        fill=_PANEL,
        line=colour,
        line_pt=2,
        radius=0.1,
        name=f"Panel {n}: {title}",
    )
    frame = shape.text_frame
    frame.margin_left = frame.margin_right = Inches(0.2)
    frame.margin_top, frame.margin_bottom = Inches(0.12), Inches(0.1)
    frame.vertical_anchor = MSO_ANCHOR.TOP
    heading: list[_Run] = [(f"{n}. {title}", {"bold": True, "color": colour})]
    paragraphs = [heading, *body]
    size = _fit(paragraphs, w - 0.4, h - 0.22)
    paragraphs[0] = [(heading[0][0], {"bold": True, "color": colour, "size": min(size + 2, 13)})]
    _write(frame, paragraphs, size)


def _label(
    slide,
    x,
    y,
    w,
    h,
    runs: Sequence[Sequence[_Run]],
    size: float,
    *,
    align=PP_ALIGN.LEFT,
    name="text",
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    box.name = name
    box.text_frame.margin_left = box.text_frame.margin_right = 0
    box.text_frame.margin_top = box.text_frame.margin_bottom = 0
    _write(box.text_frame, runs, size, gap_pt=1, align=align)


def render_canvas_pptx(fields: dict[str, Any]) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)  # standard 16:9
    prs.core_properties.title = "AI Opportunity Canvas"
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(_BG)

    t = {
        k: _text(fields, k)
        for k in (
            "solutionName",
            "team",
            "priority",
            "currentProcess",
            "painPoints",
            "businessImpact",
            "futureState",
            "achieve",
            "success",
            "kpis",
            "users",
            "decision",
            "data",
            "systems",
            "risks",
        )
    }
    types = (
        fields.get("opportunityTypes") if isinstance(fields.get("opportunityTypes"), list) else []
    )

    # Header
    _label(
        slide,
        0.4,
        0.22,
        8.0,
        0.7,
        [[("AI", {"color": _AI}), (" OPPORTUNITY ", {}), ("CANVAS", {"color": _CANVAS})]],
        30,
        name="Title",
    )
    for run in slide.shapes[-1].text_frame.paragraphs[0].runs:
        run.font.bold = True
    _label(
        slide,
        0.4,
        0.92,
        8.0,
        0.35,
        [
            [
                (
                    "From Business Problem to Prototype - One Idea, one page, ready to ",
                    {"color": _SOFT, "bold": True},
                ),
                ("build", {"color": _AI, "bold": True}),
            ]
        ],
        12,
        name="Subtitle",
    )
    _label(
        slide,
        9.0,
        0.2,
        3.93,
        0.5,
        [[("BHP", {"color": _ORANGE, "bold": True, "size": 24})]],
        24,
        align=PP_ALIGN.RIGHT,
        name="BHP",
    )
    meta = [
        [("Solution Name: ", {}), (t["solutionName"] or "-", {"underline": True})],
        [("Team: ", {}), (t["team"] or "-", {"underline": True})],
        [
            (
                " ".join(
                    ["Priority:"] + [("■ " if p == t["priority"] else "□ ") + p for p in PRIORITIES]
                ),
                {"size": 10},
            )
        ],
    ]
    _label(slide, 7.4, 0.68, 5.53, 0.8, meta, 12, align=PP_ALIGN.RIGHT, name="Solution details")

    # Five panels, laid out like the one-pager: three across, then two.
    left, gap, top1, h1, top2, h2 = 0.4, 0.2, 1.55, 2.78, 4.5, 2.35
    w3 = (13.333 - 2 * left - 2 * gap) / 3
    w2 = (13.333 - 2 * left - gap) / 2
    _panel(
        slide,
        1,
        "CURRENT STATE",
        _ORANGE,
        left,
        top1,
        w3,
        h1,
        [
            *_item("Current Process", t["currentProcess"]),
            *_item("Pain Points", t["painPoints"]),
            *_item("Business Impact", t["businessImpact"]),
        ],
    )
    checks = [[(("■ " if name in types else "□ ") + name, {})] for name in OPPORTUNITY_TYPES]
    _panel(
        slide,
        2,
        "AI OPPORTUNITY",
        _PURPLE,
        left + w3 + gap,
        top1,
        w3,
        h1,
        [*checks, *_item("Future State Vision", t["futureState"])],
    )
    _panel(
        slide,
        3,
        "SOLUTION CONCEPT",
        _BLUE,
        left + 2 * (w3 + gap),
        top1,
        w3,
        h1,
        [
            *_item("What do we want to achieve through AI?", t["achieve"]),
            *_item("What does success look like?", t["success"]),
        ],
    )
    _panel(
        slide,
        4,
        "VALUE & SUCCESS",
        _GREEN,
        left,
        top2,
        w2,
        h2,
        _item("Expected Value/KPIs", t["kpis"], underline=False),
    )
    _panel(
        slide,
        5,
        "KEY ASSUMPTIONS & QUESTIONS",
        _ORANGE,
        left + w2 + gap,
        top2,
        w2,
        h2,
        [
            *_item("Who will use this solution?", t["users"], underline=False),
            *_item("What decision will it improve?", t["decision"], underline=False),
            *_item("What data is needed?", t["data"], underline=False),
            *_item("What systems must connect?", t["systems"], underline=False),
            *_item("Key risks and dependencies?", t["risks"], underline=False),
        ],
    )

    # Footer
    _box(slide, MSO_SHAPE.RECTANGLE, 0, 7.0, 13.333, 0.5, fill=_FOOTER, name="Footer")
    _label(
        slide,
        0.4,
        7.13,
        3.6,
        0.3,
        [
            [
                ("One idea. One page. Ready to ", {}),
                ("build", {"italic": True, "color": _AI}),
                (".", {}),
            ]
        ],
        11,
        name="Tagline",
    )
    steps: list[_Run] = []
    for index, (step, what) in enumerate(_STEPS):
        steps += [
            ("  →  " if index else "", {"color": "5B6475", "size": 8}),
            (step, {"bold": True, "size": 9}),
            (f" {what}", {"color": "C9D1D9", "size": 8}),
        ]
    _label(slide, 3.7, 7.17, 7.1, 0.3, [steps], 8, align=PP_ALIGN.CENTER, name="Steps")
    badge = _box(
        slide,
        MSO_SHAPE.ROUNDED_RECTANGLE,
        10.9,
        7.06,
        2.03,
        0.38,
        fill="09151E",
        line="3A3A7A",
        radius=0.2,
        name="Badge",
    )
    badge.text_frame.margin_top = badge.text_frame.margin_bottom = 0
    _write(
        badge.text_frame,
        [
            [
                ("AI LAB  ", {"bold": True, "color": _AI, "size": 9}),
                ("SOLUTION ONE PAGER", {"size": 6.5}),
            ]
        ],
        9,
        gap_pt=0,
        align=PP_ALIGN.CENTER,
    )

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


# --- the same canvas as a PDF: one A4 landscape page, drawn by the server ---------------------

_MM_PT = 0.3528  # millimetres per point
_PDF_SIZES = (11, 10.5, 10, 9.5, 9, 8.5, 8, 7.5, 7, 6.5, 6, 5.5)
_Para = tuple[str, str]  # (text, kind): label | label_plain | value | empty | check_on | check_off


def _rgbt(hex_colour: str) -> tuple[int, int, int]:
    return int(hex_colour[0:2], 16), int(hex_colour[2:4], 16), int(hex_colour[4:6], 16)


def _paragraphs(items: Sequence[tuple[str, str, bool]]) -> list[_Para]:
    out: list[_Para] = []
    for label, value, underline in items:
        out.append((label, "label" if underline else "label_plain"))
        out.append((value, "value") if value else ("Not provided", "empty"))
    return out


def _style(pdf: FPDF, kind: str, size: float) -> None:
    bold = kind in ("label", "label_plain")
    pdf.set_font("DejaVu", ("B" if bold else "") + ("U" if kind == "label" else ""), size)
    colour = _MUTED if kind == "empty" else _WHITE
    pdf.set_text_color(*_rgbt(colour))


def _lines(pdf: FPDF, text: str, width: float, line_h: float) -> int:
    return len(pdf.multi_cell(width, line_h, text, dry_run=True, output="LINES", align="L")) or 1


def _need(pdf: FPDF, paras: Sequence[_Para], width: float, size: float) -> float:
    line_h = size * _MM_PT * 1.3
    total = 0.0
    for text, kind in paras:
        _style(pdf, kind, size)
        total += _lines(pdf, text, width, line_h) * line_h + (
            0.6 if kind.startswith("check") else 1.0
        )
    return total


def _fit_paras(
    pdf: FPDF, paras: list[_Para], width: float, height: float
) -> tuple[list[_Para], float]:
    """The largest size at which everything fits; failing that, the smallest size with the longest
    answers cut short (marked with an ellipsis; the whole answer is in the appendix)."""
    for size in _PDF_SIZES:
        if _need(pdf, paras, width, size) <= height:
            return paras, size
    size, keep = _PDF_SIZES[-1], 1.0
    while keep > 0.1:
        keep -= 0.1
        cut = [
            (shorten(t, max(20, int(len(t) * keep))) if k == "value" else t, k) for t, k in paras
        ]
        if _need(pdf, cut, width, size) <= height:
            return cut, size
    return cut, size


def _pdf_panel(
    pdf: FPDF,
    n: int,
    title: str,
    colour: str,
    x: float,
    y: float,
    w: float,
    h: float,
    paras: list[_Para],
) -> None:
    pdf.set_fill_color(*_rgbt(_PANEL))
    pdf.set_draw_color(*_rgbt(colour))
    pdf.set_line_width(0.7)
    pdf.rect(x, y, w, h, style="DF", round_corners=True, corner_radius=6)
    pad = 4.5
    pdf.set_font("DejaVu", "B", 11)
    pdf.set_text_color(*_rgbt(colour))
    pdf.set_xy(x + pad, y + 3.5)
    pdf.cell(w - 2 * pad, 6, f"{n}. {title}")
    inner, top = w - 2 * pad, y + 11
    paras, size = _fit_paras(pdf, paras, inner, h - 11 - 3)
    line_h = size * _MM_PT * 1.3
    pdf.set_xy(x + pad, top)
    for text, kind in paras:
        _style(pdf, kind, size)
        pdf.set_x(x + pad)
        pdf.multi_cell(inner, line_h, text, new_x="LEFT", new_y="NEXT", align="L")
        pdf.ln(0.6 if kind.startswith("check") else 1.0)


def render_canvas_pdf(fields: dict[str, Any]) -> bytes:
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_font("DejaVu", "", str(_FONTS / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(_FONTS / "DejaVuSans-Bold.ttf"))
    pdf.set_title("AI Opportunity Canvas")
    pdf.add_page()
    pdf.set_fill_color(*_rgbt(_BG))
    pdf.rect(0, 0, 297, 210, style="F")

    t = {
        k: _text(fields, k)
        for k in (
            "solutionName",
            "team",
            "priority",
            "currentProcess",
            "painPoints",
            "businessImpact",
            "futureState",
            "achieve",
            "success",
            "kpis",
            "users",
            "decision",
            "data",
            "systems",
            "risks",
        )
    }
    types = (
        fields.get("opportunityTypes") if isinstance(fields.get("opportunityTypes"), list) else []
    )

    # Header
    pdf.set_xy(10, 8)
    pdf.set_font("DejaVu", "B", 24)
    for text, colour in (("AI", _AI), (" OPPORTUNITY ", _WHITE), ("CANVAS", _CANVAS)):
        pdf.set_text_color(*_rgbt(colour))
        pdf.write(11, text)
    pdf.set_xy(10, 20)
    pdf.set_font("DejaVu", "B", 9.5)
    pdf.set_text_color(*_rgbt(_SOFT))
    pdf.write(5, "From Business Problem to Prototype - One Idea, one page, ready to ")
    pdf.set_text_color(*_rgbt(_AI))
    pdf.write(5, "build")
    pdf.set_font("DejaVu", "B", 20)
    pdf.set_text_color(*_rgbt(_ORANGE))
    pdf.set_xy(197, 6)
    pdf.cell(90, 9, "BHP", align="R")
    pdf.set_font("DejaVu", "", 10.5)
    pdf.set_text_color(*_rgbt(_WHITE))
    for row, (label, value) in enumerate(
        (("Solution Name: ", t["solutionName"]), ("Team: ", t["team"]))
    ):
        pdf.set_xy(137, 15 + row * 5.6)
        pdf.cell(150, 5.4, label + (shorten(value, 60) if value else "-"), align="R")
    pdf.set_font("DejaVu", "", 8.5)
    marks = "  ".join(
        ("■ " if p == t["priority"] else "□ ") + p for p in ("Low", "Medium", "High", "Strategic")
    )
    pdf.set_xy(137, 27)
    pdf.cell(150, 5, "Priority:  " + marks, align="R")

    # Five panels: three across, then two
    left, gap = 8.0, 5.0
    w3, w2 = (297 - 2 * left - 2 * gap) / 3, (297 - 2 * left - gap) / 2
    y1, h1, y2, h2 = 36.0, 76.0, 117.0, 74.0
    _pdf_panel(
        pdf,
        1,
        "CURRENT STATE",
        _ORANGE,
        left,
        y1,
        w3,
        h1,
        _paragraphs(
            [
                ("Current Process", t["currentProcess"], True),
                ("Pain Points", t["painPoints"], True),
                ("Business Impact", t["businessImpact"], True),
            ]
        ),
    )
    checks: list[_Para] = [
        (("■ " if n in types else "□ ") + n, "check_on" if n in types else "check_off")
        for n in OPPORTUNITY_TYPES
    ]
    _pdf_panel(
        pdf,
        2,
        "AI OPPORTUNITY",
        _PURPLE,
        left + w3 + gap,
        y1,
        w3,
        h1,
        checks + _paragraphs([("Future State Vision", t["futureState"], True)]),
    )
    _pdf_panel(
        pdf,
        3,
        "SOLUTION CONCEPT",
        _BLUE,
        left + 2 * (w3 + gap),
        y1,
        w3,
        h1,
        _paragraphs(
            [
                ("What do we want to achieve through AI?", t["achieve"], True),
                ("What does success look like?", t["success"], True),
            ]
        ),
    )
    _pdf_panel(
        pdf,
        4,
        "VALUE & SUCCESS",
        _GREEN,
        left,
        y2,
        w2,
        h2,
        _paragraphs([("Expected Value/KPIs", t["kpis"], False)]),
    )
    _pdf_panel(
        pdf,
        5,
        "KEY ASSUMPTIONS & QUESTIONS",
        _ORANGE,
        left + w2 + gap,
        y2,
        w2,
        h2,
        _paragraphs(
            [
                ("Who will use this solution?", t["users"], False),
                ("What decision will it improve?", t["decision"], False),
                ("What data is needed?", t["data"], False),
                ("What systems must connect?", t["systems"], False),
                ("Key risks and dependencies?", t["risks"], False),
            ]
        ),
    )

    # Footer
    pdf.set_fill_color(*_rgbt(_FOOTER))
    pdf.rect(0, 195, 297, 15, style="F")
    pdf.set_xy(10, 200)
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(*_rgbt(_WHITE))
    pdf.write(5, "One idea. One page. Ready to ")
    pdf.set_text_color(*_rgbt(_AI))
    pdf.write(5, "build")
    pdf.set_text_color(*_rgbt(_WHITE))
    pdf.write(5, ".")
    pdf.set_xy(88, 200.4)
    for index, (step, what) in enumerate(_STEPS):
        if index:
            pdf.set_font("DejaVu", "", 7)
            pdf.set_text_color(*_rgbt("5B6475"))
            pdf.write(4.4, "  →  ")
        pdf.set_font("DejaVu", "B", 6.5)
        pdf.set_text_color(*_rgbt(_WHITE))
        pdf.write(4.4, step)
        pdf.set_font("DejaVu", "", 5.8)
        pdf.set_text_color(*_rgbt("C9D1D9"))
        pdf.write(4.4, f" {what}")
    pdf.set_fill_color(*_rgbt("09151E"))
    pdf.set_draw_color(*_rgbt("3A3A7A"))
    pdf.set_line_width(0.3)
    pdf.rect(251, 197.5, 38, 10.5, style="DF", round_corners=True, corner_radius=3)
    pdf.set_xy(251, 199.2)
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_text_color(*_rgbt(_AI))
    pdf.cell(38, 3.6, "AI LAB", align="C")
    pdf.set_xy(251, 203.2)
    pdf.set_font("DejaVu", "", 5.5)
    pdf.set_text_color(*_rgbt(_WHITE))
    pdf.cell(38, 3, "SOLUTION ONE PAGER", align="C")
    return bytes(pdf.output())


def safe_filename_stem(solution_name: str) -> str:
    """A short, readable, ASCII-only file name stem from the solution name, or a fixed fallback."""
    ascii_name = unicodedata.normalize("NFKD", solution_name).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40].strip("-")
    return stem or "plan-on-a-page"


__all__ = [
    "PDF_MEDIA_TYPE",
    "PPTX_MEDIA_TYPE",
    "render_canvas_pdf",
    "render_canvas_pptx",
    "safe_filename_stem",
]
