"""
src/teams_scraper.py — Top-level orchestration.

Walk order:
  Teams (Curso) → Channels (Class) → File tree (folders + files)
  + Site Drives (supplementary) — catches custom SharePoint libraries

Site drives strategy:
  The primary path to custom document libraries (e.g. 'Material de Aula') is
  through the siteId embedded in the filesFolder response we already retrieve
  during channel processing. This avoids the broken /teams/{id}/drive endpoint
  which returns 404 when the institution uses non-standard SharePoint provisioning.

  For teams where channels are denied (403), we fall back to the /groups/{id}/drive
  path (same group ID as the team ID) which has different routing and often
  succeeds where /teams/{id}/drive fails.

End-of-run report:
  Printed after all teams are processed: teams, channels, files, errors.
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
MAX_CHANNEL_RETRIES   = 0
CHANNEL_RETRY_DELAY   = 5


# ─── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class ScrapingStats:
    teams_total:    int = 0
    teams_denied:   int = 0
    teams_fallback: int = 0
    channels_total: int = 0
    files_new:      int = 0
    files_skipped:  int = 0
    files_error:    int = 0

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
    item      = kwargs.get("item", {})
    item_id   = item["id"]
    etag      = item.get("eTag", item.get("id"))
    file_name = item.get("name", "unknown")

    # ── Etag check BEFORE semaphore ───────────────────────────────────────────
    # is_file_current is a cheap DB read — no reason to rate-limit it.
    # Running all etag checks in parallel means 1000+ skip decisions happen
    # concurrently; only files that actually need downloading enter the queue.
    if await db_mod.is_file_current(pool, item_id, etag):
        log.info("[SKIP] %s (etag matches — already in S3)", file_name)
        stats.files_skipped += 1
        return

    # ── Actual download+upload is rate-limited by semaphore ──────────────────
    async with semaphore:
        try:
            result = await downloader.download_item(graph, pool, **kwargs)
            if result == "ok":
                stats.files_new += 1
            elif result == "skip":
                # download_item did its own etag check (race condition safe)
                stats.files_skipped += 1
            else:
                stats.files_error += 1
        except Exception as exc:
            log.error("Failed to download %s: %s", file_name, exc)
            stats.files_error += 1


# ─── Channel listing with retry + fallback ────────────────────────────────────

async def _get_channels_with_fallback(
    graph: GraphClient,
    team_id: str,
    team_name: str,
    stats: ScrapingStats,
) -> Optional[list]:
    last_exc = None

    for attempt in range(1, MAX_CHANNEL_RETRIES + 2):
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


# ─── Site drives enumeration ───────────────────────────────────────────────────

_DEFAULT_LIBRARY_NAMES = {"documents", "arquivos", "documentos"}


async def _get_site_id_for_team(
    graph: GraphClient,
    team_id: str,
    team_name: str,
    known_site_id: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve the SharePoint siteId for a team.

    Priority:
      1. known_site_id  — extracted from a filesFolder response (fast path)
      2. Parse webUrl from /groups/{id}/drive or /teams/{id}/drive response
         → GET /sites/{host}:{site_path} to get the real site ID

    The siteId fields (parentReference.siteId, sharePointIds) are null for
    institutions with non-standard SharePoint provisioning. The webUrl is
    always present and gives us enough to resolve the site.
    """
    from urllib.parse import urlparse

    if known_site_id:
        return known_site_id

    for label, drive_coro in [
        ("groups drive", graph.get_group_drive(team_id)),
        ("teams drive",  graph.get_team_drive(team_id)),
    ]:
        try:
            drive = await drive_coro
        except Exception as exc:
            log.debug("[DRIVES] %s failed for %s: %s", label, team_name, exc)
            continue

        # Try direct siteId fields first (standard provisioning)
        site_id = (
            (drive.get("parentReference") or {}).get("siteId")
            or (drive.get("sharePointIds") or {}).get("siteId")
        )
        if site_id:
            log.debug("[DRIVES] Got siteId via %s fields for %s", label, team_name)
            return site_id

        # Fall back to webUrl parsing (always present, even when siteId is null)
        web_url = drive.get("webUrl", "")
        if "/sites/" in web_url:
            parsed = urlparse(web_url)
            # webUrl is like: https://pucsp.sharepoint.com/sites/452516_4385_2/Documentos...
            # We want the site path:  /sites/452516_4385_2
            path_parts = parsed.path.split("/")
            site_path = "/" + "/".join(path_parts[1:3])   # [sites, name]
            try:
                site = await graph.get_site_by_url(parsed.hostname, site_path)
                site_id = site.get("id")
                if site_id:
                    log.debug(
                        "[DRIVES] Got siteId via webUrl (%s) for %s",
                        web_url, team_name,
                    )
                    return site_id
            except Exception as exc:
                log.debug("[DRIVES] webUrl site resolve failed for %s: %s", team_name, exc)

    log.warning("[DRIVES] Could not resolve siteId for %s", team_name)
    return None


