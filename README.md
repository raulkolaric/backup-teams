# Backup Teams Architecture

An automated, cloud-native pipeline and REST API for archiving Microsoft Teams course files. Built in Python (FastAPI, `asyncio`), the system intercepts network traffic to capture OAuth tokens, enumerates the Microsoft Graph API to acquire file streams, pipes them directly to Amazon S3, and stores normalized metadata with full-text search capabilities in an AWS RDS PostgreSQL database.

This system guarantees data persistence, ensuring academic files do not become permanently inaccessible upon course deprecation or channel deletion.

---

## Technical Capabilities

**1. Full-Text Search (FTS) with Paragraph Extraction**
The database leverages PostgreSQL 16 GIN (`Generalized Inverted Index`) indexes over `tsvector` document representations. The `/search` API endpoint utilizes Common Table Expressions (CTEs) to segment document strings, compute `ts_rank_cd` density per segment, and return the optimal contextual paragraph containing the matched keywords with native HTML `<b>` highlighting. This emulates an Elasticsearch fragmenting behavior purely within the SQL layer.

**2. Automated EC2 CI/CD Deployment**
The API is containerized natively and deployed via a continuous delivery pipeline (`.github/workflows/deploy.yml`). Pushing to the `main` branch triggers a GitHub Action runner that establishes an SSH connection to the AWS EC2 Host, pulls the latest repository commit, and re-allocates the FastAPI Docker container dynamically resulting in near-zero downtime deployments.

**3. S3-Direct Streams & eTag Deduplication**
The downloader sub-system explicitly bypasses local block storage. File streams acquired from the Graph API are piped directly into Amazon S3 standard storage instances. Incremental runs rely on upstream `eTag` metadata hashing; if a file's eTag is locally cached in PostgreSQL and remains unchanged remotely, network I/O is skipped.

---

## System Architecture

1. **Token Interceptor:** Operates Playwright to transparently intercept Bearer tokens from the Teams web client session traffic.
2. **Asynchronous Crawler:** Recursively enumerates Teams, Channels, and Files via `httpx` HTTP/2 clients.
3. **Data Lake (Amazon S3):** Receives chunked file byte streams directly from upstream APIs.
4. **Relational Meta-Store (Amazon RDS):** Stores structured relational data mapping courses, channel hierarchies, file URIs, and extracted PDF text. Controlled exclusively via Alembic schema migrations.
5. **API Gateway:** A parallelized asynchronous FastAPI application hosted on an AWS EC2 instance, serving REST endpoints and generating time-constrained presigned S3 download URLs.

---

## API Endpoints
*The API interface and schema are documented via OpenAPI UI at `/docs`.*

| Endpoint | Protocol | Description |
| :--- | :--- | :--- |
| `/health` | GET | Container liveness HTTP probe |
| `/search/?q=...` | GET | `tsvector` FTS query. Returns array of structurally bounded semantic paragraphs |
| `/cursos/` | GET | Array of all root-level Microsoft Teams schemas |
| `/cursos/{id}/classes` | GET | Array of Child Channel associations for a parent Team ID |
| `/files/{id}` | GET | Resolves metadata and an ephemeral presigned S3 object URL |
| `/stats/` | GET | Aggregate database ingestion and metadata metrics |

---

## Infrastructure Stack

- **Backend Logic:** Python 3.12, FastAPI, `asyncio`, Uvicorn
- **Persistent Data:** PostgreSQL 16 (AWS RDS), `asyncpg`, Alembic 
- **Cloud Infrastructure:** AWS S3 (`boto3`), AWS EC2 (Ubuntu 24.04), Docker Engine Component 
- **Extraction Protocol:** Playwright (Chromium), `httpx` (HTTP/2), `pdfminer.six`

---

## Deployment Parameters

**Prerequisites:** Docker Engine, Python 3.12, Git.

**Local Instantiation:**
```bash
git clone https://github.com/raulkolaric/backup-teams.git
cd backup-teams
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Provision environment vars containing AWS/PG URI strings
cp .env.example .env

# Bootstrap stateful PG container
docker compose up db -d
alembic upgrade head

# Initialize API Server
uvicorn api.main:app --reload

# Execute Test Runner
pytest tests/ -v
```

**Production Configuration:**
The production deployment utilizes `docker-compose.prod.yml` to instruct the Docker daemon to attach the container directly to TCP port 80, bypassing standard application-level reverse proxies.
