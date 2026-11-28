"""Any file type can be uploaded safely, previewed only when it is passive.

The browser is allowed to pick CSV, JSON, archives, scripts, executables and
extensionless files. The server answers by writing every unknown/active type
with a passive ``.bin`` extension under a generated UUID name, keeping the
sanitized original name only as display metadata. Downloads always force
attachment disposition and preview routes never serve active web content.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select
from starlette.datastructures import Headers, QueryParams

from app.models.assignment import Assignment
from app.models.chat import Chat, ChatMember, MessageAttachment
from app.models.comment import Comment
from app.models.comment_attachment import CommentAttachment
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.task_extensions import TaskAttachment
from app.models.ticket import Ticket, TicketAttachment, TicketHistory
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes as web

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / 'app' / 'templates'

CSV = b'sensor,value\n1,42\n'
JSON = b'{"sensor": 42}\n'
CFG = b'set interface=lan0\n'
HTML = b'<html><body><script>alert(1)</script></body></html>'
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'
EXE = b'MZ\x90\x00' + b'\x00' * 48
PNG = b'\x89PNG\r\n\x1a\n' + b'pretend image bytes'
PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n'


@pytest.fixture(autouse=True)
def _legacy_template_call(monkeypatch):
    """Keep this repo's legacy TemplateResponse call shape working."""
    def render(name, context, *args, **kwargs):
        return Jinja2Templates.TemplateResponse(
            web.templates, context.get('request'), name, context, *args, **kwargs
        )

    monkeypatch.setattr(web, '_original_template_response', render)


def _request(user, *, headers=None, form_data=None, path='/web'):
    data = form_data or {}

    async def form():
        return data

    return SimpleNamespace(
        session={'user_id': user.id} if user else {},
        headers=headers or {},
        state=SimpleNamespace(),
        url=SimpleNamespace(path=path, query=''),
        query_params=QueryParams(),
        form=form,
    )


def _upload(filename, data, content_type='application/octet-stream'):
    return UploadFile(
        file=BytesIO(data),
        filename=filename,
        headers=Headers({'content-type': content_type}),
    )


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    import app.models  # noqa: F401  (register every model/table)

    # BASE_DIR points at the app/ folder in production; mirroring that shape
    # keeps 'app/uploads/...' database paths resolvable inside the sandbox.
    app_dir = tmp_path / 'app'
    monkeypatch.setattr(web, 'BASE_DIR', app_dir)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'any_file.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)

    # The guest route deliberately submits into workspace 1.
    acme = Workspace(id=1, name='Acme', timezone='UTC')
    db.add(acme)
    await db.flush()
    ada = User(username='ada', email='ada@acme.test', hashed_password='x',
               is_admin=True, workspace_id=acme.id)
    bob = User(username='bob', email='bob@acme.test', hashed_password='x',
               workspace_id=acme.id)
    db.add_all([ada, bob])
    await db.flush()
    project = Project(name='Office', workspace_id=acme.id, owner_id=ada.id)
    db.add(project)
    await db.flush()
    task = Task(title='Check', project_id=project.id, creator_id=ada.id,
                status=TaskStatus.in_progress)
    db.add(task)
    await db.flush()
    db.add(Assignment(task_id=task.id, assignee_id=ada.id))
    ticket = Ticket(ticket_number='TKT-2026-00001', subject='Printer', workspace_id=acme.id)
    db.add(ticket)
    chat = Chat(workspace_id=acme.id, name='Team', created_by_id=ada.id)
    db.add(chat)
    await db.flush()
    db.add(ChatMember(chat_id=chat.id, user_id=ada.id))
    await db.commit()

    yield SimpleNamespace(engine=engine, db=db, tmp_path=tmp_path, app_dir=app_dir,
                          acme=acme, ada=ada, bob=bob, project=project, task=task,
                          ticket=ticket, chat=chat)
    await db.close()
    await engine.dispose()


# --- Core validation -----------------------------------------------------------

