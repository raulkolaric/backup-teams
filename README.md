# backup-teams

An automated, cloud-native pipeline for archiving Microsoft Teams course files. Built in Python using asynchronous I/O, the system intercepts network traffic to capture OAuth tokens from the Teams web client, walks the Microsoft Graph API to enumerate all course channels and their files, streams file content directly to Amazon S3 without writing to disk, and persists structured metadata to a PostgreSQL database on AWS RDS. A FastAPI application exposes the indexed data as a REST API with time-limited presigned S3 download URLs.

This project exists because Microsoft provides no viable bulk-export mechanism for Teams files, and files become permanently inaccessible when a professor leaves an institution or closes a channel.

---

## Technical Highlights

**Network traffic interception for OAuth token capture.** Microsoft Teams does not expose an OAuth flow suitable for automated use. This project uses Playwright to control a Chromium browser, navigate the Teams web application, and intercept live network requests at the HTTP layer to extract Bearer tokens from authenticated Graph API calls. The captured token is used to drive all subsequent API interactions, bypassing the need for registered Azure AD applications.

**Fully asynchronous I/O pipeline.** The download and upload pipeline is built on Python's `asyncio` with `asyncpg` for non-blocking database access and `httpx` for async HTTP. File downloads from the Graph API and S3 uploads via `boto3` run concurrently using `asyncio.to_thread`, keeping the event loop unblocked during blocking I/O without thread pool starvation.

**eTag-based incremental deduplication.** Every file in the Microsoft Graph API carries an eTag field that changes when the file content changes. The system stores each file's eTag and checks it on every run before downloading. Files with matching eTags are skipped entirely — no download, no S3 operation, no database write. Changed files are detected automatically and re-uploaded, with the S3 object overwritten in-place.

**S3-direct streaming pipeline with zero local disk writes.** File bytes flow from the Graph API download endpoint directly into memory and from there directly to S3 via `put_object`. Nothing is written to the local filesystem. The pipeline fails fast: if the S3 upload fails, the database record is not written, so there are no dangling metadata rows pointing to non-existent objects.

**Versioned schema migrations with Alembic.** The PostgreSQL schema is managed entirely by Alembic migrations, not raw SQL scripts. Each schema change is a versioned, reversible migration file with an `upgrade` and `downgrade` function. The migration history is stored in the `alembic_version` table in the database, making it possible to know the exact schema version of any database instance.

---

## Architecture

```
Microsoft Teams Web Client
        |
        | Playwright intercepts Bearer token from live MSAL request
        |
   Graph API (httpx async client)
        |
        | Enumerate Joined Teams → Channels → Drive Items
        |
   download_item() [asyncio concurrent tasks]
        |
        +──── eTag check (asyncpg) ─── SKIP if unchanged
        |
        +──── Download bytes (Graph API /content)
                    |
                    +──── S3 put_object (boto3, asyncio.to_thread)
                    |            |
                    |            +──── archive record (asyncpg upsert)
                    |
              AWS RDS PostgreSQL
              AWS S3 Object Storage
                    |
              FastAPI REST API
                    |
              Presigned URL generation
              → Client downloads file directly from S3
```

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Browser automation | Playwright (Chromium) | Session management and network interception |
| Graph API client | httpx (async) | Async HTTP/2 client for all Microsoft Graph calls |
| File download pipeline | Python asyncio, asyncio.to_thread | Concurrent async/sync boundary management |
| Cloud file storage | Amazon S3 (boto3) | Durable object storage for all course files |
| Cloud database | AWS RDS PostgreSQL 16 | Managed relational store for file metadata |
| Async DB driver | asyncpg | High-performance async PostgreSQL client |
| Schema migrations | Alembic + SQLAlchemy | Versioned, reversible schema management |
| REST API | FastAPI + Uvicorn | Async Python API with OpenAPI documentation |
| Containerisation | Docker, Docker Compose | Reproducible local development environment |
| Testing | pytest, pytest-asyncio | Async unit tests with full I/O mocking |
| Environment | python-dotenv | Secrets management from `.env` |

