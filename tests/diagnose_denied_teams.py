"""
tests/diagnose_denied_teams.py

Targeted diagnostic for teams with 0 files in the DB.
Tries every Graph API path we know and reports what works/fails.

Run:
    python tests/diagnose_denied_teams.py
"""
import sys, os
# Make `src.*` importable when running the script directly (not via pytest)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv()

from src.auth import get_bearer_token
from src.graph_client import GraphClient

# Teams with 0 files in DB (from: SELECT name, teams_id FROM curso WHERE file_count = 0)
TEAMS = {
    "250c4dce-6dea-45ee-94fe-1d5f72a79c22": "ESTRUTURA DE DADOS - LINEARES (PRÁTICA) 2026/1",
    "5464d7ff-dd0c-4e63-9ab8-a7d9c9caed86": "DESENVOLVIMENTO DE ALGORITMOS PARA TIPOS LINEARES",
    "366ea3ec-4106-40b7-851a-a77b6e69376b": "CÁLCULO DIFERENCIAL E INTEGRAL II 2025/2",
    "8da0c693-0d8b-410f-b043-789963525f8a": "TÓPICOS DE MATEMÁTICA DISCRETA 2025/2",
}


def ok(label: str, value=None):
    suffix = f" → {value}" if value else ""
    print(f"  ✅  {label}{suffix}")


def fail(label: str, exc):
    print(f"  ❌  {label}: {type(exc).__name__} — {exc}")


async def diagnose_team(graph: GraphClient, team_id: str, name: str):
    print()
    print("=" * 70)
    print(f"  {name}")
    print(f"  {team_id}")
    print("=" * 70)

    # ── 1. Channels ─────────────────────────────────────────────────────────
    channels = None
    try:
        channels = await graph.list_channels(team_id)
        ok(f"list_channels → {len(channels)} channel(s)")
        for ch in channels:
            print(f"       • {ch.get('displayName')} ({ch['id'][:20]}…)")
    except Exception as e:
        fail("list_channels", e)

    # ── 2. Primary channel ───────────────────────────────────────────────────
    primary = None
    try:
        primary = await graph.get_primary_channel(team_id)
        ok(f"primaryChannel → {primary.get('displayName')}")
    except Exception as e:
        fail("primaryChannel", e)

    # ── 3. filesFolder for each channel ──────────────────────────────────────
    all_channels = (channels or []) + ([primary] if primary else [])
    site_id_from_folder = None
    for ch in all_channels:
        ch_id   = ch["id"]
        ch_name = ch.get("displayName", "?")
        try:
            ff = await graph.get_files_folder(team_id, ch_id)
            drive_id     = ff["parentReference"]["driveId"]
            site_id_from_folder = ff.get("parentReference", {}).get("siteId")
            root_item_id = ff["id"]
            web_url      = ff.get("webUrl", "")
            ok(f"filesFolder[{ch_name}] driveId={drive_id[:20]}…  webUrl={web_url}")

            # Walk direct children of the channel root
            try:
                children = await graph.list_drive_children(drive_id, root_item_id)
                if children:
                    ok(f"  drive children → {len(children)} item(s)")
                    for c in children[:5]:
                        print(f"       • {c['name']} ({'folder' if 'folder' in c else 'file'})")
                else:
                    print(f"       (folder is empty)")
            except Exception as e:
                fail(f"  list_drive_children [{ch_name}]", e)
        except Exception as e:
            fail(f"filesFolder[{ch_name}]", e)

    # ── 4. Groups drive ──────────────────────────────────────────────────────
    group_drive_web_url = None
    try:
        gd = await graph.get_group_drive(team_id)
        group_drive_web_url = gd.get("webUrl", "")
        ok(f"groups/drive  webUrl={group_drive_web_url}")
    except Exception as e:
        fail("groups/drive", e)

    # ── 5. Site drives via webUrl ─────────────────────────────────────────────
    web_url = group_drive_web_url or ""
    if "/sites/" in web_url:
        parsed = urlparse(web_url)
        path_parts = parsed.path.split("/")
        site_path  = "/" + "/".join(path_parts[1:3])
        try:
            site = await graph.get_site_by_url(parsed.hostname, site_path)
            resolved_site_id = site.get("id", "")
            ok(f"site resolved  id={resolved_site_id[:30]}…")

            drives = await graph.list_site_drives(resolved_site_id)
            ok(f"site drives → {len(drives)} librar(ies)")
            for d in drives:
                print(f"       • '{d.get('name')}' (driveId={d['id'][:20]}…)")

                # Walk root of each drive
                try:
                    root = await graph.get_drive_root(d["id"])
                    children = await graph.list_drive_children(d["id"], root["id"])
                    if children:
                        print(f"         → {len(children)} item(s) in root")
                        for c in children[:3]:
                            print(f"           • {c['name']} ({'folder' if 'folder' in c else 'file'})")
                    else:
                        print(f"         → (empty)")
                except Exception as e:
                    print(f"         → walk failed: {e}")
        except Exception as e:
            fail(f"site resolve/drives ({site_path})", e)
    else:
        print("  ⚠️  No /sites/ in webUrl — cannot enumerate site drives")

    print()


async def main(token: str):
    async with GraphClient(token) as graph:
        for team_id, name in TEAMS.items():
            await diagnose_team(graph, team_id, name)


if __name__ == "__main__":
    _token = get_bearer_token()   # sync Playwright — must be BEFORE asyncio.run()
    asyncio.run(main(_token))
