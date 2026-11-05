from pathlib import Path

from fpdf import FPDF

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

_FONTS = Path(__file__).parent / "fonts"
_FAMILY = "DejaVu"
_INK, _MUTED, _ACCENT = (30, 30, 30), (95, 100, 112), (200, 80, 30)
_BODY, _LINE = 10.5, 5.6  # readable text size and line height (mm)


class _Document(FPDF):
    def __init__(self, revision: int, app_name: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.revision, self.app_name = revision, app_name
        self.set_margins(18, 18, 18)
        self.set_auto_page_break(auto=True, margin=20)
        self.add_font(_FAMILY, "", str(_FONTS / "DejaVuSans.ttf"))
        self.add_font(_FAMILY, "B", str(_FONTS / "DejaVuSans-Bold.ttf"))
        self.set_title(f"{app_name}: Appendix")
        self.alias_nb_pages()

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font(_FAMILY, "", 8)
        self.set_text_color(*_MUTED)
        self.cell(0, 6, f"{self.app_name}: Appendix · revision {self.revision}", align="L")
        self.ln(9)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font(_FAMILY, "", 8)
        self.set_text_color(*_MUTED)
        self.cell(0, 6, f"Page {self.page_no()} of {{nb}}", align="C")

    def need(self, mm: float) -> None:
        """Start a new page rather than leave a heading alone at the bottom of one."""
        if self.get_y() + mm > self.h - self.b_margin:
            self.add_page()

    def text_block(
        self, text: str, *, size: float = _BODY, bold: bool = False, colour=_INK
    ) -> None:
        self.set_font(_FAMILY, "B" if bold else "", size)
        self.set_text_color(*colour)
        # Words wrap at spaces; a token longer than a line is broken, never run off the page.
        self.multi_cell(0, _LINE, text, new_x="LMARGIN", new_y="NEXT", align="L", wrapmode="WORD")


def render_pdf(appendix: AppendixInput) -> bytes:
    pdf = _Document(appendix.source_revision, appendix.app_name)
    pdf.add_page()
    pdf.text_block(f"{appendix.app_name}: Appendix", size=20, bold=True)
    pdf.text_block(INTRO, size=11, colour=_MUTED)
    pdf.text_block(
        f"Source revision {appendix.source_revision} · "
        f"{appendix.included_turns} conversation turns",
        size=9,
        colour=_MUTED,
    )
    pdf.text_block(SNAPSHOT_NOTE, size=9, colour=_MUTED)
    pdf.ln(4)

    pdf.text_block("Summary", size=15, bold=True, colour=_ACCENT)
    pdf.ln(1)
    if appendix.summary.overview:
        pdf.text_block(OVERVIEW_LABEL, bold=True)
        pdf.text_block(appendix.summary.overview)
        pdf.text_block(OVERVIEW_NOTE, size=9, colour=_MUTED)
    else:
        pdf.text_block(OVERVIEW_UNAVAILABLE, size=9, colour=_MUTED)
    pdf.ln(2)
    pdf.text_block(SUMMARY_NOTE, size=9, colour=_MUTED)
    pdf.ln(1)
    for label, text in appendix.summary.facts:
        pdf.need(20)
        pdf.ln(1)
        pdf.text_block(label, bold=True)
        pdf.text_block(text)
    pdf.ln(2)
    pdf.need(30)
    pdf.text_block("Key answers", size=12, bold=True, colour=_ACCENT)
    for label, text in appendix.summary.key_answers:
        pdf.need(16)
        pdf.ln(1)
        pdf.text_block(label, bold=True)
        pdf.text_block(text)
    pdf.add_page()

    pdf.text_block("Part A. Full answers", size=15, bold=True, colour=_ACCENT)
    pdf.ln(1)
    for section in appendix.sections:
        pdf.need(30)
        pdf.ln(2)
        pdf.text_block(section.title, size=12, bold=True, colour=_ACCENT)
        for answer in section.answers:
            pdf.need(24)
            pdf.ln(1)
            pdf.text_block(answer.label, bold=True)
            omitted = answer.value is None
            pdf.text_block(display_value(answer), colour=_MUTED if omitted else _INK)
    pdf.ln(4)

    pdf.need(40)
    pdf.text_block("Part B. Conversation record", size=15, bold=True, colour=_ACCENT)
    pdf.text_block(RECORD_NOTE, size=9, colour=_MUTED)
    pdf.ln(1)
    for turn in appendix.turns:
        pdf.need(22)
        pdf.ln(2)
        pdf.text_block(
            f"Turn {turn.number} · {SPEAKER_LABELS[turn.speaker]}", bold=True, colour=_ACCENT
        )
        pdf.text_block(turn.text)
    return bytes(pdf.output())
