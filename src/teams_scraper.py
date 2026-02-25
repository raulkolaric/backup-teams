"""
src/teams_scraper.py — Top-level orchestration.

Walk order:
  Teams (Curso) → Channels (Class) → File tree (folders + files)

For each Team we:
  1. Upsert a `curso` row in the DB.
  2. Detect the professor from team owners.
  3. Try to list channels. If denied (403), fall back to primary channel.
     Retries up to MAX_CHANNEL_RETRIES times with CHANNEL_RETRY_DELAY seconds
     between attempts before giving up.
  4. For each channel, upsert a `class` row and recursively walk the
     channel's SharePoint drive.

End-of-run report
-----------------
A summary table is printed after all teams are processed, showing:
  - Teams processed / denied
  - Files new (uploaded to S3) / skipped / errored
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from uuid import UUID

import asyncpg

from src.graph_client import GraphClient
from src import db as db_mod
from src import downloader
from src.utils import build_local_path, get_download_root, sanitize

log = logging.getLogger("backup_teams.scraper")

DOWNLOAD_CONCURRENCY  = int(os.getenv("DOWNLOAD_CONCURRENCY", "4"))
MAX_CHANNEL_RETRIES   = 2
CHANNEL_RETRY_DELAY   = 5   # seconds between retry attempts


# ─── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class ScrapingStats:
    teams_total:   int = 0
    teams_denied:  int = 0   # 403 even after retries — no files recovered
    teams_fallback: int = 0  # 403 on channels but primary channel worked
    channels_total: int = 0
    files_new:     int = 0   # successfully uploaded to S3
    files_skipped: int = 0   # etag match — already current
    files_error:   int = 0   # download or S3 failure

    def report(self) -> str:
        lines = [
            "",
            "=" * 58,
            "  Scrape Complete — Summary",
            "=" * 58,
            f"  Teams processed :  {self.teams_total}",
            f"  Teams denied    :  {self.teams_denied}   (no channel access after retries)",
            f"  Teams fallback  :  {self.teams_fallback} (primary channel only)",
            f"  Channels walked :  {self.channels_total}",
            "-" * 58,
            f"  New files       :  {self.files_new}",
            f"  Skipped (same)  :  {self.files_skipped}",
            f"  Errors          :  {self.files_error}",
            "=" * 58,
            "",
        ]
        return "\n".join(lines)


# ─── Professor detection ───────────────────────────────────────────────────────

async def _resolve_professor(
    graph: GraphClient,
    pool: asyncpg.Pool,
    team_id: str,
) -> Optional[UUID]:
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
    stats: ScrapingStats,
    *,
    drive_id: str,
    item_id: str,
    class_id: UUID,
    local_base: Path,
) -> None:
    try:
        children = await graph.list_drive_children(drive_id, item_id)
    except Exception as exc:
        log.error("Failed to list folder contents (item %s): %s", item_id, exc)
        return

    tasks = []
    for child in children:
        name = sanitize(child["name"])

        if "folder" in child:
            sub_folder = local_base / name
            sub_folder.mkdir(parents=True, exist_ok=True)
            tasks.append(
                _walk_folder(
                    graph, pool, semaphore, stats,
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
                    graph, pool, semaphore, stats,
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
    stats: ScrapingStats,
    **kwargs,
) -> None:
    async with semaphore:
        try:
            result = await downloader.download_item(graph, pool, **kwargs)
            if result == "ok":
                stats.files_new += 1
            elif result == "skip":
                stats.files_skipped += 1
            else:
                stats.files_error += 1
        except Exception as exc:
            item_name = kwargs.get("item", {}).get("name", "unknown")
            log.error("Failed to download %s: %s", item_name, exc)
            stats.files_error += 1


# ─── Channel listing with retry + fallback ────────────────────────────────────

async def _get_channels_with_fallback(
    graph: GraphClient,
    team_id: str,
    team_name: str,
    stats: ScrapingStats,
) -> Optional[list]:
    """
    Try to list all channels. On 403, retry up to MAX_CHANNEL_RETRIES times
    with CHANNEL_RETRY_DELAY seconds between attempts.

    If all retries fail, fall back to the primary channel.
    Returns a list of channel dicts, or None if access is fully denied.
    """
    last_exc = None

    for attempt in range(1, MAX_CHANNEL_RETRIES + 2):   # +2 = initial + retries
        try:
            return await graph.list_channels(team_id)
        except Exception as exc:
            last_exc = exc
            is_forbidden = "403" in str(exc) or "Forbidden" in str(exc)

            if attempt <= MAX_CHANNEL_RETRIES and is_forbidden:
                log.warning(
                    "Channel list denied for %s — retrying in %ds (attempt %d/%d)",
                    team_name, CHANNEL_RETRY_DELAY, attempt, MAX_CHANNEL_RETRIES + 1,
                )
                await asyncio.sleep(CHANNEL_RETRY_DELAY)
            else:
                break

    # All retries exhausted — try primary channel fallback
    log.warning(
        "Channel list denied for %s after %d attempts — trying primary channel",
        team_name, MAX_CHANNEL_RETRIES + 1,
    )
    try:
        primary = await graph.get_primary_channel(team_id)
        stats.teams_fallback += 1
        return [primary]
    except Exception as fallback_exc:
        log.error(
            "Primary channel also denied for %s: %s",
            team_name, fallback_exc,
        )
        stats.teams_denied += 1
        return None


# ─── Site-level drive enumeration ─────────────────────────────────────────────

# Library names that are the default Teams document library — already covered
# by the channel filesFolder walk, so we skip them here to avoid duplicates.
_DEFAULT_LIBRARY_NAMES = {"documents", "arquivos", "documentos"}


async def _process_site_drives(
    graph: GraphClient,
    pool: asyncpg.Pool,
    semaphore: asyncio.Semaphore,
    stats: ScrapingStats,
    *,
    team_id: str,
    curso_id: UUID,
    professor_id: Optional[UUID],
    download_root: str,
    curso_name: str,
) -> None:
    """
    Enumerate ALL SharePoint document libraries for a team's site and walk
    each non-default one.

    This recovers files from custom libraries (e.g. 'Material de Aula') that
    are invisible via the standard channel /filesFolder endpoint.

    Strategy:
      1. GET /teams/{id}/drive          → get siteId from parentReference
      2. GET /sites/{siteId}/drives     → list all document libraries
      3. Walk each library that isn't the default Teams Documents folder
    """
    try:
        team_drive = await graph.get_team_drive(team_id)
    except Exception as exc:
        log.debug("[DRIVES] Could not get team drive for %s: %s", curso_name, exc)
        return

    site_id = team_drive.get("parentReference", {}).get("siteId")
    if not site_id:
        log.debug("[DRIVES] No siteId found for %s", curso_name)
        return

    try:
        drives = await graph.list_site_drives(site_id)
    except Exception as exc:
        log.debug("[DRIVES] Could not list site drives for %s: %s", curso_name, exc)
        return

    log.debug("[DRIVES] %s — found %d libraries", curso_name, len(drives))

    for drive in drives:
        drive_name = drive.get("name", "unknown")
        drive_id   = drive["id"]

        if drive_name.lower() in _DEFAULT_LIBRARY_NAMES:
            log.debug("[DRIVES] Skipping default library %r for %s", drive_name, curso_name)
            continue

        log.info("[DRIVES] %s — walking library %r", curso_name, drive_name)

        class_id = await db_mod.upsert_class(
            pool,
            name=drive_name,
            curso_id=curso_id,
            professor_id=professor_id,
            semester=os.getenv("DEFAULT_SEMESTER", "Unknown"),
            class_year=int(os.getenv("DEFAULT_YEAR", "2025")),
            teams_channel_id=f"drive:{drive_id}",
        )

        try:
            root = await graph.get_drive_root(drive_id)
        except Exception as exc:
            log.warning("[DRIVES] Could not get root of %r: %s", drive_name, exc)
            continue

        local_base = build_local_path(download_root, curso_name, drive_name)

        await _walk_folder(
            graph, pool, semaphore, stats,
            drive_id=drive_id,
            item_id=root["id"],
            class_id=class_id,
            local_base=local_base,
        )


# ─── Channel processing ────────────────────────────────────────────────────────

async def _process_channel(
    graph: GraphClient,
    pool: asyncpg.Pool,
    semaphore: asyncio.Semaphore,
    stats: ScrapingStats,
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
    stats.channels_total += 1

    class_id = await db_mod.upsert_class(
        pool,
        name=channel_name,
        curso_id=curso_id,
        professor_id=professor_id,
        semester=os.getenv("DEFAULT_SEMESTER", "Unknown"),
        class_year=int(os.getenv("DEFAULT_YEAR", "2025")),
        teams_channel_id=channel_id,
    )

    try:
        files_folder = await graph.get_files_folder(team_id, channel_id)
    except Exception as exc:
        log.warning("    No files folder for channel %s: %s", channel_name, exc)
        return

    drive_id     = files_folder["parentReference"]["driveId"]
    root_item_id = files_folder["id"]
    local_base   = build_local_path(download_root, curso_name, channel_name)

    await _walk_folder(
        graph, pool, semaphore, stats,
        drive_id=drive_id,
        item_id=root_item_id,
        class_id=class_id,
        local_base=local_base,
    )


# ─── Public entry point ────────────────────────────────────────────────────────

async def scrape_all(
    graph: GraphClient,
    pool: asyncpg.Pool,
) -> ScrapingStats:
    """
    Main orchestration loop.
    Returns a ScrapingStats object with the final counts.
    """
    download_root = get_download_root()
    semaphore     = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    stats         = ScrapingStats()

    log.info("Fetching joined teams…")
    teams = await graph.list_joined_teams()
    log.info("Found %d teams.", len(teams))

    for team in teams:
        team_id   = team["id"]
        team_name = team.get("displayName", "unknown-team")
        log.info("Team: %s", team_name)
        stats.teams_total += 1

        curso_id     = await db_mod.upsert_curso(pool, name=team_name, teams_id=team_id)
        professor_id = await _resolve_professor(graph, pool, team_id)

        # ── Channel path (standard) ───────────────────────────────────────────
        channels = await _get_channels_with_fallback(graph, team_id, team_name, stats)
        if channels is not None:
            channel_tasks = [
                _process_channel(
                    graph, pool, semaphore, stats,
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

        # ── Site drives path (supplementary) ──────────────────────────────────
        # Always run — catches custom libraries (e.g. 'Material de Aula') that
        # are invisible via channel /filesFolder. eTag dedup prevents re-uploads.
        await _process_site_drives(
            graph, pool, semaphore, stats,
            team_id=team_id,
            curso_id=curso_id,
            professor_id=professor_id,
            download_root=download_root,
            curso_name=team_name,
        )

    log.info("All teams processed.")
    return stats
