"""
main.py — Entry point.

Run with:  python main.py
Or inside Docker: docker compose up scraper
"""
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv(override=True)

from src.utils import setup_logging
from src.auth import get_bearer_token
from src.db import init_pool
from src.graph_client import GraphClient
from src.teams_scraper import scrape_all

log = logging.getLogger("backup_teams")


async def main() -> None:
    setup_logging()

    log.info("─" * 60)
    log.info("  Microsoft Teams File Backup")
    log.info("─" * 60)

    # Step 1 — Auth (Playwright, synchronous, runs before the async loop)
    log.info("Step 1/3 — Acquiring Bearer token via browser…")
    token = get_bearer_token()

    # Step 2 — Database
    log.info("Step 2/3 — Connecting to PostgreSQL…")
    pool = await init_pool()

    # Step 3 — Scrape + Download
    log.info("Step 3/3 — Starting scrape across all teams…")
    async with GraphClient(token) as graph:
        await scrape_all(graph, pool)

    await pool.close()
    log.info("Done. All files are in your downloads folder.")


if __name__ == "__main__":
    asyncio.run(main())