"""Command-line interface for dupscan."""

from __future__ import annotations

import argparse
import itertools
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .scanner import KEEP_MODES, ScanError, find_duplicates, pick_keep

_SIZE_SUFFIXES = {"k": 2**10, "m": 2**20, "g": 2**30}


def parse_size(text: str) -> int:
    """Parse ``4096``, ``8k``, ``1.5m``, ``2g`` into a byte count."""
    raw = text.strip().lower()
    multiplier = _SIZE_SUFFIXES.get(raw[-1]) if raw else None
    if multiplier:
        raw = raw[:-1]
    try:
        value = float(raw) * (multiplier or 1)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid size: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("size must not be negative")
    return int(value)


class Style:
    """ANSI styling that degrades to plain text when disabled."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dupscan",
        description=(
            "Find duplicate files by content: bucket by size, hash the first "
            "64 KiB, then confirm with a full SHA-256 — pure standard library."
        ),
    )
    parser.add_argument(
        "path", nargs="?", default=".",
        help="directory to scan (default: the current directory)",
    )
    parser.add_argument(
        "-m", "--min-size", type=parse_size, default=1, metavar="SIZE",
        help="ignore files smaller than SIZE, e.g. 4096, 8k, 1.5m (default: 1)",
    )
    parser.add_argument(
        "-e", "--exclude", action="append", metavar="PATTERN",
        help="skip files matching an fnmatch pattern such as '*.tmp' or 'build/*' (repeatable)",
    )
    parser.add_argument(
        "-k", "--keep", choices=KEEP_MODES, default="newest", metavar="MODE",
        help="which copy to keep when cleaning up: newest, oldest or first (default: newest)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "-d", "--delete", action="store_true",
        help="delete redundant copies (asks for confirmation unless --yes)",
    )
    mode.add_argument(
        "-M", "--move-to", metavar="DIR",
        help="move redundant copies into DIR instead of deleting them",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="do not prompt before --delete",
    )
    parser.add_argument(
        "--follow-symlinks", action="store_true",
        help="also descend into symlinked directories (file symlinks are always ignored)",
    )
    parser.add_argument(
        "-j", "--json", action="store_true",
        help="print machine-readable JSON instead of the report (no cleanup actions)",
    )
    parser.add_argument(
        "--fail-on-dupes", action="store_true",
        help="exit with status 1 when duplicates are found, e.g. for CI checks",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="log every deleted or moved file to stderr",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"dupscan {__version__}",
    )
    parser.add_argument("--no-color", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.json and (args.delete or args.move_to):
        print("warning: --json only reports; --delete/--move-to were ignored", file=sys.stderr)

    style = Style(_color_enabled() and not args.json and not args.no_color)
    root = Path(os.path.abspath(args.path))

    try:
        groups = find_duplicates(
            args.path,
            min_size=args.min_size,
            exclude=args.exclude or (),
            follow_symlinks=args.follow_symlinks,
        )
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(_to_json(groups, root), indent=2))
        return _exit_code(groups, args)

    plan = [pick_keep(group.files, args.keep) for group in groups]
    print_report(groups, plan, root, style)
    changed = apply_plan(plan, args)
    if changed:
        print(f"Done: {changed} file(s) cleaned up.")
    return _exit_code(groups, args)


def apply_plan(plan, args) -> int:
    """Carry out ``--delete`` or ``--move-to``; return the number changed."""
    redundant = [dupe for _keep, dupes in plan for dupe in dupes]
    if not redundant:
        return 0

    if args.delete:
        changed = 0
        if not _confirm_delete(redundant, args):
            print("Aborted; nothing was changed.")
            return 0
        for dupe in redundant:
            try:
                dupe.unlink()
            except OSError as exc:
                print(f"error: could not delete {dupe}: {exc}", file=sys.stderr)
            else:
                changed += 1
                if args.verbose:
                    print(f"deleted {dupe}", file=sys.stderr)
        return changed

    if args.move_to:
        target = Path(os.path.abspath(args.move_to))
        target.mkdir(parents=True, exist_ok=True)
        changed = 0
        for dupe in redundant:
            dest = _unique_destination(target, dupe.name)
            try:
                shutil.move(str(dupe), str(dest))
            except OSError as exc:
                print(f"error: could not move {dupe}: {exc}", file=sys.stderr)
            else:
                changed += 1
                if args.verbose:
                    print(f"moved {dupe} -> {dest}", file=sys.stderr)
        return changed

    return 0


def _confirm_delete(redundant, args) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("error: refusing to --delete without --yes because stdin is not a terminal", file=sys.stderr)
        return False
    print("About to delete these file(s):")
    for dupe in redundant:
        print(f"  - {dupe}")
    try:
        answer = input("Proceed? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def _unique_destination(directory: Path, name: str) -> Path:
    dest = directory / name
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for index in itertools.count(1):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate


def print_report(groups, plan, root: Path, style: Style) -> None:
    if not groups:
        print(f"No duplicate files found under {root}.")
        return

    total_waste = sum(group.waste for group in groups)
    total_dupes = sum(len(dupes) for _keep, dupes in plan)
    print(f"{len(groups)} duplicate group(s) under {root}")
    print(f"{total_dupes} redundant file(s), {format_bytes(total_waste)} wasted\n")

    for group, (keep, dupes) in zip(groups, plan):
        header = (
            f"{format_bytes(group.size)} x{len(group.files)}"
            f"  sha256:{group.digest[:16]}"
        )
        print(style(header, "1;36"))
        print(f"  {style('keep', '32')}  {os.path.relpath(keep, root)}")
        for dupe in dupes:
            print(f"  {style('dupe', '31')}  {os.path.relpath(dupe, root)}")
        print()


def format_bytes(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TiB"


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.name == "nt":
        os.system("")  # enable ANSI escape sequences on Windows consoles
    return sys.stdout.isatty()


def _to_json(groups, root: Path) -> dict:
    return {
        "root": str(root),
        "duplicates": bool(groups),
        "groups": [
            {
                "size": group.size,
                "sha256": group.digest,
                "waste": group.waste,
                "files": [os.path.relpath(path, root) for path in group.files],
            }
            for group in groups
        ],
        "total_waste": sum(group.waste for group in groups),
    }


def _exit_code(groups, args) -> int:
    return 1 if (args.fail_on_dupes and groups) else 0


def run() -> int:
    try:
        return main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(run())
