# backup-teams

A scraper and REST API for archiving files from Microsoft Teams — built because Microsoft
makes it nearly impossible to reliably access what should be your own files.

---

## The Problem with Microsoft Teams

Microsoft Teams is a closed, opaque ecosystem with no straightforward way to bulk-export
files. The specific failures that motivated this project:

- **No bulk download.** Files are buried inside SharePoint drives nested under Teams and
  Channels. There is no "download everything" button.
- **Files disappear without warning.** When a professor closes a Team or leaves the
  institution, all associated files become inaccessible to students — permanently.
- **The desktop app is a web wrapper.** Teams is Electron-based, which means authentication
  is handled through an embedded browser session with no exposed API surface for token
  extraction without interception.
- **Token acquisition is hostile to automation.** Microsoft's OAuth flow for Teams requires
  an interactive browser session. The access token changes on every login, is not stored
  anywhere accessible on disk, and must be captured from live network traffic.
- **Rate limiting with no feedback.** The Microsoft Graph API — the only programmatic
  interface to Teams files — enforces rate limits that return 429 errors without a
  consistent `Retry-After` header across all endpoints.

This project works around all of the above.

---

## How It Works

Authentication is handled by a Playwright-controlled browser that performs the full
interactive login to the Teams web client. Network traffic is intercepted mid-session to
capture the Bearer token from a live Graph API request. That token is then used to drive
all subsequent file operations via the Graph API directly, bypassing the Teams UI entirely.

Files are tracked by their Microsoft Graph `eTag`. On every run, the scraper checks whether
the stored eTag matches the current one. If it matches, the file is skipped. If it differs,
the old local copy is renamed to a timestamped backup and the new version is downloaded.
This means re-runs are safe and fast — only changed or new files are touched.

All metadata (courses, classes, professors, files, contributors) is persisted in a
PostgreSQL database, which forms the foundation for the REST API layer.

---

## Project Phases

### Phase 1 — Single-student local scraper (complete)

A command-line tool that authenticates one student against Teams, walks all joined Teams
and Channels via the Graph API, and downloads every file to a local directory. Database
tracks files by eTag to skip unchanged files on subsequent runs.

### Phase 2 — Schema and API foundation (complete)

PostgreSQL schema extended to support multi-student contributions. Alembic introduced for
versioned schema migrations. FastAPI application scaffolded with stub routes for files and
classes, ready for implementation.

### Phase 3 — Cloud deployment and REST API (in progress)

Deploy PostgreSQL to a managed cloud provider. Implement the REST API endpoints so that
any student can query the aggregated file index and retrieve download metadata. Add API
key authentication to gate write operations.

### Phase 4 — Multi-student aggregation (planned)

Multiple students run the scraper independently. Each scrape run is attributed to a
student record. The central database aggregates file discoveries across all contributors,
building a complete index of available course materials regardless of which student has
access to which Team.

### Phase 5 — S3 storage and deduplication (planned)

Rather than storing files locally on each student's machine, downloads are uploaded to an
S3 bucket keyed by `drive_item_id`. The `archive.s3_key` column (already present in the
schema) maps each file to its cloud object. A single canonical copy exists per file
regardless of how many students submitted it.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Scraper orchestration | Python, asyncio |
| Authentication | Playwright (Chromium) |
| Graph API client | httpx |
| Database | PostgreSQL 16 |
| ORM / migrations | SQLAlchemy + Alembic |
| Async DB driver | asyncpg |
| REST API | FastAPI + Uvicorn |
| Containerisation | Docker, Docker Compose |

---

## Local Setup

**Prerequisites:** Docker Desktop, Python 3.9+, Git.

```bash
git clone https://github.com/raulkolaric/backup-teams.git
cd backup-teams

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env   # edit with your Teams credentials and DB settings

# Start the database
docker compose up db -d

# Apply schema migrations
alembic upgrade head

# Run the scraper
python main.py
```

**API server:**

```bash
uvicorn api.main:app --reload
# Interactive docs at http://127.0.0.1:8000/docs
```

**Run tests:**

```bash
pytest tests/ -v
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `EMAIL` | Microsoft Teams login email |
| `PASSWORD` | Microsoft Teams login password |
| `DB_HOST` | PostgreSQL host (default: `localhost`) |
| `DB_PORT` | PostgreSQL port (default: `5433` for Docker) |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `DOWNLOAD_ROOT` | Local directory for downloaded files |
| `DOWNLOAD_CONCURRENCY` | Max parallel downloads (default: `4`) |
| `DEFAULT_SEMESTER` | Fallback semester label (e.g. `2026/1`) |
| `DEFAULT_YEAR` | Fallback year (e.g. `2026`) |

Never commit `.env`. It is listed in `.gitignore`.

---

## Repository Structure

```
backup-teams/
  src/
    auth.py            # Playwright login and token capture
    graph_client.py    # Graph API wrapper
    teams_scraper.py   # Orchestration: Teams -> Channels -> files
    downloader.py      # File download with eTag deduplication
    db.py              # asyncpg pool and SQL queries
    utils.py           # Path helpers and sanitisation
  api/
    main.py            # FastAPI entry point
    routers/
      files.py         # GET /files endpoints
      classes.py       # GET /classes endpoints
  alembic/
    versions/          # Versioned schema migrations
    env.py             # Migration environment
  db/
    schema.sql         # Reference schema (migrations are the source of truth)
  tests/
    test_downloader.py # Unit tests for skip and conflict logic
  docker-compose.yml
  requirements.txt
```
