"""
Job Card PDF Generator using ReportLab.
Produces a professional A4 job card for tickets and tasks: e-mailed to billing
on a billable close, and printed from the ticket page at any stage.
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Brand colours – subtle blue/slate palette
BRAND_DARK = colors.HexColor("#1e293b")   # slate-800
BRAND_MID = colors.HexColor("#3b82f6")    # blue-500
BRAND_LIGHT = colors.HexColor("#eff6ff")  # blue-50
BRAND_BORDER = colors.HexColor("#bfdbfe") # blue-200
LABEL_GRAY = colors.HexColor("#64748b")   # slate-500
TEXT_BLACK = colors.HexColor("#0f172a")   # slate-900
ROW_ALT = colors.HexColor("#f8fafc")      # slate-50


def _style(name, **kwargs) -> ParagraphStyle:
    base = getSampleStyleSheet()["Normal"]
    return ParagraphStyle(name, parent=base, **kwargs)


def _text(value) -> str:
    """Plain text for a Paragraph, which otherwise reads <, > and & as markup
    (a subject like "Tom & Jerry <b>" made the whole job card fail)."""
    return escape(str(value)).replace("\n", "<br/>")


def _zone(tz_name: Optional[str]):
    """The company's timezone, or None for UTC (or when it is not known here)."""
    if not tz_name or tz_name == "UTC":
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception:
        return None


def _local(dt: Optional[datetime], zone) -> Optional[datetime]:
    """A stored (UTC) time as the company's local time."""
    if dt is None or zone is None:
        return dt
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(zone).replace(tzinfo=None)