@pytest.mark.parametrize('filename,expected_mime', [
    ('report.csv', 'text/csv'),
    ('data.json', 'application/json'),
    ('notes.txt', 'text/plain'),
    ('paperwork.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    ('sheet.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    ('bundle.zip', 'application/zip'),
    ('config.rsc', 'application/octet-stream'),
    ('program.exe', 'application/octet-stream'),
    ('page.html', 'application/octet-stream'),
    ('picture.svg', 'application/octet-stream'),
    ('script.js', 'application/octet-stream'),
    ('extensionless', 'application/octet-stream'),
])
def test_validate_attachment_accepts_any_type_and_stores_it_as_bin(filename, expected_mime):
    from app.core.attachment_upload import validate_attachment

    assert validate_attachment(CSV, filename, 'text/csv') == (expected_mime, '.bin')


def test_validate_attachment_keeps_verified_photos_and_pdfs():
    from app.core.attachment_upload import validate_attachment

    assert validate_attachment(PNG, 'meter.csv', 'text/csv') == ('image/png', '.png')
    assert validate_attachment(PDF, 'quote.pdf', 'application/pdf') == ('application/pdf', '.pdf')
    assert validate_attachment(PDF, 'quote.csv', 'text/csv') == ('text/csv', '.bin')


def test_validate_attachment_rejects_only_empty_and_oversized_files():
    import app.core.attachment_upload as upload

    assert upload.MAX_ATTACHMENT_BYTES == 10 * 1024 * 1024
    assert upload.validate_attachment(b'', 'empty.csv') is None
    assert upload.validate_attachment(b'x' * 9, 'big.csv', max_bytes=8) is None
    assert upload.validate_attachment(b'x' * 8, 'ok.csv', max_bytes=8) == ('text/csv', '.bin')


def test_clean_upload_name_strips_header_and_attribute_control_characters():
    from app.core.attachment_upload import clean_upload_name

    name = clean_upload_name('..\\..\\evil\r\nX-Injected: yes; onload="x".csv')
    for character in ('\r', '\n', ':', ';', '"', '<', '>', '/'):
        assert character not in name
    assert name.endswith('.csv')


def test_preview_allowlists_exclude_svg_html_and_script():
    import app.core.attachment_upload as upload

    assert 'image/png' in upload.SAFE_IMAGE_PREVIEW_MIMES
    assert 'application/pdf' in upload.SAFE_INLINE_PREVIEW_MIMES
    assert upload.SAFE_INLINE_PREVIEW_MIMES <= upload.SAFE_IMAGE_PREVIEW_MIMES | {'application/pdf'}
    for unsafe in ('image/svg+xml', 'text/html', 'application/javascript'):
        assert unsafe not in upload.SAFE_IMAGE_PREVIEW_MIMES
        assert unsafe not in upload.SAFE_INLINE_PREVIEW_MIMES


@pytest.mark.parametrize('filename,expected_mime', [
    ('rows.csv', 'text/csv'),
    ('notes.txt', 'text/plain'),
    ('page.html', 'application/octet-stream'),
    ('picture.svg', 'application/octet-stream'),
    ('program.exe', 'application/octet-stream'),
    ('extensionless', 'application/octet-stream'),
])
def test_validate_quotation_accepts_any_nonempty_file(tmp_path, filename, expected_mime):
    from app.core import project_quotation as quotations

    assert quotations.validate_quotation(CSV, filename) == (expected_mime, '.bin')
    stored = quotations.new_quotation_name('.bin')
    assert stored.endswith('.bin')
    path = quotations.safe_quotation_path(tmp_path, stored)
    assert path == (quotations.quotation_directory(tmp_path) / stored).resolve()
    for bad in ('../escape.bin', 'sub/escape.bin', 'escape.html', '', None):
        assert quotations.safe_quotation_path(tmp_path, bad) is None


def test_validate_quotation_keeps_photos_pdfs_and_rejects_bad_inputs(monkeypatch):
    from app.core import project_quotation as quotations

    assert quotations.validate_quotation(PNG, 'quote.csv') == ('image/png', '.png')
    assert quotations.validate_quotation(PDF, 'quote.pdf') == ('application/pdf', '.pdf')
    assert quotations.validate_quotation(b'', 'quote.csv') is None
    assert quotations.validate_quotation(CSV, None) is None
    assert quotations.validate_quotation(CSV, '...') is None
    monkeypatch.setattr(quotations, 'MAX_QUOTATION_BYTES', 4)
    assert quotations.validate_quotation(CSV, 'quote.csv') is None


# --- Upload routes -------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_attachments_accept_any_file_type_with_safe_storage(env):
    response = await web.web_task_add_attachment(
        _request(env.ada, headers={'X-Requested-With': 'XMLHttpRequest'}),
        env.task.id,
        files=[
            _upload('../../payload.html', HTML, 'text/html'),
            _upload('report.csv', CSV, 'text/csv'),
            _upload('extensionless', CFG),
        ],
        db=env.db,
    )
    assert response.status_code == 200
    assert json.loads(response.body)['saved'] == 3

    rows = (await env.db.execute(select(TaskAttachment).where(
        TaskAttachment.task_id == env.task.id
    ))).scalars().all()
    assert [row.filename for row in rows] == ['payload.html', 'report.csv', 'extensionless']
    for row, data in zip(rows, (HTML, CSV, CFG)):
        assert row.file_path.startswith('app/uploads/tasks/') and row.file_path.endswith('.bin')
        stored = env.tmp_path / row.file_path
        assert stored.is_file() and stored.read_bytes() == data


@pytest.mark.asyncio
async def test_task_comment_accepts_any_file_type_with_safe_storage(env):
    response = await web.web_task_add_comment(
        _request(env.ada), env.task.id, content='logs attached',
        files=[_upload('..\\data.json', JSON, 'application/json'),
               _upload('payload.svg', SVG, 'image/svg+xml')],
        db=env.db,
    )
    assert response.status_code == 303

    rows = (await env.db.execute(select(CommentAttachment))).scalars().all()
    assert [row.filename for row in rows] == ['data.json', 'payload.svg']
    for row, data in zip(rows, (JSON, SVG)):
        assert row.file_path.endswith('.bin')
        assert (env.tmp_path / row.file_path).read_bytes() == data
    assert rows[0].content_type == 'application/json'
    assert rows[1].content_type == 'application/octet-stream'


@pytest.mark.asyncio
async def test_ticket_comment_accepts_any_file_type_with_safe_storage(env):
    request = _request(env.ada)
    response = await web.web_tickets_add_comment(
        request, env.ticket.id, content='see files', is_internal=False,
        files=[_upload('../../archive.7z', EXE, 'application/x-7z-compressed'),
               _upload('notes.csv', CSV, 'text/csv')],
        db=env.db,
    )
    assert response.status_code == 303
    assert 'error_message' not in request.session

    rows = (await env.db.execute(select(TicketAttachment).where(
        TicketAttachment.comment_id.is_not(None)
    ))).scalars().all()
    assert [row.filename for row in rows] == ['archive.7z', 'notes.csv']
    for row, data in zip(rows, (EXE, CSV)):
        assert row.file_path.endswith('.bin')
        assert (env.tmp_path / row.file_path).read_bytes() == data


@pytest.mark.asyncio
async def test_ticket_photos_and_files_accepts_any_type_while_keeping_previews(env):
    response = await web.web_tickets_add_photos(
        _request(env.ada, headers={'X-Requested-With': 'XMLHttpRequest'}),
        env.ticket.id,
        photos=[
            _upload('../../meter.csv', CSV, 'text/csv'),
            _upload('bad.svg', SVG, 'image/svg+xml'),
            _upload('scan.pdf', PDF, 'application/pdf'),
            _upload('site.png', PNG, 'image/png'),
            _upload('extensionless', CFG),
        ],
        db=env.db,
    )
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload['saved'] == 5 and payload['error'] is None

    rows = (await env.db.execute(select(TicketAttachment).where(
        TicketAttachment.ticket_id == env.ticket.id
    ))).scalars().all()
    by_name = {row.filename: row for row in rows}
    assert set(by_name) == {'meter.csv', 'bad.svg', 'scan.pdf', 'site.png', 'extensionless'}
    assert by_name['meter.csv'].mime_type == 'text/csv'
    assert by_name['meter.csv'].file_path.endswith('.bin')
    assert by_name['bad.svg'].mime_type == 'application/octet-stream'
    assert by_name['bad.svg'].file_path.endswith('.bin')
    assert by_name['scan.pdf'].mime_type == 'application/pdf'
    assert by_name['scan.pdf'].file_path.endswith('.pdf')
    assert by_name['site.png'].mime_type == 'image/png'
    assert by_name['site.png'].file_path.endswith('.png')
    assert by_name['extensionless'].file_path.endswith('.bin')
    for data in (CSV, SVG, PDF, PNG, CFG):
        assert any((env.tmp_path / row.file_path).read_bytes() == data for row in rows)
    history = (await env.db.execute(select(TicketHistory).where(
        TicketHistory.ticket_id == env.ticket.id,
        TicketHistory.action == 'photos_added',
    ))).scalar_one()
    assert '5' in history.new_value


@pytest.mark.asyncio
async def test_guest_ticket_submission_accepts_any_file_type_safely(env):
    response = await web.web_tickets_guest_submit(
        _request(None, path='/web/tickets/guest'),
        guest_name='Grace', guest_surname='Hopper', guest_email='grace@example.test',
        guest_phone='555', guest_company='Acme', subject='Printer issue',
        description='The printer is jammed', guest_office_number=None, guest_branch=None,
        files=[_upload('../../sheet.csv', CSV, 'text/csv'),
               _upload('evil.html', HTML, 'text/html')],
        camera_files=[_upload('snap.png', PNG, 'image/png')],
        db=env.db,
    )
    assert response.status_code == 200

    ticket = (await env.db.execute(select(Ticket).where(Ticket.is_guest.is_(True)))).scalar_one()
    rows = (await env.db.execute(select(TicketAttachment).where(
        TicketAttachment.ticket_id == ticket.id
    ))).scalars().all()
    assert [row.filename for row in rows] == ['sheet.csv', 'evil.html', 'snap.png']
    assert rows[0].file_path.endswith('.bin') and rows[0].mime_type == 'text/csv'
    assert rows[1].file_path.endswith('.bin') and rows[1].mime_type == 'application/octet-stream'
    assert rows[2].file_path.endswith('.png') and rows[2].mime_type == 'image/png'
    for row, data in zip(rows, (CSV, HTML, PNG)):
        assert (env.tmp_path / row.file_path).read_bytes() == data


@pytest.mark.asyncio
async def test_chat_messages_accept_any_file_type_with_safe_storage(env):
    response = await web.web_chat_send_message(
        _request(env.ada), env.chat.id, content='logs',
        attachments=[_upload('../../payload.exe', EXE),
                     _upload('rows.csv', CSV, 'text/csv'),
                     _upload('shot.png', PNG, 'image/png')],
        db=env.db,
    )
    assert response.status_code == 303

    rows = (await env.db.execute(select(MessageAttachment))).scalars().all()
    assert [row.filename for row in rows] == ['payload.exe', 'rows.csv', 'shot.png']
    assert rows[0].file_path.startswith('app/uploads/chat_messages/')
    assert rows[0].file_path.endswith('.bin')
    assert rows[0].mime_type == 'application/octet-stream'
    assert rows[1].mime_type == 'text/csv'
    assert rows[2].file_path.endswith('.png')
    for row, data in zip(rows, (EXE, CSV, PNG)):
        stored = env.tmp_path / row.file_path
        assert stored.is_file() and stored.read_bytes() == data


@pytest.mark.asyncio
async def test_project_quotation_accepts_any_file_type_safely(env):
    response, request = await _create_project(env, _upload('../../quote.html', HTML, 'text/html'))
    assert response.status_code == 303
    project = (await env.db.execute(select(Project).where(
        Project.name == 'Any file project'
    ))).scalar_one()
    assert project.quotation_filename.endswith('.bin')
    assert project.quotation_original_name == 'quote.html'
    assert project.quotation_content_type == 'application/octet-stream'
    stored = env.app_dir / 'private_uploads' / 'quotations' / project.quotation_filename
    assert stored.is_file() and stored.read_bytes() == HTML
    assert not (env.app_dir / 'uploads').exists(), 'quotations stay outside the public mount'
    await env.db.refresh(env.acme)
    assert env.acme.quotation_bytes == len(HTML)
    assert 'error_message' not in request.session


async def _create_project(env, quotation, *, name='Any file project'):
    request = _request(env.ada)
    response = await web.web_projects_create(
        request, name=name, description=None, quotation_file=quotation, db=env.db
    )
    return response, request


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['empty', 'oversized'])
async def test_every_route_still_rejects_empty_and_oversized_files(env, monkeypatch, case):
    import app.core.project_quotation as quotations

    monkeypatch.setattr(web, 'MAX_ATTACHMENT_BYTES', 32)
    monkeypatch.setattr(web, 'TICKET_PHOTO_MAX_BYTES', 32)
    monkeypatch.setattr(quotations, 'MAX_QUOTATION_BYTES', 32)
    data = b'' if case == 'empty' else b'x' * 33
    filename = 'empty.csv' if case == 'empty' else 'big.csv'
    expected = 'empty' if case == 'empty' else 'larger'

    ajax = _request(env.ada, headers={'X-Requested-With': 'XMLHttpRequest'})
    response = await web.web_task_add_attachment(
        ajax, env.task.id, files=[_upload(filename, data, 'text/csv')], db=env.db
    )
    assert response.status_code == 400
    assert expected in json.loads(response.body)['skipped'][0].lower()
    assert (await env.db.execute(select(TaskAttachment))).scalars().all() == []

    response = await web.web_tickets_add_photos(
        ajax, env.ticket.id, photos=[_upload(filename, data, 'text/csv')], db=env.db
    )
    assert response.status_code == 400
    assert expected in json.loads(response.body)['error'].lower()

    comment_request = _request(env.ada)
    await web.web_tickets_add_comment(
        comment_request, env.ticket.id, content='x', is_internal=False,
        files=[_upload(filename, data, 'text/csv')], db=env.db,
    )
    assert expected in comment_request.session['error_message'].lower()

    task_comment_request = _request(env.ada)
    await web.web_task_add_comment(
        task_comment_request, env.task.id, content='x',
        files=[_upload(filename, data, 'text/csv')], db=env.db,
    )
    assert expected in task_comment_request.session['error_message'].lower()

    chat_request = _request(env.ada)
    await web.web_chat_send_message(
        chat_request, env.chat.id, content='x',
        attachments=[_upload(filename, data, 'text/csv')], db=env.db,
    )
    assert expected.lower() in chat_request.session['error_message'].lower()
    assert (await env.db.execute(select(MessageAttachment))).scalars().all() == []

    await web.web_tickets_guest_submit(
        _request(None, path='/web/tickets/guest'),
        guest_name='G', guest_surname='H', guest_email='g@example.test',
        guest_phone='1', guest_company='Acme', subject='Issue', description='Desc',
        guest_office_number=None, guest_branch=None,
        files=[_upload(filename, data, 'text/csv')], camera_files=[], db=env.db,
    )
    guest_ticket = (await env.db.execute(select(Ticket).where(
        Ticket.is_guest.is_(True)
    ))).scalar_one_or_none()
    if guest_ticket:
        assert (await env.db.execute(select(TicketAttachment).where(
            TicketAttachment.ticket_id == guest_ticket.id
        ))).scalars().all() == []

    quote_request = _request(env.ada)
    await web.web_projects_create(
        quote_request, name='Bad quote', quotation_file=_upload(filename, data, 'text/csv'),
        db=env.db,
    )
    assert 'quotation' in quote_request.session['error_message'].lower()
    assert (await env.db.execute(select(Project).where(
        Project.name == 'Bad quote'
    ))).scalars().all() == []


