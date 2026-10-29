"""PDF / Excel exports of All Tasks, the Board View, a project and the project report."""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

# Both are in requirements.txt; skip where they are not installed
pytest.importorskip('openpyxl', reason='openpyxl (requirements.txt) writes the Excel files')
pytest.importorskip('reportlab', reason='reportlab (requirements.txt) writes the PDF files')

from app.core.exports import Export, ExportTable, to_excel, to_pdf  # noqa: E402
from app.models.assignment import Assignment  # noqa: E402
from app.models.enums import TaskStatus  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.project_member import ProjectMember  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.models.task_extensions import TaskTimeEntry  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.workspace import Workspace  # noqa: E402
from app.web.routes import (  # noqa: E402
    web_admin_generate_user_activity_excel,
    web_admin_generate_user_activity_pdf,
    web_project_export,
    web_project_report,
    web_project_report_excel,
    web_projects_export,
    web_tasks_list,
    web_tasks_list_export,
)

TODAY = date.today()


def _workbook(body: bytes):
    openpyxl = pytest.importorskip('openpyxl', reason='openpyxl (requirements.txt) writes the Excel files')
    return openpyxl.load_workbook(io.BytesIO(body))


def _rows(wb, sheet):
    return [list(r) for r in wb[sheet].iter_rows(values_only=True)]


def test_excel_and_pdf_builders():
    export = Export(
        title='Test <export> & co', filename='x',
        lines=['Showing: everything'], summary=[('Tasks', 2)],
        tables=[ExportTable('Tasks: [all]', ['Task', 'Due'], [['Fix "printer" <b> & cable\x07', TODAY], ['Second', None]])],
    )
    wb = _workbook(to_excel(export))
    assert wb.sheetnames == ['Summary', 'Tasks all'], 'sheet names cannot hold []:*?/\\'
    header = wb['Tasks all']['A1']
    assert header.value == 'Task' and header.fill.start_color.rgb.endswith('6366F1') and header.font.bold
    assert wb['Tasks all']['A2'].value == 'Fix "printer" <b> & cable', 'control characters dropped'
    assert wb['Tasks all']['B2'].value.date() == TODAY and wb['Tasks all'].freeze_panes == 'A2'
    assert _rows(wb, 'Summary')[0][0] == 'Test <export> & co'

    pytest.importorskip('reportlab')
    pdf = to_pdf(export)
    assert pdf.startswith(b'%PDF')
    fitz = pytest.importorskip('fitz')
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        text = ' '.join(' '.join(page.get_text().split()) for page in doc)
    assert 'Fix "printer" <b> & cable' in text and 'Test <export> & co' in text


async def _seed(path):
    """Acme: admin Ada, member Bob (of Office move), Cara (no projects). Other Co elsewhere."""
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name='Acme'), Workspace(name='Other Co')
    db.add_all([acme, other])
    await db.flush()
    u = dict(ada=User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id),
             bob=User(username='bob', hashed_password='x', full_name='Bob Builder', workspace_id=acme.id),
             cara=User(username='cara', hashed_password='x', workspace_id=acme.id),
             olive=User(username='olive', hashed_password='x', is_admin=True, workspace_id=other.id))
    db.add_all(u.values())
    await db.flush()
    p = dict(move=Project(name='Office move', workspace_id=acme.id, owner_id=u['ada'].id),
             site=Project(name='New site', workspace_id=acme.id, owner_id=u['ada'].id),
             old=Project(name='Old stuff', workspace_id=acme.id, owner_id=u['ada'].id, is_archived=True))
    db.add_all(p.values())
    await db.flush()
    db.add(ProjectMember(project_id=p['move'].id, user_id=u['bob'].id))
    t = dict(
        overdue=Task(title='Patch switch', project_id=p['move'].id, creator_id=u['ada'].id,
                     due_date=TODAY - timedelta(days=2)),
        doing=Task(title='Move desks', project_id=p['move'].id, creator_id=u['ada'].id, status=TaskStatus.in_progress),
        stuck=Task(title='Order cables', project_id=p['move'].id, creator_id=u['ada'].id, status=TaskStatus.blocked),
        finished=Task(title='Pack boxes', project_id=p['move'].id, creator_id=u['ada'].id, status=TaskStatus.done,
                      is_archived=True),
        survey=Task(title='Survey site', project_id=p['site'].id, creator_id=u['ada'].id),
    )
    db.add_all(t.values())
    await db.flush()
    db.add_all([Assignment(task_id=t['overdue'].id, assignee_id=u['bob'].id),
                Assignment(task_id=t['survey'].id, assignee_id=u['cara'].id),
                TaskTimeEntry(task_id=t['overdue'].id, user_id=u['bob'].id,
                              started_at=datetime(2026, 1, 1, 8), ended_at=datetime(2026, 1, 1, 9))])
    await db.commit()
    return engine, db, u, p, t


