"""
main.py — Entry point.

Auth runs FIRST, synchronously (Playwright Sync API cannot run inside an
asyncio event loop). Once we have the token, we enter the async world.

Run with:  python main.py
"""
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv(override=True)

import os
from src.utils import setup_logging
from src.auth import get_bearer_token
from src.db import init_pool, get_system_token
from src.graph_client import GraphClient
from src.teams_scraper import scrape_all

log = logging.getLogger("backup_teams")


from src.indexer import run_incremental

async def _async_main(token: str = None) -> None:
    """Everything that runs inside the asyncio event loop."""
    log.info("Step 2/4 — Connecting to PostgreSQL…")
    pool = await init_pool()
    
    if os.getenv("SERVER_MODE", "").lower() == "true":
        admin_email = os.environ.get("EMAIL")
        log.info(f"SERVER_MODE=True. Fetching vaulted token from Postgres for {admin_email}...")
        token = await get_system_token(pool, admin_email)
        if not token:
            log.error("No vaulted token found in database. Run the Chrome Extension to sync your token.")
            await pool.close()
            return

    if not token:
        log.error("No token provided to start the graph client.")
        await pool.close()
        return

    log.info("Step 3/4 — Starting scrape across all teams…")
    async with GraphClient(token) as graph:
        stats = await scrape_all(graph, pool)

    log.info("Step 4/4 — Indexing new PDFs for full-text search…")
    indexed_count = await run_incremental(pool)
    log.info("Indexed %d new PDFs.", indexed_count)

    await pool.close()
    log.info(stats.report())



def main() -> None:
    setup_logging()

    log.info("─" * 60)
    log.info("  Microsoft Teams File Backup")
    log.info("─" * 60)

    # Step 1 — Auth
    if os.getenv("SERVER_MODE", "").lower() == "true":
        log.info("SERVER_MODE enabled. Skipping local Playwright extraction.")
        token = None
    else:
        # Playwright Sync API cannot be used inside asyncio.run().
        log.info("Step 1/3 — Acquiring Bearer token via browser…")
        token = get_bearer_token()

    # Step 2 & 3 — Everything else is async.
    asyncio.run(_async_main(token))


if __name__ == "__main__":
    main()