# --- Downloads and previews ----------------------------------------------------

def _write_attachment(env, folder, filename, data):
    path = env.app_dir / 'uploads' / folder / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f'app/uploads/{folder}/{filename}'


@pytest.mark.asyncio
async def test_download_routes_force_attachment_with_hardened_headers(env):
    hostile = '../../evil " onload="alert.csv'
    data_by_kind = {
        'ticket': (CSV, 'text/csv'),
        'task': (JSON, 'application/json'),
        'comment': (HTML, 'application/octet-stream'),
        'chat': (SVG, 'application/octet-stream'),
    }
    folders = {
        'ticket': 'tickets', 'task': 'tasks',
        'comment': 'comments', 'chat': 'chat_messages',
    }
    paths = {
        kind: _write_attachment(env, folders[kind], f'{kind}.bin', data_by_kind[kind][0])
        for kind in data_by_kind
    }

    # The chat download route needs a message in a chat the user belongs to.
    from app.models.chat import Message
    message = Message(chat_id=env.chat.id, author_id=env.ada.id, content='x')
    env.db.add(message)
    await env.db.flush()

    comment = Comment(task_id=env.task.id, author_id=env.ada.id, content='x')
    env.db.add(comment)
    await env.db.flush()
    ticket_attachment = TicketAttachment(
        ticket_id=env.ticket.id, filename=hostile, file_path=paths['ticket'],
        file_size=len(CSV), mime_type='text/csv', uploaded_by_id=env.ada.id,
    )
    task_attachment = TaskAttachment(
        task_id=env.task.id, filename=hostile, file_path=paths['task'],
        file_size=len(JSON), file_type='application/json', uploaded_by=env.ada.id,
    )
    comment_attachment = CommentAttachment(
        comment_id=comment.id, filename=hostile, file_path=paths['comment'],
        file_size=len(HTML), content_type='application/octet-stream',
        uploaded_by_id=env.ada.id,
    )
    chat_attachment = MessageAttachment(
        message_id=message.id, filename=hostile, file_path=paths['chat'],
        file_size=len(SVG), mime_type='application/octet-stream',
    )
    env.db.add_all([ticket_attachment, task_attachment, comment_attachment, chat_attachment])
    await env.db.commit()

    calls = [
        web.web_ticket_attachment_download(_request(env.ada), ticket_attachment.id, db=env.db),
        web.web_task_attachment_download(_request(env.ada), task_attachment.id, db=env.db),
        web.download_comment_attachment(_request(env.ada), comment_attachment.id, db=env.db),
        web.download_chat_attachment(_request(env.ada), chat_attachment.id, db=env.db),
    ]
    for response in [await call for call in calls]:
        assert response.status_code == 200
        disposition = response.headers['content-disposition']
        assert disposition.startswith('attachment;'), disposition
        assert '..' not in disposition and '<' not in disposition and '" on' not in disposition
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['content-security-policy'] == "default-src 'none'"


