# CRM Backend (FastAPI)

A small-business friendly CRM backend focused on Task & Project management.

## Features
- FastAPI with OpenAPI docs
- SQLite by default (file `data.db` in project root) with optional Postgres later
- SQLModel (SQLAlchemy) models and Pydantic schemas
- JWT auth (access/refresh), password hashing
- CRUD for Projects and Tasks (pagination, filtering, sorting)
- Minimal server-rendered pages (Jinja2 + HTMX) for quick start
- Alembic migrations
- Tests with pytest + httpx
- Ruff/Black/Isort, Mypy (optional)

## Quickstart (Windows PowerShell)

1. Clone or open this folder.
2. Create a virtual environment and install deps:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

3. Copy env file and adjust if needed (SQLite is default):

```powershell
Copy-Item .env.example .env
```

4. Initialize the database (SQLite):

```powershell
# Create tables via app startup
python -m app.scripts.init_db
```

5. Run the app:

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

6. Open docs and pages:
- API docs: http://localhost:8000/docs
- Projects page: http://localhost:8000/

## Optional: Postgres + Docker
- Update `DATABASE_URL` in `.env` to Postgres
- Use `docker-compose up -d` (service included for later use)

## Tests

```powershell
pytest
```

## Next Steps
- Expand permissions and roles
- Add attachments and comments UI
- Add email/notification integrations
