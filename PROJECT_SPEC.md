# Project Specification: Backup Teams Ecosystem

## 1. Mission Statement
Backup Teams is a high-performance archival engine designed to preserve educational resources from Microsoft Teams. It automates the extraction, indexing, and serving of course materials, ensuring accessibility beyond the lifespan of the original course channels.

---

## 2. System Architecture

### A. The Ingestion Engine (Scraper)
- **Runtime:** Python 3.12 (Headless Execution)
- **Discovery:** Intercepts OAuth tokens from Teams v2 web client via Playwright.
- **Traversal:** Recursively crawls Microsoft Graph API (Teams -> Channels -> Files).
- **Efficiency:** Uses **eTag-based deduplication**. Before downloading, the scraper checks the RDS metadata; if the eTag matches, the file is skipped to conserve bandwidth and storage.
- **Data Flow:** Streams bytes directly from Graph API to **Amazon S3** (Zero local disk writes).

### B. The Logic Layer (REST API)
- **Runtime:** FastAPI (Asynchronous Python)
- **Hosting:** AWS EC2 (Ubuntu 24.04).
- **Search Logic:** Implements a context-aware **Full-Text Search (FTS)**. 
    - Text is extracted from PDFs via `pdfminer.six` and stored in a GIN-indexed `tsvector` column.
    - Matches are resolved using **PostgreSQL CTEs** that split documents into logical paragraphs (`\n\n`) and return the most relevant paragraph as a highlighted snippet.
- **Security:** Serves time-limited **S3 Presigned URLs** for secure file downloads.

### C. The Interface Layer (UI - Planned)
- **Framework:** Next.js 14+ (TypScript, Tailwind CSS).
- **Hosting:** Vercel (Edge-optimized delivery).
- **Integration:** Bridges to the Backend using a generated TypeScript client from the FastAPI OpenAPI (Swagger) schema.

---

## 3. Infrastructure source of truth

- **Identity:** AWS Elastic IP (Static identifier for the EC2 Host).
- **Database:** Amazon RDS (PostgreSQL 16) in a private security group.
- **Storage:** Amazon S3 (Binary Large Object store).
- **Automation:** GitHub Actions (`deploy.yml`) for SSH-based container hot-swaps on the EC2 host.

---

## 4. Key Logic Patterns

### Paragraph-Level Search (CTE Pattern)
Instead of standard FTS excerpts, the system treats paragraphs as first-class citizens:
1. Filter top 20 candidate documents by rank.
2. Segment document text into arrays using `\n\n`.
3. Compute rank for each individual segment.
4. Return the "winning" segment as the `excerpt`.
 
### The "Senior" Deployment Standard
- **Backend:** Managed as an immutable container on EC2.
- **Frontend:** Managed as an edge-optimized static site on Vercel.
- **Obfuscation:** DNS Subdomains + Cloudflare Proxy (Planned) to shield the origin Elastic IP.

---

## 5. Roadmap
- [x] Phase 1: Core Scraper & S3 Uploads
- [x] Phase 2: RDS Metadata & full-text search
- [x] Phase 3: Paragraph-segment extraction API
- [x] Phase 4: CI/CD Pipeline to AWS EC2
- [ ] Phase 5: Next.js Discovery Interface
- [ ] Phase 6: Domain mapping and Cloudflare CDN
