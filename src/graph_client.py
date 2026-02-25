"""
src/graph_client.py — Async Microsoft Graph API wrapper.

Responsibilities
----------------
- Hold the Bearer token and attach it to every request.
- Provide one method per Graph endpoint we need.
- Handle HTTP 429 (rate limit) with exponential back-off (up to 5 retries).
- Raise clear errors on 401 (token expired) so the caller can re-auth.
"""
import asyncio
import logging
from typing import Any, Optional, List

import httpx

log = logging.getLogger("backup_teams.graph")

BASE_URL = "https://graph.microsoft.com/v1.0"
MAX_RETRIES = 5


class GraphClient:
    """Thin async wrapper around the Microsoft Graph REST API."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._client: Optional[httpx.AsyncClient] = None

    # ─── Context manager ──────────────────────────────────────────────────────

    async def __aenter__(self) -> "GraphClient":
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
            timeout=60.0,
            http2=True,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    # ─── Internal request helper ──────────────────────────────────────────────

    async def _get(self, url: str, **params) -> Any:
        """
        GET `url` (absolute or relative to BASE_URL) with automatic retry on
        429 and connection errors.

        Raises:
            httpx.HTTPStatusError  — on 401 (token expired, caught by caller)
            RuntimeError           — after MAX_RETRIES exhausted
        """
        assert self._client, "GraphClient must be used as an async context manager."
        delay = 2.0

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await self._client.get(url, params=params or None)
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                log.warning("Network error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
                await asyncio.sleep(delay)
                delay *= 2
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", delay))
                log.warning("Rate-limited — waiting %ds (attempt %d/%d)", retry_after, attempt, MAX_RETRIES)
                await asyncio.sleep(retry_after)
                delay = max(delay * 2, retry_after)
                continue

            if resp.status_code == 401:
                raise httpx.HTTPStatusError(
                    "Bearer token expired (401). Re-auth required.",
                    request=resp.request,
                    response=resp,
                )

            if resp.status_code == 403:
                # Log the full error body — Microsoft includes an error code and
                # message that explains exactly why access was denied.
                try:
                    body = resp.json()
                    err  = body.get("error", {})
                    log.debug(
                        "403 body for %s: code=%r message=%r",
                        url, err.get("code"), err.get("message"),
                    )
                except Exception:
                    pass

            resp.raise_for_status()
            return resp.json()

        raise RuntimeError(f"Graph API request to {url!r} failed after {MAX_RETRIES} retries.")

    # ─── Paging helper ────────────────────────────────────────────────────────

    async def _get_all(self, url: str, **params) -> List[dict]:
        """Follow @odata.nextLink pages and return all items concatenated."""
        results = []
        next_url: Optional[str] = url
        while next_url:
            data = await self._get(next_url, **(params if next_url == url else {}))
            results.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
        return results

    # ─── Public Graph methods ─────────────────────────────────────────────────

    async def list_joined_teams(self) -> List[dict]:
        """Return all Teams the authenticated user is a member of."""
        return await self._get_all("/me/joinedTeams")

    async def list_channels(self, team_id: str) -> List[dict]:
        """Return all channels for the given team."""
        return await self._get_all(f"/teams/{team_id}/channels")

    async def get_primary_channel(self, team_id: str) -> dict:
        """
        Return the primary (General) channel for a team.

        Used as a fallback when /teams/{id}/channels returns 403 —
        which happens in Education tenants where the channels list
        endpoint is restricted for student-role members even when they
        can see the team. The primary channel is always accessible.
        """
        return await self._get(f"/teams/{team_id}/primaryChannel")

    async def get_files_folder(self, team_id: str, channel_id: str) -> dict:
        """
        Return the root DriveItem for a channel's file library.
        The response contains `parentReference.driveId` and `id`.
        """
        return await self._get(f"/teams/{team_id}/channels/{channel_id}/filesFolder")

    async def list_drive_children(self, drive_id: str, item_id: str) -> List[dict]:
        """List direct children of a drive item (folder contents)."""
        return await self._get_all(f"/drives/{drive_id}/items/{item_id}/children")

    async def get_team_drive(self, team_id: str) -> dict:
        """
        Return the default SharePoint drive for a team via the Teams endpoint.
        Returns 404 when the institution uses non-standard SharePoint provisioning.
        In that case, use get_group_drive() as a fallback.
        """
        return await self._get(f"/teams/{team_id}/drive")

    async def get_group_drive(self, group_id: str) -> dict:
        """
        Return the SharePoint drive for a Microsoft 365 Group (same ID as the team).
        Fallback when /teams/{id}/drive returns 404 — the /groups path has different
        routing logic and often succeeds where /teams fails.
        """
        return await self._get(f"/groups/{group_id}/drive")

    async def list_site_drives(self, site_id: str) -> List[dict]:
        """
        Return all document libraries (drives) on a SharePoint site.
        """
        return await self._get_all(f"/sites/{site_id}/drives")

    async def get_site_by_url(self, hostname: str, site_path: str) -> dict:
        """
        Resolve a SharePoint site by its URL components when siteId fields
        are null in drive responses (common for non-standard provisioning).

        Example:
          hostname  = "pucsp.sharepoint.com"
          site_path = "/sites/452516_4385_2"
          → GET /sites/pucsp.sharepoint.com:/sites/452516_4385_2
        """
        return await self._get(f"/sites/{hostname}:{site_path}")

    async def get_drive_root(self, drive_id: str) -> dict:
        """Return the root DriveItem of a drive (starting point for walking)."""
        return await self._get(f"/drives/{drive_id}/root")

    async def get_team_members(self, team_id: str) -> List[dict]:
        """
        Return membership of a team. Members with roles=["owner"] are owners
        (usually the professor in an education tenant).
        """
        return await self._get_all(f"/teams/{team_id}/members")

    async def download_file(self, drive_id: str, item_id: str) -> bytes:
        """
        Stream-download a file and return its raw bytes.
        Follows the redirect that Graph returns for /content.
        """
        assert self._client
        delay = 2.0
        url = f"/drives/{drive_id}/items/{item_id}/content"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await self._client.get(url, follow_redirects=True)
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                log.warning("Download error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
                await asyncio.sleep(delay)
                delay *= 2
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", delay))
                log.warning("Rate-limited on download — waiting %ds", retry_after)
                await asyncio.sleep(retry_after)
                delay = max(delay * 2, retry_after)
                continue

            if resp.status_code == 401:
                raise httpx.HTTPStatusError(
                    "Bearer token expired during download.",
                    request=resp.request,
                    response=resp,
                )

            resp.raise_for_status()
            return resp.content

        raise RuntimeError(f"File download failed after {MAX_RETRIES} retries (item {item_id}).")