async def _process_site_drives(
    graph: GraphClient,
    pool: asyncpg.Pool,
    semaphore: asyncio.Semaphore,
    stats: ScrapingStats,
    *,
    team_id: str,
    team_name: str,
    curso_id: UUID,
    professor_id: Optional[UUID],
    download_root: str,
    known_site_id: Optional[str] = None,
) -> None:
    """
    Walk all non-default SharePoint document libraries for a team's site.

    Uses known_site_id (from filesFolder) when available — avoids the broken
    /teams/{id}/drive path. Falls back to /groups/{id}/drive if needed.
    """
    site_id = await _get_site_id_for_team(
        graph, team_id, team_name, known_site_id
    )
    if not site_id:
        return

    try:
        drives = await graph.list_site_drives(site_id)
    except Exception as exc:
        log.warning("[DRIVES] Could not list site drives for %s: %s", team_name, exc)
        return

    for drive in drives:
        drive_name = drive.get("name", "unknown")
        drive_id   = drive["id"]

        if drive_name.lower() in _DEFAULT_LIBRARY_NAMES:
            continue

        log.info("[DRIVES] %s — walking library %r", team_name, drive_name)

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

        local_base = build_local_path(download_root, team_name, drive_name)

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
) -> Optional[str]:
    """
    Process a single channel's file tree.
    Returns the SharePoint siteId discovered from the filesFolder response,
    or None if the filesFolder could not be retrieved.
    This siteId is used by the site drives pass to find custom libraries.
    """
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
        return None

    drive_id     = files_folder["parentReference"]["driveId"]
    root_item_id = files_folder["id"]

    # ── KEY: extract siteId from the filesFolder response ─────────────────────
    # This is the actual SharePoint site ID for this team, regardless of whether
    # /teams/{id}/drive works or not. Used by _process_site_drives.
    site_id = files_folder.get("parentReference", {}).get("siteId")

    local_base = build_local_path(download_root, curso_name, channel_name)

    await _walk_folder(
        graph, pool, semaphore, stats,
        drive_id=drive_id,
        item_id=root_item_id,
        class_id=class_id,
        local_base=local_base,
    )

    return site_id


# ─── Public entry point ────────────────────────────────────────────────────────

async def _process_team(
    graph: GraphClient,
    pool: asyncpg.Pool,
    semaphore: asyncio.Semaphore,
    stats: ScrapingStats,
    team: dict,
    download_root: str,
) -> None:
    """Process a single team: channels + site drives. Called concurrently."""
    team_id   = team["id"]
    team_name = team.get("displayName", "unknown-team")

    log.info("Team: %s", team_name)
    stats.teams_total += 1

    curso_id     = await db_mod.upsert_curso(pool, name=team_name, teams_id=team_id)
    professor_id = await _resolve_professor(graph, pool, team_id)

    # ── Channel pass ─────────────────────────────────────────────────────────
    channels = await _get_channels_with_fallback(graph, team_id, team_name, stats)

    known_site_id: Optional[str] = None

    if channels is not None:
        channel_results = await asyncio.gather(
            *[
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
            ],
            return_exceptions=True,
        )
        for result in channel_results:
            if isinstance(result, str) and result:
                known_site_id = result
                break

    # ── Site drives pass ──────────────────────────────────────────────────────
    await _process_site_drives(
        graph, pool, semaphore, stats,
        team_id=team_id,
        team_name=team_name,
        curso_id=curso_id,
        professor_id=professor_id,
        download_root=download_root,
        known_site_id=known_site_id,
    )


async def scrape_all(
    graph: GraphClient,
    pool: asyncpg.Pool,
) -> ScrapingStats:
    """
    Main orchestration loop — all teams processed concurrently.

    The download semaphore (DOWNLOAD_CONCURRENCY) controls actual file I/O.
    Team-level API calls (channel listing, drives, filesFolder) run in
    parallel across all teams, eliminating idle wait time between them.
    """
    download_root = get_download_root()
    semaphore     = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    stats         = ScrapingStats()

    log.info("Fetching joined teams…")
    teams = await graph.list_joined_teams()
    log.info("Found %d teams — processing concurrently.", len(teams))

    await asyncio.gather(
        *[
            _process_team(graph, pool, semaphore, stats, team, download_root)
            for team in teams
        ],
        return_exceptions=True,
    )

    log.info("All teams processed.")
    return stats
