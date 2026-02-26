# Backup Teams

An automated, cloud-native pipeline and REST API for archiving Microsoft Teams course files. Built in Python (FastAPI + `asyncio`), the system intercepts network traffic to capture OAuth tokens, crawls the Microsoft Graph API to download files, streams them directly to Amazon S3, and stores metadata with full-text search capabilities in an AWS RDS PostgreSQL database.

This project exists to ensure files do not become permanently inaccessible when a course ends or a channel is closed.

---

## 🚀 Recent Accomplishments

- **Full-Text Search (FTS) with Paragraph Extraction:** Upgraded the PostgreSQL database to use GIN indexes and `tsvector`. The `/search` API endpoint now uses complex Common Table Expressions (CTEs) to isolate and return the exact contextual paragraph containing matched keywords with native `<b>` highlighting (simulating an Elasticsearch experience).
- **Automated EC2 Deployment (CI/CD):** Established a "Vercel-like" deployment pipeline mimicking PaaS simplicity. Pushes to `main` trigger a GitHub Action that automatically SSHs into an AWS EC2 instance, pulls the latest code, and hot-swaps the FastAPI Docker container without downtime.
- **S3-Direct Streaming:** Transformed the downloader to bypass local disk storage entirely. File bytes stream from the Graph API directly into Amazon S3, skipping the local filesystem and relying on `eTag` hashing to prevent redundant uploads.

---

## 🏗️ Architecture

1. **Scraper (Offline Job):** Uses Playwright to transparently intercept Bearer tokens from the Teams web client. Recursively enumerates Teams, Channels, and Files.
2. **Data Lake:** Streams file bytes directly to **Amazon S3**.
3. **Database:** Stores structured course hierarchy, file metadata, and extracted PDF text in **Amazon RDS (PostgreSQL 16)**. Managed by Alembic migrations.
4. **API Backend:** A fast, asynchronous **FastAPI** application hosted on an **AWS EC2** instance via Docker Compose, serving REST endpoints and generating time-limited presigned S3 download URLs.

---

## 🔌 API Endpoints
*The API is fully documented via Swagger at `/docs`.*

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/health` | GET | Liveness probe |
| `/search/?q=term` | GET | Full-text search across all PDF contents. Returns highlighted snippet paragraphs. |
| `/cursos/` | GET | List all extracted Microsoft Teams |
| `/cursos/{id}/classes` | GET | List all Channels within a Team |
| `/files/{id}` | GET | Retrieve metadata and a presigned S3 download URL for a file |
| `/stats/` | GET | High-level database statistics |

---

## 💻 Tech Stack

- **Backend:** Python 3.12, FastAPI, `asyncio`, Uvicorn
- **Database:** PostgreSQL 16 (AWS RDS), `asyncpg`, Alembic (Migrations)
- **Cloud/Infra:** AWS S3 (`boto3`), AWS EC2, Docker Compose, GitHub Actions
- **Scraping:** Playwright (Chromium), `httpx` (HTTP/2), `pdfminer.six`

---

## 🛠️ Local Setup

**Prerequisites:** Docker Desktop, Python 3.12, Git.

```bash
# Clone and setup env
git clone https://github.com/raulkolaric/backup-teams.git
cd backup-teams
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment variables (DB credentials, AWS Keys)
cp .env.example .env

# Run local database and apply migrations
docker compose up db -d
alembic upgrade head

# Run the API locally
uvicorn api.main:app --reload

# Run the tests
pytest tests/ -v
```

## 🚢 Deployment

The API is structured for continuous deployment via GitHub Actions (`.github/workflows/deploy.yml`). 
Pushing to the `main` branch automatically rebuilds the `docker-compose.prod.yml` payload on the target EC2 instance.
