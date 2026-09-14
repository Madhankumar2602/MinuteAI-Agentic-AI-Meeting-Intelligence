"""Render Minutes of Meeting as a professional A4 PDF (ReportLab).

Pure function of a ``MinutesOfMeeting``: no database, no storage, no network.

    cover block   title · date/time · participants · input · agenda
    1  Executive summary          6  Action items (owner, deadline, status)
    2  Key discussion points      7  Pending / unresolved items
    3  Keywords                   8  Next steps
    4  Participants & speakers    9  Items needing review
    5  Decisions                  10 Source & transcript reference (evidence)
    footer on every page: title · "Page n of N" · generated time

Safety: every piece of text comes from users or the model, and ReportLab
paragraphs interpret a small XML markup. All text is escaped before it is
placed in a paragraph, so a transcript containing ``<font>`` or ``&`` renders
literally instead of breaking or restyling the document (tested).

Fonts: DejaVu Sans is bundled (``fonts/``, free licence) because the PDF base
fonts cover only Latin-1. Names like "Łukasz" or "Ζωή" and symbols like "₹" render
correctly. Scripts DejaVu does not cover (e.g. CJK, Devanagari) show as empty
boxes; that is a known limitation, documented in ADR 0013.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.schemas.mom import MinutesOfMeeting

RENDERER_VERSION = "mom-pdf-v1"

_FONT_DIR = Path(__file__).parent / "fonts"
_FONT = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"
_FONT_ITALIC = "DejaVuSans-Oblique"

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#475569")
FAINT = colors.HexColor("#94a3b8")
RULE = colors.HexColor("#e2e8f0")
BRAND = colors.HexColor("#4f46e5")
HEAD_BG = colors.HexColor("#f1f5f9")
WARN = colors.HexColor("#b45309")
WARN_SOFT = colors.HexColor("#fffbeb")

_STATUS_LABEL = {
    "pending": "Pending",
    "in_progress": "In progress",
    "done": "Done",
    "cancelled": "Cancelled",
    "open": "Open",
    "resolved": "Resolved",
    "superseded": "Superseded",
}
_INPUT_LABEL = {"transcript": "Transcript", "notes": "Meeting notes", "recording": "Recording"}


def _register_fonts() -> None:
    if _FONT in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(_FONT, str(_FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(_FONT_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont(_FONT_ITALIC, str(_FONT_DIR / "DejaVuSans-Oblique.ttf")))
    pdfmetrics.registerFontFamily(
        _FONT, normal=_FONT, bold=_FONT_BOLD, italic=_FONT_ITALIC, boldItalic=_FONT_BOLD
    )


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=_FONT, fontSize=9.5, leading=13.5, textColor=INK)
    return {
        "body": base,
        "muted": ParagraphStyle("muted", parent=base, textColor=MUTED),
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=11, textColor=MUTED),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11.5),
        "cell_head": ParagraphStyle(
            "cell_head", parent=base, fontName=_FONT_BOLD, fontSize=8, leading=11, textColor=MUTED
        ),
        "eyebrow": ParagraphStyle(
            "eyebrow", parent=base, fontName=_FONT_BOLD, fontSize=8, leading=10, textColor=BRAND
        ),
        "title": ParagraphStyle(
            "title", parent=base, fontName=_FONT_BOLD, fontSize=20, leading=25, spaceAfter=2
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName=_FONT_BOLD,
            fontSize=12,
            leading=16,
            textColor=INK,
            spaceBefore=14,
            spaceAfter=6,
            alignment=TA_LEFT,
            keepWithNext=True,  # never leave a heading alone at a page end
        ),
        "quote": ParagraphStyle(
            "quote", parent=base, fontName=_FONT_ITALIC, fontSize=8, leading=11, textColor=MUTED
        ),
    }


def _t(value: object) -> str:
    """Escape text for a ReportLab paragraph, keeping line breaks."""
    return escape(str(value)).replace("\n", "<br/>")


class _NumberedCanvas(rl_canvas.Canvas):
    """Draws the footer after layout, when the total page count is known."""

    def __init__(self, *args, footer_left: str, generated: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved: list[dict] = []
        self._footer_left = footer_left
        self._generated = generated

    def showPage(self) -> None:  # noqa: N802 - ReportLab API
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._footer(total)
            super().showPage()
        super().save()

    def _footer(self, total: int) -> None:
        width, _ = A4
        self.setStrokeColor(RULE)
        self.setLineWidth(0.6)
        self.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
        self.setFont(_FONT, 7.5)
        self.setFillColor(FAINT)
        self.drawString(18 * mm, 9.5 * mm, self._footer_left)
        self.drawCentredString(width / 2, 9.5 * mm, self._generated)
        self.drawRightString(width - 18 * mm, 9.5 * mm, f"Page {self._pageNumber} of {total}")


def _table(rows: list[list], widths: list[float], s: dict[str, ParagraphStyle]) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _bullets(items: list[str], s: dict[str, ParagraphStyle]) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(_t(i), s["body"]), leftIndent=12) for i in items],
        bulletType="bullet",
        start="•",
        bulletFontName=_FONT,
        bulletColor=BRAND,
        leftIndent=12,
    )


def render_minutes_pdf(
    minutes: MinutesOfMeeting, *, generated_at: datetime | None = None
) -> tuple[bytes, int]:
    """Return (PDF bytes, page count)."""
    _register_fonts()
    s = _styles()
    generated_at = generated_at or datetime.now(UTC)
    content_width = A4[0] - 36 * mm
    story: list = []

    # ---- cover block -----------------------------------------------------
    when = minutes.meeting_date.astimezone(UTC)
    meta_rows = [
        ["Date & time", when.strftime("%A, %d %B %Y · %H:%M UTC")],
        ["Participants", ", ".join(minutes.participants) or "Not recorded"],
        [
            "Input",
            _INPUT_LABEL[minutes.source.input_kind]
            + (
                f" · {minutes.source.recording_filename}"
                if minutes.source.recording_filename
                else ""
            ),
        ],
    ]
    if minutes.keywords:
        meta_rows.append(["Topics", " · ".join(minutes.keywords)])
    header = [
        Paragraph("MINUTES OF MEETING", s["eyebrow"]),
        Spacer(1, 3),
        Paragraph(_t(minutes.title), s["title"]),
        Spacer(1, 6),
    ]
    meta = Table(
        [[Paragraph(_t(k), s["cell_head"]), Paragraph(_t(v), s["cell"])] for k, v in meta_rows],
        colWidths=[30 * mm, content_width - 30 * mm],
    )
    meta.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story += [*header, meta]
    if minutes.is_stale:
        story += [
            Spacer(1, 8),
            _callout("The transcript changed after these minutes were produced.", s),
        ]
    if minutes.agenda:
        story += [
            Paragraph("Agenda / description", s["h2"]),
            Paragraph(_t(minutes.agenda), s["muted"]),
        ]

    section = 0

    def heading(text: str) -> Paragraph:
        nonlocal section
        section += 1
        return Paragraph(f"{section}. {_t(text)}", s["h2"])

    # ---- 1. summary, 2. points ------------------------------------------
    story += [heading("Executive summary"), Paragraph(_t(minutes.executive_summary), s["body"])]
    if minutes.key_points:
        story += [heading("Key discussion points"), _bullets(minutes.key_points, s)]

    # ---- speakers ---------------------------------------------------------
    if minutes.speakers:
        rows = [
            [Paragraph(h, s["cell_head"]) for h in ("Speaker", "Contribution", "Turns", "Share")]
        ]
        for sp in minutes.speakers:
            rows.append(
                [
                    Paragraph(f"<b>{_t(sp.name)}</b>", s["cell"]),
                    Paragraph(_t(sp.contribution or "—"), s["cell"]),
                    Paragraph(str(sp.turns) if sp.turns else "—", s["cell"]),
                    Paragraph(f"{sp.share:.0%}" if sp.words else "—", s["cell"]),
                ]
            )
        story += [
            heading("Participants & speaker contributions"),
            _table(rows, [32 * mm, content_width - 32 * mm - 36 * mm, 16 * mm, 20 * mm], s),
        ]

    # ---- decisions --------------------------------------------------------
    story.append(heading("Decisions"))
    if minutes.decisions:
        rows = [[Paragraph(h, s["cell_head"]) for h in ("#", "Decision", "Status")]]
        for d in minutes.decisions:
            text = f"<b>{_t(d.text)}</b>"
            if d.context:
                text += f"<br/><font color='#475569'>{_t(d.context)}</font>"
            rows.append(
                [
                    Paragraph(str(d.number), s["cell"]),
                    Paragraph(text, s["cell"]),
                    Paragraph(_STATUS_LABEL[d.status.value], s["cell"]),
                ]
            )
        story.append(_table(rows, [9 * mm, content_width - 9 * mm - 26 * mm, 26 * mm], s))
    else:
        story.append(Paragraph("No decisions were recorded.", s["muted"]))

    # ---- action items -----------------------------------------------------
    story.append(heading("Action items"))
    if minutes.action_items:
        head = ("#", "Task", "Owner", "Deadline", "Status")
        rows = [[Paragraph(h, s["cell_head"]) for h in head]]
        for a in minutes.action_items:
            task = _t(a.task)
            if a.priority and a.priority.value == "high":
                task += " <font color='#b45309'>(high priority)</font>"
            deadline = a.deadline.strftime("%d %b %Y") if a.deadline else "—"
            if a.deadline_text:
                deadline += f"<br/><font color='#94a3b8'>“{_t(a.deadline_text)}”</font>"
            rows.append(
                [
                    Paragraph(str(a.number), s["cell"]),
                    Paragraph(task, s["cell"]),
                    Paragraph(
                        _t(a.owner) if a.owner else "<font color='#b45309'>Unassigned</font>",
                        s["cell"],
                    ),
                    Paragraph(deadline, s["cell"]),
                    Paragraph(_STATUS_LABEL[a.status.value], s["cell"]),
                ]
            )
        story.append(
            _table(
                rows,
                [
                    9 * mm,
                    content_width - 9 * mm - 30 * mm - 30 * mm - 22 * mm,
                    30 * mm,
                    30 * mm,
                    22 * mm,
                ],
                s,
            )
        )
    else:
        story.append(Paragraph("No action items were recorded.", s["muted"]))

    # ---- pending, next steps ------------------------------------------------
    story.append(heading("Pending & unresolved items"))
    if minutes.pending_items:
        story.append(_bullets([p.item for p in minutes.pending_items], s))
    else:
        story.append(Paragraph("Nothing was left open.", s["muted"]))

    story.append(heading("Next steps"))
    if minutes.next_steps:
        story.append(_bullets(minutes.next_steps, s))
        if minutes.next_steps_derived:
            story.append(Paragraph("Derived from the open action items.", s["small"]))
    else:
        story.append(Paragraph("No next steps were recorded.", s["muted"]))

    # ---- review flags -------------------------------------------------------
    if minutes.review_flags:
        story += [
            heading("Items needing review"),
            _callout(
                "<br/>".join(f"• {_t(f.message)}" for f in minutes.review_flags), s, escaped=True
            ),
        ]

    # ---- source & evidence ----------------------------------------------------
    src = minutes.source
    source_rows = [
        ["Input", _INPUT_LABEL[src.input_kind]],
        [
            "Transcript",
            f"{src.transcript_words:,} words"
            + (f" · {src.language}" if src.language else "")
            + (
                f" · {src.duration_seconds // 60} min {src.duration_seconds % 60} s"
                if src.duration_seconds
                else ""
            ),
        ],
        ["Transcript SHA-256", src.transcript_sha256],
        [
            "Extraction",
            f"{src.extraction_provider} {src.extraction_model} · prompt {src.prompt_version}",
        ],
        ["Extracted", src.extracted_at.astimezone(UTC).strftime("%d %b %Y %H:%M UTC")],
        [
            "Evidence",
            f"{src.evidence_verified} of {src.evidence_total} items verified against the input",
        ],
    ]
    if src.transcription_model:
        source_rows.insert(2, ["Transcription", src.transcription_model])
    story.append(heading("Source & transcript reference"))
    source_table = Table(
        [[Paragraph(_t(k), s["cell_head"]), Paragraph(_t(v), s["cell"])] for k, v in source_rows],
        colWidths=[34 * mm, content_width - 34 * mm],
    )
    source_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(source_table)

    evidence = (
        [(f"Decision {d.number}", d.evidence_quote, d.evidence_verified) for d in minutes.decisions]
        + [
            (f"Action {a.number}", a.evidence_quote, a.evidence_verified)
            for a in minutes.action_items
        ]
        + [
            (f"Pending {i}", p.evidence_quote, p.evidence_verified)
            for i, p in enumerate(minutes.pending_items, start=1)
        ]
    )
    quoted = [e for e in evidence if e[1]]
    if quoted:
        story.append(Spacer(1, 8))
        story.append(Paragraph("Evidence quoted from the input", s["cell_head"]))
        for label, quote, verified in quoted:
            mark = (
                "<font color='#15803d'>✓ verified</font>"
                if verified
                else "<font color='#b45309'>⚠ not found in input</font>"
            )
            story.append(
                KeepTogether(
                    [
                        Paragraph(f"<b>{_t(label)}</b> · {mark}", s["small"]),
                        Paragraph(f"“{_t(quote)}”", s["quote"]),
                        Spacer(1, 3),
                    ]
                )
            )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=22 * mm,
        title=f"Minutes of Meeting — {minutes.title}",
        author="MinuteAI",
        subject="Minutes of Meeting",
        creator=f"MinuteAI {RENDERER_VERSION}",
    )
    footer_left = f"MinuteAI · {minutes.title}"
    if len(footer_left) > 60:
        footer_left = footer_left[:57] + "…"
    generated = f"Generated {generated_at.astimezone(UTC).strftime('%d %b %Y %H:%M UTC')}"

    def make_canvas(*args, **kwargs):
        return _NumberedCanvas(*args, footer_left=footer_left, generated=generated, **kwargs)

    doc.build(story, canvasmaker=make_canvas)
    data, page_count = buffer.getvalue(), doc.page
    return data, page_count


def _callout(text: str, s: dict[str, ParagraphStyle], *, escaped: bool = False) -> Table:
    box = Table(
        [[Paragraph(text if escaped else _t(text), s["cell"])]], colWidths=[A4[0] - 36 * mm]
    )
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), WARN_SOFT),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, WARN),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return box
