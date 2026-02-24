"""
src/teams_scraper.py — Top-level orchestration.

Walk order:
  Teams (Curso) → Channels (Class) → File tree (folders + files)

For each Team we:
  1. Upsert a `curso` row in the DB.
  2. Detect the professor from team owners.
  3. For each standard channel, upsert a `class` row.
  4. Recursively walk the channel's SharePoint drive and download every file
     via downloader.download_item().

Semaphore Controls
------------------
We limit concurrent downloads to DOWNLOAD_CONCURRENCY (default 4) to stay
within Graph API rate limits without being unnecessarily slow.
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from uuid import UUID

import asyncpg

from src.graph_client import GraphClient
from src import db as db_mod
from src import downloader
from src.utils import build_local_path, get_download_root, sanitize

log = logging.getLogger("backup_teams.scraper")

DOWNLOAD_CONCURRENCY = int(os.getenv("DOWNLOAD_CONCURRENCY", "4"))


# ─── Professor detection ───────────────────────────────────────────────────────

async def _resolve_professor(
    graph: GraphClient,
    pool: asyncpg.Pool,
    team_id: str,
) -> Optional[UUID]:
    """
    Find the first team owner and upsert them as a professor.
    Returns their UUID, or None if no owner info is available.
    """
    try:
        members = await graph.get_team_members(team_id)
        for m in members:
            if "owner" in m.get("roles", []):
                name  = m.get("displayName", "Unknown")
                email = m.get("email") or m.get("userId", "unknown@unknown.com")
                return await db_mod.upsert_professor(pool, name=name, email=email)
    except Exception as exc:
        log.warning("Could not fetch team members for %s: %s", team_id, exc)
    return None


# ─── Recursive folder walk ─────────────────────────────────────────────────────

async def _walk_folder(
    graph: GraphClient,
    pool: asyncpg.Pool,
    semaphore: asyncio.Semaphore,
    *,
    drive_id: str,
    item_id: str,
    class_id: UUID,
    local_base: Path,
) -> None:
    """
    Recursively walk a drive folder.
    - Folders  → recurse (creating matching local sub-directories).
    - Files    → download via downloader (respecting the semaphore).
    """
    try:
        children = await graph.list_drive_children(drive_id, item_id)
    except Exception as exc:
        log.error("Failed to list folder contents (item %s): %s", item_id, exc)
        return

    tasks = []
    for child in children:
        name = sanitize(child["name"])

        if "folder" in child:
            # Recurse into sub-folder
            sub_folder = local_base / name
            sub_folder.mkdir(parents=True, exist_ok=True)
            tasks.append(
                _walk_folder(
                    graph, pool, semaphore,
                    drive_id=drive_id,
                    item_id=child["id"],
                    class_id=class_id,
                    local_base=sub_folder,
                )
            )
        elif "file" in child:
            local_path = local_base / name
            tasks.append(
                _download_with_semaphore(
                    graph, pool, semaphore,
                    drive_id=drive_id,
                    item=child,
                    class_id=class_id,
                    local_path=local_path,
                )
            )

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _download_with_semaphore(
    graph: GraphClient,
    pool: asyncpg.Pool,
    semaphore: asyncio.Semaphore,
    **kwargs,
) -> None:
    """Wrap download_item with the concurrency semaphore."""
    async with semaphore:
        try:
            await downloader.download_item(graph, pool, **kwargs)
        except Exception as exc:
            item_name = kwargs.get("item", {}).get("name", "unknown")
            log.error("Failed to download %s: %s", item_name, exc)


# ─── Channel processing ────────────────────────────────────────────────────────

async def _process_channel(
    graph: GraphClient,
    pool: asyncpg.Pool,
    semaphore: asyncio.Semaphore,
    *,
    team_id: str,
    channel: dict,
    curso_id: UUID,
    professor_id: Optional[UUID],
    download_root: str,
    curso_name: str,
) -> None:
    channel_id   = channel["id"]
    channel_name = channel.get("displayName", "unknown-channel")

    log.info("  Channel: %s", channel_name)

    # ── Register class in DB ──────────────────────────────────────────────────
    class_id = await db_mod.upsert_class(
        pool,
        name=channel_name,
        curso_id=curso_id,
        professor_id=professor_id,
        semester=os.getenv("DEFAULT_SEMESTER", "Unknown"),
        class_year=int(os.getenv("DEFAULT_YEAR", "2025")),
        teams_channel_id=channel_id,
    )

    # ── Get the SharePoint drive root for this channel ────────────────────────
    try:
        files_folder = await graph.get_files_folder(team_id, channel_id)
    except Exception as exc:
        log.warning("    No files folder for channel %s: %s", channel_name, exc)
        return

    drive_id    = files_folder["parentReference"]["driveId"]
    root_item_id = files_folder["id"]

    local_base = build_local_path(download_root, curso_name, channel_name)

    await _walk_folder(
        graph, pool, semaphore,
        drive_id=drive_id,
        item_id=root_item_id,
        class_id=class_id,
        local_base=local_base,
    )


# ─── Public entry point ────────────────────────────────────────────────────────

async def scrape_all(
    graph: GraphClient,
    pool: asyncpg.Pool,
) -> None:
    """
    Main orchestration loop:
      For every joined Team → every Channel → walk files and download.
    """
    download_root = get_download_root()
    semaphore     = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

    log.info("Fetching joined teams…")
    teams = await graph.list_joined_teams()
    log.info("Found %d teams.", len(teams))

    for team in teams:
        team_id   = team["id"]
        team_name = team.get("displayName", "unknown-team")
        log.info("Team: %s", team_name)

        # ── Upsert curso ──────────────────────────────────────────────────────
        curso_id = await db_mod.upsert_curso(pool, name=team_name, teams_id=team_id)

        # ── Find professor (team owner) ───────────────────────────────────────
        professor_id = await _resolve_professor(graph, pool, team_id)

        # ── Process each channel ──────────────────────────────────────────────
        try:
            channels = await graph.list_channels(team_id)
        except Exception as exc:
            log.error("Could not list channels for %s: %s", team_name, exc)
            continue

        channel_tasks = [
            _process_channel(
                graph, pool, semaphore,
                team_id=team_id,
                channel=ch,
                curso_id=curso_id,
                professor_id=professor_id,
                download_root=download_root,
                curso_name=team_name,
            )
            for ch in channels
        ]
        await asyncio.gather(*channel_tasks, return_exceptions=True)

    log.info("All teams processed. Backup complete.")
