"""Job cards print from the ticket page for everyone, and never fail on what a ticket holds."""
from __future__ import annotations

import io
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

pytest.importorskip('reportlab', reason='reportlab (requirements.txt) creates the PDFs')

from app.core.job_card_pdf import generate_job_card_pdf  # noqa: E402
from app.models.ticket import Ticket, TicketSignature  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.workspace import Workspace  # noqa: E402
from app.web.routes import _ticket_job_card_pdf, web_ticket_job_card  # noqa: E402

BASE = dict(ticket_number='TKT-2026-00001', subject='Printer offline', priority='high',
            created_at=datetime(2026, 10, 14, 7, 5), technician_name='Johnny')


def _pdf_text(pdf: bytes) -> str:
    fitz = pytest.importorskip('fitz', reason='PyMuPDF reads the text back out of the PDF')
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        return ' '.join(' '.join(page.get_text().split()) for page in doc)


def _signature_png() -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (300, 100), 'white')
    ImageDraw.Draw(img).line([(10, 80), (60, 20), (120, 70), (290, 30)], fill='black', width=4)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


@pytest.mark.parametrize('changes', [
    {'closed_at': None},                                          # still open
    {'closed_at': datetime(2026, 10, 14, 9), 'close_type': 'sla'},
    {'closed_at': datetime(2026, 10, 14, 9), 'subject': 'Tom & Jerry <b>',
     'additional_notes': 'Line one\nR&D <ok>', 'client_name': 'A <b>'},
    {'closed_at': None, 'signature_png': b'not a png', 'signer_name': 'X'},
], ids=['open', 'sla', 'markup-like-text', 'broken-signature'])
def test_job_card_is_always_created(changes):
    assert generate_job_card_pdf(**{**BASE, **changes}).startswith(b'%PDF')


def test_job_card_contents():
    pdf = generate_job_card_pdf(
        **{**BASE, 'subject': 'Tom & Jerry <b>'}, closed_at=datetime(2026, 10, 14, 13, 40),
        billable_labour_onsite='2h 30m', billable_sundries='Toner x1', time_worked='3h 05m',
        signature_png=_signature_png(), signer_name='Thandi Mokoena', signed_at=datetime(2026, 10, 14, 13, 35),
        tz_name='Africa/Johannesburg', company_name='Kyotech Connect',
    )
    text = _pdf_text(pdf)
    assert 'Tom & Jerry <b>' in text, 'shown as typed, not read as markup'
    assert 'Opened 14 Oct 2026 09:05' in text and 'Closed 14 Oct 2026 15:40' in text, 'company time (UTC+2)'
    assert 'Labour Onsite 2h 30m' in text and 'Sundries Toner x1' in text and 'Time Worked 3h 05m' in text
    assert 'Thandi Mokoena' in text and '14 Oct 2026 15:35' in text


def test_open_and_sla_job_cards_say_so():
    assert 'Closed Not closed yet' in _pdf_text(generate_job_card_pdf(**BASE, closed_at=None))
    sla = _pdf_text(generate_job_card_pdf(**BASE, closed_at=datetime(2026, 10, 14, 9), close_type='sla',
                                          billable_traveling='should not show'))
    assert "Covered by the client's SLA - nothing to bill" in sla and 'should not show' not in sla


# --- printing from the ticket page ------------------------------------------

async def _seed(path):
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name='Acme', timezone='UTC'), Workspace(name='Other Co')
    db.add_all([acme, other])
    await db.flush()
    tech = User(username='tech', hashed_password='x', full_name='Johnathan Smith', display_name='Johnny',
                workspace_id=acme.id)
    closer = User(username='closer', hashed_password='x', full_name='Cara Closer', workspace_id=acme.id)
    outsider = User(username='olive', hashed_password='x', workspace_id=other.id)
    db.add_all([tech, closer, outsider])
    await db.flush()
    ticket = Ticket(ticket_number='TKT-2026-00007', subject='Printer offline', workspace_id=acme.id,
                    assigned_to_id=tech.id, billable_labour_onsite='1h', job_client_name='Thandi')
    db.add(ticket)
    await db.flush()
    db.add(TicketSignature(ticket_id=ticket.id, signer_name='Thandi Mokoena', image_png=_signature_png()))
    await db.commit()
    return engine, db, acme, ticket, tech, closer, outsider


def _request(user):
    return SimpleNamespace(session={'user_id': user.id} if user else {})


@pytest.mark.asyncio
async def test_everyone_in_the_company_can_print_the_job_card(tmp_path):
    engine, db, _, ticket, tech, _, _ = await _seed(tmp_path / 'jc.db')
    try:
        response = await web_ticket_job_card(_request(tech), ticket.id, db=db)  # not an admin
        assert response.status_code == 200
        assert response.media_type == 'application/pdf' and response.body.startswith(b'%PDF')
        assert response.headers['content-disposition'] == 'inline; filename="JobCard_TKT-2026-00007.pdf"'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_job_card_is_not_for_other_companies(tmp_path):
    engine, db, _, ticket, _, _, outsider = await _seed(tmp_path / 'jc.db')
    try:
        other = await web_ticket_job_card(_request(outsider), ticket.id, db=db)
        assert (other.status_code, other.headers['location']) == (303, '/web/tickets')
        signed_out = await web_ticket_job_card(_request(None), ticket.id, db=db)
        assert (signed_out.status_code, signed_out.headers['location']) == (303, '/web/login')
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_job_card_technician_and_contents(tmp_path):
    engine, db, acme, ticket, tech, closer, _ = await _seed(tmp_path / 'jc.db')
    try:
        open_card = _pdf_text(await _ticket_job_card_pdf(db, ticket, acme))
        assert 'Technician Johnny' in open_card, 'open ticket: its assigned technician, by display name'
        assert 'Labour Onsite 1h' in open_card and 'Full Name Thandi' in open_card
        assert 'Thandi Mokoena' in open_card, 'the captured signature is on the card'

        ticket.closed_by_id, ticket.closed_at = closer.id, datetime(2026, 10, 14, 12)
        closed_card = _pdf_text(await _ticket_job_card_pdf(db, ticket, acme))
        assert 'Technician Cara Closer' in closed_card, 'closed ticket: whoever closed it, as on the e-mailed card'
    finally:
        await db.close()
        await engine.dispose()
