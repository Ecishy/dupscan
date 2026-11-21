"""Core duplicate-file detection logic for dupscan.

Scanning runs in three narrowing passes so expensive full-file hashing
only happens for files that are already strong duplicate candidates:

1. walk the tree and bucket regular files by size,
2. hash the first ``HEAD_SIZE`` bytes of same-size files,
3. fully hash whatever is still ambiguous.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

#: Read chunk (bytes) used while hashing files.
CHUNK_SIZE = 64 * 1024

#: Bytes hashed in the cheap "head" pass before committing to full hashes.
HEAD_SIZE = 64 * 1024

#: Accepted values for the "which copy should survive" policy.
KEEP_MODES = ("newest", "oldest", "first")


class ScanError(OSError):
    """Raised when the scan target is missing or not a directory."""


@dataclass(frozen=True)
class DuplicateGroup:
    """Two or more files with byte-identical content."""

    size: int
    digest: str
    files: tuple[Path, ...]

    @property
    def waste(self) -> int:
        """Bytes occupied by redundant copies of the content."""
        return self.size * (len(self.files) - 1)


def find_duplicates(
    root: str | os.PathLike[str],
    *,
    min_size: int = 1,
    exclude: Iterable[str] = (),
    follow_symlinks: bool = False,
) -> list[DuplicateGroup]:
    """Return duplicate groups under *root*, biggest waste first.

    ``min_size`` (bytes) filters out smaller files; the default of 1
    excludes empty files, whose "duplicates" are usually noise.

    ``exclude`` holds fnmatch patterns matched against file names and
    slash-separated paths relative to *root* (case-insensitive on
    Windows, case-sensitive elsewhere).

    File symlinks are always ignored so linked copies are not counted
    twice; with ``follow_symlinks`` the walk also descends into
    symlinked directories (at the usual risk of loops).
    """
    root_path = Path(os.path.abspath(root))
    if not root_path.is_dir():
        raise ScanError(f"not a directory: {root_path}")

    patterns = tuple(exclude)
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path, size in _walk(root_path, patterns, follow_symlinks):
        if size >= min_size:
            by_size[size].append(path)

    by_head: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for path in paths:
            head = _hash_head(path)
            if head is not None:
                by_head[(size, head)].append(path)

    by_full: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for (size, _head), paths in by_head.items():
        if len(paths) < 2:
            continue
        for path in paths:
            digest = _hash_full(path)
            if digest is not None:
                by_full[(size, digest)].append(path)

    groups = [
        DuplicateGroup(size, digest, tuple(sorted(paths)))
        for (size, digest), paths in by_full.items()
        if len(paths) >= 2
    ]
    groups.sort(key=lambda g: (g.waste, g.size), reverse=True)
    return groups


def pick_keep(files: Iterable[Path], keep: str = "newest") -> tuple[Path, list[Path]]:
    """Split *files* into ``(the copy to keep, the redundant copies)``.

    ``keep`` may be ``"newest"`` / ``"oldest"`` (by modification time,
    ties broken by path) or ``"first"`` (alphabetically first path).
    """
    if keep not in KEEP_MODES:
        raise ValueError(f"keep must be one of {KEEP_MODES}, got {keep!r}")

    scored = []
    for path in files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = float("-inf")
        scored.append((mtime, str(path), path))

    if keep == "first":
        winner = min(scored, key=lambda item: item[1])
    elif keep == "newest":
        winner = max(scored, key=lambda item: item[:2])
    else:  # oldest
        winner = min(scored, key=lambda item: item[:2])

    rest = [path for _mtime, _text, path in scored if path != winner[2]]
    return winner[2], rest


def _walk(root: Path, patterns: tuple[str, ...], follow_symlinks: bool):
    """Yield ``(path, size)`` for every regular file below *root*."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        current = Path(dirpath)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not _is_excluded(current / name, root, patterns)
            and (follow_symlinks or not _is_link(current / name))
        )
        for name in sorted(filenames):
            path = current / name
            if _is_excluded(path, root, patterns) or _is_link(path):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue  # vanished or unreadable; skip quietly
            yield path, size


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name == "nt" and hasattr(path, "is_junction"):
        return path.is_junction()
    return False


def _is_excluded(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    rel = path.relative_to(root).as_posix()
    return any(fnmatch(path.name, pat) or fnmatch(rel, pat) for pat in patterns)


def _hash_head(path: Path) -> str | None:
    try:
        with path.open("rb") as fh:
            data = fh.read(HEAD_SIZE)
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _hash_full(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()