---

## Database Schema

Five tables managed by Alembic migrations:

| Table | Description |
|---|---|
| `professor` | Deduplicated professor records from Teams channel metadata |
| `curso` | Course records keyed by name and institution |
| `class` | A specific run of a course (semester, year, channel) |
| `student` | Students who have contributed scrape runs |
| `archive` | File index: name, extension, s3_key, etag, drive_item_id, timestamps |

The `archive.s3_key` column stores the path within the S3 bucket. Presigned download URLs are generated on demand from the key — the actual S3 URL is never stored, so rotating buckets or regions requires no schema migration.

---

## Project Roadmap

### Phase A — AWS RDS (complete)
PostgreSQL on AWS RDS. Alembic migrations applied against the cloud database.

### Phase B — S3 Data Lake (complete)
S3-direct pipeline with eTag deduplication. Zero local disk writes. migration 002 makes `local_path` nullable.

### Phase C — REST API (in progress)
Real FastAPI endpoints with asyncpg queries, filtering, pagination, and presigned S3 download URLs per file.

### Phase D — Encrypted Credential Store
AES-256 encrypted Teams credentials and per-student Playwright session state stored in the database. Enables automated re-scraping without interactive login.

### Phase E — EC2 Headless Worker
Playwright running headless on EC2. A cron job polls for new files per student, decrypts credentials, reuses Playwright session cookies where valid, and triggers incremental scrapes.

### Phase F — Student Web Interface
A Next.js frontend where students submit their Teams credentials, and can browse the aggregated file library from all contributors across current and past semesters. File downloads go directly from S3 to the browser via time-limited presigned URLs.

---

## Local Setup

**Prerequisites:** Docker Desktop, Python 3.9+, Git.

```bash
git clone https://github.com/raulkolaric/backup-teams.git
cd backup-teams

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your Teams credentials and AWS keys

docker compose up db -d
alembic upgrade head

python main.py
```

**API server:**

```bash
uvicorn api.main:app --reload
# Docs at http://127.0.0.1:8000/docs
```

**Tests:**

```bash
pytest tests/ -v
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `EMAIL` | Microsoft Teams login email |
| `PASSWORD` | Microsoft Teams login password |
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port |
| `DB_NAME` | Database name (`backup_teams`) |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `DOWNLOAD_ROOT` | Base path for S3 key derivation |
| `DOWNLOAD_CONCURRENCY` | Max parallel Graph API download workers |
| `AWS_ACCESS_KEY_ID` | IAM access key with S3 put/get permissions |
| `AWS_SECRET_ACCESS_KEY` | IAM secret key |
| `AWS_REGION` | S3 and RDS region |
| `S3_BUCKET` | Target S3 bucket name |

Never commit `.env`. It is listed in `.gitignore`.

---

## Repository Structure

```
backup-teams/
  src/
    auth.py              # Playwright session management and token interception
    graph_client.py      # Microsoft Graph API async HTTP client
    teams_scraper.py     # Orchestration: Joined Teams → Channels → files
    downloader.py        # S3-direct pipeline with eTag deduplication
    storage.py           # boto3 S3 operations (upload, exists, presigned URL)
    db.py                # asyncpg pool and all SQL queries
    utils.py             # Path sanitisation and logging setup
  api/
    main.py              # FastAPI application entry point, /health
    routers/
      files.py           # GET /files endpoints
      classes.py         # GET /classes endpoints
  alembic/
    versions/            # Versioned schema migrations
    env.py               # Migration environment (reads DB URL from .env)
  db/
    schema.sql           # Reference schema (Alembic is the source of truth)
  tests/
    test_downloader.py   # Async unit tests for the S3-direct pipeline
  docker-compose.yml     # Local PostgreSQL service
  requirements.txt
```
