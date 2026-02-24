"""
src/utils.py — shared helpers: logging, path building, filename sanitisation.
"""
import re
import os
import logging
from pathlib import Path

from rich.logging import RichHandler
from rich.console import Console

console = Console()


# ─── Logging ──────────────────────────────────────────────────────────────────

def setup_logging(level: int = logging.INFO) -> None:
    """Configure Rich-based logging for the whole application."""
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


log = logging.getLogger("backup_teams")


# ─── Filename / path helpers ───────────────────────────────────────────────────

_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')
_WHITESPACE    = re.compile(r'\s+')


def sanitize(name: str) -> str:
    """
    Strip characters that are illegal in file/directory names and collapse
    runs of whitespace into a single space.
    """
    name = _ILLEGAL_CHARS.sub("_", name)
    name = _WHITESPACE.sub(" ", name).strip()
    return name or "unnamed"


def build_local_path(
    download_root: str,
    curso_name: str,
    *sub_parts: str,
) -> Path:
    """
    Construct (and create) the full container-internal path for a file.

    Example:
        build_local_path("/data/downloads", "Calculus", "Week 1", "lecture.pdf")
        → Path("/data/downloads/Calculus/Week 1/lecture.pdf")
          (parent directories are created automatically)
    """
    parts = [sanitize(p) for p in (curso_name, *sub_parts)]
    path  = Path(download_root).joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def versioned_backup_path(original: Path) -> Path:
    """
    Return a sibling path with a timestamp suffix, used when a file has
    changed and we want to keep the old version.

    Example:
        /data/downloads/Calculus/lecture.pdf
        → /data/downloads/Calculus/lecture_backup_20250224T163700.pdf
    """
    from datetime import datetime
    ts   = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    stem = original.stem
    return original.with_name(f"{stem}_backup_{ts}{original.suffix}")


def get_download_root() -> str:
    """
    Read DOWNLOAD_ROOT from the environment (set via Docker volume / .env).
    Falls back to a local ./downloads directory for development without Docker.
    """
    root = os.getenv("DOWNLOAD_ROOT", "./downloads")
    Path(root).mkdir(parents=True, exist_ok=True)
    return root