@pytest.mark.asyncio
async def test_stored_paths_outside_uploads_are_never_downloaded_or_deleted(env):
    outside = env.tmp_path / 'secret.txt'
    outside.write_text('not an attachment')
    inside = _write_attachment(env, 'tickets', 'safe.bin', CSV)

    assert web._resolve_stored_upload(str(outside)) is None
    assert web._resolve_stored_upload('../secret.txt') is None
    assert web._resolve_stored_upload(inside) is not None

    attachment = TicketAttachment(
        ticket_id=env.ticket.id, filename='secret.txt', file_path=str(outside),
        file_size=len(outside.read_bytes()), mime_type='text/plain',
        uploaded_by_id=env.ada.id,
    )
    env.db.add(attachment)
    await env.db.commit()

    with pytest.raises(HTTPException) as denied:
        await web.web_ticket_attachment_download(_request(env.ada), attachment.id, db=env.db)
    assert denied.value.status_code == 404

    deletion = await web.web_ticket_attachment_delete(
        _request(env.ada), attachment.id, db=env.db
    )
    assert deletion.status_code == 200
    assert outside.is_file(), 'a bad DB row must never delete an arbitrary file'


@pytest.mark.asyncio
async def test_preview_routes_never_serve_active_web_content_inline(env):
    ticket_cases = [('svg.bin', SVG, 'image/svg+xml'), ('html.bin', HTML, 'text/html')]
    for name, data, mime in ticket_cases:
        path = _write_attachment(env, 'tickets', name, data)
        attachment = TicketAttachment(
            ticket_id=env.ticket.id, filename=name, file_path=path,
            file_size=len(data), mime_type=mime, uploaded_by_id=env.ada.id,
        )
        env.db.add(attachment)
        await env.db.flush()
        with pytest.raises(HTTPException) as denied:
            await web.web_ticket_attachment_preview(_request(env.ada), attachment.id, db=env.db)
        assert denied.value.status_code == 400

    photo_path = _write_attachment(env, 'tickets', 'photo.png', PNG)
    photo = TicketAttachment(
        ticket_id=env.ticket.id, filename='photo.png', file_path=photo_path,
        file_size=len(PNG), mime_type='image/png', uploaded_by_id=env.ada.id,
    )
    env.db.add(photo)
    await env.db.commit()
    preview = await web.web_ticket_attachment_preview(_request(env.ada), photo.id, db=env.db)
    assert preview.status_code == 200
    assert preview.media_type == 'image/png'
    assert preview.headers['x-content-type-options'] == 'nosniff'

    comment = Comment(task_id=env.task.id, author_id=env.ada.id, content='x')
    env.db.add(comment)
    await env.db.flush()
    unsafe_path = _write_attachment(env, 'comments', 'page.bin', HTML)
    unsafe = CommentAttachment(
        comment_id=comment.id, filename='page.html', file_path=unsafe_path,
        file_size=len(HTML), content_type='text/html', uploaded_by_id=env.ada.id,
    )
    safe_path = _write_attachment(env, 'comments', 'report.bin', PDF)
    safe = CommentAttachment(
        comment_id=comment.id, filename='report.pdf', file_path=safe_path,
        file_size=len(PDF), content_type='application/pdf', uploaded_by_id=env.ada.id,
    )
    env.db.add_all([unsafe, safe])
    await env.db.commit()

    forced = await web.preview_comment_attachment(_request(env.ada), unsafe.id, db=env.db)
    assert forced.status_code == 200
    assert forced.headers['content-disposition'].startswith('attachment;')
    assert forced.media_type == 'application/octet-stream'
    assert forced.headers['x-content-type-options'] == 'nosniff'
    assert forced.headers['content-security-policy'] == "default-src 'none'"

    inline = await web.preview_comment_attachment(_request(env.ada), safe.id, db=env.db)
    assert inline.status_code == 200
    assert inline.headers['content-disposition'].startswith('inline;')
    assert inline.media_type == 'application/pdf'