def generate_job_card_pdf(
    ticket_number: str,
    subject: str,
    priority: str,
    created_at: datetime,
    closed_at: Optional[datetime],  # None while the ticket is still open
    technician_name: str,
    # Client details
    client_name: Optional[str] = None,
    client_surname: Optional[str] = None,
    client_phone: Optional[str] = None,
    client_office_number: Optional[str] = None,
    # Billing
    billable_traveling: Optional[str] = None,
    billable_labour_onsite: Optional[str] = None,
    billable_remote_labour: Optional[str] = None,
    billable_sundries: Optional[str] = None,
    # Notes
    additional_notes: Optional[str] = None,
    # Time on the ticket timer, e.g. "3h 05m" (row left out when None)
    time_worked: Optional[str] = None,
    # "sla" when the client's SLA covers the work, so there is nothing to bill
    close_type: Optional[str] = None,
    # The customer's signature captured on the ticket, drawn on the signature line
    signature_png: Optional[bytes] = None,
    signer_name: Optional[str] = None,
    signed_at: Optional[datetime] = None,
    # Company timezone for the times printed (stored times are UTC)
    tz_name: Optional[str] = None,
    # Branding
    company_name: str = "Support Team",
    logo_path: Optional[str] = None,
) -> bytes:
    """Return a PDF job card as bytes."""

    zone = _zone(tz_name)
    created_at, closed_at, signed_at = (_local(t, zone) for t in (created_at, closed_at, signed_at))
    printed_at = _local(datetime.now(timezone.utc), zone).replace(tzinfo=None)
    zone_label = tz_name if zone else "UTC"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
    )

    W = A4[0] - 36 * mm  # usable width

    # ── Styles ──────────────────────────────────────────────────────
    s_company = _style("company", fontSize=18, leading=22, textColor=BRAND_DARK,
                       fontName="Helvetica-Bold")
    s_jobcard_title = _style("jctitle", fontSize=11, leading=14, textColor=BRAND_MID,
                              fontName="Helvetica-Bold", spaceAfter=0)
    s_ticket_no = _style("tktno", fontSize=22, leading=26, textColor=BRAND_DARK,
                          fontName="Helvetica-Bold")
    s_meta = _style("meta", fontSize=8.5, leading=12, textColor=LABEL_GRAY)
    s_section_header = _style("sechdr", fontSize=9, leading=12, textColor=colors.white,
                               fontName="Helvetica-Bold")
    s_label = _style("lbl", fontSize=8.5, leading=12, textColor=LABEL_GRAY)
    s_value = _style("val", fontSize=9.5, leading=13, textColor=TEXT_BLACK,
                     fontName="Helvetica-Bold")
    s_value_normal = _style("valn", fontSize=9.5, leading=13, textColor=TEXT_BLACK)
    s_note = _style("note", fontSize=9, leading=13, textColor=TEXT_BLACK)
    s_footer = _style("footer", fontSize=7.5, leading=10, textColor=LABEL_GRAY,
                      alignment=TA_CENTER)
    s_sig_label = _style("siglbl", fontSize=8, leading=11, textColor=LABEL_GRAY,
                          alignment=TA_CENTER)

    story = []

    # ── Header row: logo + company + ticket number ───────────────────
    left_cells = []
    if logo_path and os.path.isfile(logo_path):
        try:
            img = Image(logo_path)
            img._restrictSize(48 * mm, 18 * mm)
            left_cells.append(img)
        except Exception:
            left_cells.append(Paragraph(_text(company_name), s_company))
    else:
        left_cells.append(Paragraph(_text(company_name), s_company))

    left_cells.append(Spacer(1, 2 * mm))
    left_cells.append(Paragraph("JOB CARD", s_jobcard_title))

    # Closed tickets carry their closing time; an open one, when it was printed
    card_time = closed_at or printed_at
    right_cells = [
        Paragraph(_text(f"#{ticket_number}"), s_ticket_no),
        Spacer(1, 1 * mm),
        Paragraph(f"Date: {card_time.strftime('%d %B %Y')}", s_meta),
        Paragraph(f"Time: {card_time.strftime('%H:%M')}", s_meta),
    ]

    header_table = Table(
        [[left_cells, right_cells]],
        colWidths=[W * 0.6, W * 0.4],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width="100%", thickness=2, color=BRAND_MID, spaceAfter=4 * mm))

    # ── Job details banner ───────────────────────────────────────────
    def section_header(title: str):
        tbl = Table(
            [[Paragraph(title, s_section_header)]],
            colWidths=[W],
        )
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BRAND_DARK),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("ROUNDEDCORNERS", [3, 3, 3, 3]),
        ]))
        return tbl

    def info_row(label: str, value: str, alt: bool = False):
        bg = ROW_ALT if alt else colors.white
        row = Table(
            [[Paragraph(label, s_label), Paragraph(_text(value) if value else "—", s_value_normal)]],
            colWidths=[W * 0.38, W * 0.62],
        )
        row.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
        ]))
        return row

    # Job summary
    story.append(section_header("  JOB SUMMARY"))
    story.append(info_row("Subject / Fault", subject, False))
    story.append(info_row("Ticket Number", f"#{ticket_number}", True))
    story.append(info_row("Priority", priority.title(), False))
    story.append(info_row("Opened", created_at.strftime("%d %b %Y  %H:%M"), True))
    story.append(info_row("Closed", closed_at.strftime("%d %b %Y  %H:%M") if closed_at else "Not closed yet", False))
    story.append(info_row("Technician", technician_name, True))
    if time_worked:
        story.append(info_row("Time Worked", time_worked, False))
    story.append(Spacer(1, 4 * mm))

    # ── Client details ───────────────────────────────────────────────
    story.append(section_header("  CLIENT DETAILS"))
    full_name = " ".join(filter(None, [client_name, client_surname])) or None
    story.append(info_row("Full Name", full_name, False))
    story.append(info_row("Phone Number", client_phone, True))
    story.append(info_row("Office Number", client_office_number, False))
    story.append(Spacer(1, 4 * mm))

    # ── Billable work ────────────────────────────────────────────────
    story.append(section_header("  BILLABLE WORK"))
    if (close_type or "").lower() == "sla":
        story.append(info_row("Billing", "Covered by the client's SLA - nothing to bill", False))
    else:
        story.append(info_row("Traveling", billable_traveling, False))
        story.append(info_row("Labour Onsite", billable_labour_onsite, True))
        story.append(info_row("Remote Labour", billable_remote_labour, False))
        story.append(info_row("Sundries", billable_sundries, True))
    story.append(Spacer(1, 4 * mm))

    # ── Additional notes ─────────────────────────────────────────────
    story.append(section_header("  ADDITIONAL NOTES"))
    notes_text = _text(additional_notes) if additional_notes else "No additional notes."
    notes_tbl = Table(
        [[Paragraph(notes_text, s_note)]],
        colWidths=[W],
    )
    notes_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ROW_ALT),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
    ]))
    story.append(notes_tbl)
    story.append(Spacer(1, 8 * mm))

    # ── Signature lines ──────────────────────────────────────────────
    sig_line = "_" * 32
    client_sig = Paragraph(sig_line, s_sig_label)
    client_label = "Client Signature"
    if signature_png:
        # The signature the customer drew on the ticket, in place of the blank line
        try:
            client_sig = Image(io.BytesIO(signature_png))
            client_sig._restrictSize(W * 0.38, 22 * mm)
            signed = [_text(signer_name)] if signer_name else []
            if signed_at:
                signed.append(signed_at.strftime("%d %b %Y %H:%M"))
            client_label = "Client Signature" + ("<br/>" + "  ·  ".join(signed) if signed else "")
        except Exception:
            client_sig = Paragraph(sig_line, s_sig_label)
    sig_data = [
        [
            client_sig,
            Paragraph("", s_sig_label),
            Paragraph(sig_line, s_sig_label),
        ],
        [
            Paragraph(client_label, s_sig_label),
            Paragraph("", s_sig_label),
            Paragraph("Technician Signature", s_sig_label),
        ],
    ]
    sig_table = Table(sig_data, colWidths=[W * 0.4, W * 0.2, W * 0.4])
    sig_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, 0), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(sig_table)
    story.append(Spacer(1, 6 * mm))

    # ── Footer ───────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=BRAND_BORDER, spaceAfter=3 * mm))
    story.append(Paragraph(
        f"{_text(company_name)}  ·  Job Card generated {printed_at.strftime('%d %b %Y %H:%M')} {_text(zone_label)}",
        s_footer,
    ))

    doc.build(story)
    return buf.getvalue()