def _request(user, path='/web/tasks/list'):
    return SimpleNamespace(session={'user_id': user.id}, state=SimpleNamespace(), headers={},
                           url=SimpleNamespace(path=path, query=''), query_params={})


async def _tasks_export(db, user, fmt='excel', **filters):
    args = dict(tab=None, status=None, priority=None, assignee_id=None, project_id=None)
    args.update(filters)
    return await web_tasks_list_export(_request(user), fmt, db=db, **args)


@pytest.mark.asyncio
async def test_all_tasks_export_follows_the_page(tmp_path):
    engine, db, u, p, t = await _seed(tmp_path / 'e.db')
    try:
        response = await _tasks_export(db, u['ada'], project_id=str(p['move'].id))
        assert response.headers['content-disposition'].startswith('attachment; filename="tasks_')
        rows = _rows(_workbook(response.body), 'Tasks')
        assert rows[0] == ['Task', 'Project', 'Status', 'Priority', 'Assigned to', 'Start', 'Due', 'Overdue', 'Time worked']
        assert sorted(r[0] for r in rows[1:]) == ['Move desks', 'Order cables', 'Patch switch'], 'active tasks, as the page'
        patch = next(r for r in rows if r[0] == 'Patch switch')
        assert patch[4] == 'Bob Builder' and patch[7] == 'Yes' and patch[8] == '1h 00m'

        done = _rows(_workbook((await _tasks_export(db, u['ada'], tab='done')).body), 'Tasks')
        assert [r[0] for r in done[1:]] == ['Pack boxes']

        # Bob sees only his own tasks in his projects, as on the page
        bobs = _rows(_workbook((await _tasks_export(db, u['bob'])).body), 'Tasks')
        assert [r[0] for r in bobs[1:]] == ['Patch switch']

        page = await web_tasks_list(_request(u['bob']), tab=None, status=None, priority=None,
                                    assignee_id=None, project_id=None, db=db)
        assert [task.title for task in page.context['tasks']] == ['Patch switch'], 'the page itself still works'

        pdf = await _tasks_export(db, u['ada'], fmt='pdf')
        assert pdf.media_type == 'application/pdf' and pdf.body.startswith(b'%PDF')
        with pytest.raises(HTTPException) as unknown:
            await _tasks_export(db, u['ada'], fmt='docx')
        assert unknown.value.status_code == 404
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_board_view_export(tmp_path):
    engine, db, u, p, t = await _seed(tmp_path / 'e.db')
    try:
        wb = _workbook((await web_projects_export(_request(u['ada']), 'excel', db=db)).body)
        projects = {r[0]: r for r in _rows(wb, 'Projects')[1:]}
        assert set(projects) == {'Office move', 'New site'}, 'archived projects left out'
        move = projects['Office move']
        assert move[4:13] == [1, 1, 1, 1, 1, 4, '25%', 1, '1h 00m'], 'members, columns, total, done %, overdue, time'
        tasks = _rows(wb, 'Tasks')
        assert [r[0] for r in tasks[1:]] == ['Survey site', 'Patch switch', 'Move desks', 'Order cables', 'Pack boxes'], \
            'by project, then board column; completed tasks included'

        bobs = _workbook((await web_projects_export(_request(u['bob']), 'excel', db=db)).body)
        assert [r[0] for r in _rows(bobs, 'Projects')[1:]] == ['Office move'], 'only his projects'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_export_and_access(tmp_path):
    engine, db, u, p, t = await _seed(tmp_path / 'e.db')
    try:
        response = await web_project_export(_request(u['bob']), p['move'].id, 'excel', db=db)
        assert 'project_Office_move_' in response.headers['content-disposition']
        wb = _workbook(response.body)
        rows = _rows(wb, 'Tasks')
        assert 'Project' not in rows[0]
        assert [r[0] for r in rows[1:]] == ['Patch switch', 'Move desks', 'Order cables', 'Pack boxes']
        summary = dict(r[:2] for r in _rows(wb, 'Summary') if r[0] and r[1] is not None)
        assert summary['Tasks'] == 4 and summary['Done'] in (1, '25%')

        with pytest.raises(HTTPException) as not_member:
            await web_project_export(_request(u['cara']), p['move'].id, 'pdf', db=db)
        assert not_member.value.status_code == 403
        with pytest.raises(HTTPException) as elsewhere:
            await web_project_export(_request(u['olive']), p['move'].id, 'pdf', db=db)
        assert elsewhere.value.status_code == 404
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_report_excel(tmp_path):
    engine, db, u, p, t = await _seed(tmp_path / 'e.db')
    try:
        dates = dict(start_date=(TODAY - timedelta(days=30)).isoformat(), end_date=TODAY.isoformat())
        wb = _workbook((await web_project_report_excel(_request(u['ada']), p['move'].id, db=db, **dates)).body)
        assert wb.sheetnames == ['Summary', 'Contributors', 'Tasks', 'Recent activity']
        assert sorted(r[0] for r in _rows(wb, 'Tasks')[1:]) == ['Move desks', 'Order cables', 'Pack boxes', 'Patch switch']
        assert _rows(wb, 'Contributors')[1][0] == 'Bob Builder'

        page = await web_project_report(_request(u['ada'], '/web/projects/1/report'), p['move'].id, db=db, **dates)
        assert page.context['summary']['total_tasks'] == 4, 'the report page itself still works'

        with pytest.raises(HTTPException) as not_admin:
            await web_project_report_excel(_request(u['bob']), p['move'].id, db=db, **dates)
        assert not_admin.value.status_code == 403
    finally:
        await db.close()
        await engine.dispose()


async def _streamed(response) -> bytes:
    return b''.join([chunk async for chunk in response.body_iterator])


@pytest.mark.asyncio
async def test_user_activity_report_with_projects(tmp_path):
    """Ada created projects in the period: both report files still build
    (they used to fail on a project status that projects do not have)."""
    engine, db, u, p, t = await _seed(tmp_path / 'e.db')
    try:
        dates = dict(start_date=(TODAY - timedelta(days=30)).isoformat(), end_date=TODAY.isoformat())
        wb = _workbook(await _streamed(await web_admin_generate_user_activity_excel(
            _request(u['ada']), u['ada'].id, db=db, **dates)))
        projects = _rows(wb, 'Projects Created')
        assert sorted(r[1:] for r in projects[1:]) == [['New site', 'Active'], ['Office move', 'Active'],
                                                      ['Old stuff', 'Archived']]
        pdf = await web_admin_generate_user_activity_pdf(_request(u['ada']), u['ada'].id, db=db, **dates)
        assert (await _streamed(pdf)).startswith(b'%PDF')
    finally:
        await db.close()
        await engine.dispose()