# --- Templates -----------------------------------------------------------------

def test_dropzone_default_accepts_any_file_type_but_honors_explicit_accept():
    environment = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    source = (TEMPLATES_DIR / '_dropzone_assets.html').read_text(encoding='utf-8')
    assert "accept=''" in source
    assert 'allowed.length' in source, 'explicit accept lists are still enforced in the browser'

    rendered = environment.from_string(
        "{% from '_dropzone_assets.html' import dropzone %}"
        "{{ dropzone('files', 'any-file', max_bytes=1024) }}"
    ).render()
    assert 'accept=' not in rendered
    assert 'name="files"' in rendered and 'data-max-bytes="1024"' in rendered

    restricted = environment.from_string(
        "{% from '_dropzone_assets.html' import dropzone %}"
        "{{ dropzone('files', 'some-file', accept='.pdf') }}"
    ).render()
    assert 'accept=".pdf"' in restricted


def test_general_upload_templates_no_longer_restrict_the_file_picker():
    detail = (TEMPLATES_DIR / 'tickets' / 'detail.html').read_text(encoding='utf-8')
    attachment_cards = (TEMPLATES_DIR / 'tickets' / '_attachments_page.html').read_text(encoding='utf-8')
    assert "accept='image/*'" not in detail
    assert 'photoGalleryInput' in detail
    assert 'Drop photos or files here, or click to browse' in detail
    assert 'Any file type up to 15 MB each' in detail
    assert 'Any file type up to 10 MB each' in detail
    assert 'file(s)' in detail and '/web/tickets/{{ ticket.id }}/photos' in detail
    assert "attachment.mime_type == 'text/csv'" in attachment_cards
    assert 'photoCameraInput" accept="image/*" capture="environment"' in detail

    guest = (TEMPLATES_DIR / 'tickets' / 'guest.html').read_text(encoding='utf-8')
    assert 'id="file-upload"' in guest
    assert 'accept="image/*,.pdf' not in guest
    assert 'any file type up to 10 mb' in guest.lower()
    assert 'accept="image/*" capture="environment" id="camera-input"' in guest

    projects = (TEMPLATES_DIR / 'projects' / 'index.html').read_text(encoding='utf-8')
    assert "accept='.pdf" not in projects
    assert 'Any file type · up to 25 MB' in projects

    specialized = {
        'admin/backups.html': 'accept=".zip"',
        'admin/superadmin.html': 'accept=".db,.zip"',
        'admin/site_settings.html': 'accept="image/*"',
        'auth/profile.html': 'accept="image/jpeg,image/png,image/gif,image/webp"',
        'tickets/bubbles.html': 'accept="image/*"',
    }
    for name, marker in specialized.items():
        assert marker in (TEMPLATES_DIR / name).read_text(encoding='utf-8'), name

    environment = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    for template in sorted(TEMPLATES_DIR.rglob('*.html')):
        environment.parse(template.read_text(encoding='utf-8'))
