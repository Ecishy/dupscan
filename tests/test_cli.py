"""Tests for dupscan's command-line interface."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from dupscan.cli import main, parse_size


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        fixtures = (
            (self.root / "a.bin", b"same content", 1000),
            (self.root / "sub" / "b.bin", b"same content", 2000),
            (self.root / "c.bin", b"unique", 1000),
        )
        for path, content, mtime in fixtures:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            os.utime(path, (mtime, mtime))

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_report_lists_duplicates_with_exit_zero(self) -> None:
        code, out, _ = self.run_cli(str(self.root))

        self.assertEqual(code, 0)
        self.assertIn("1 duplicate group(s)", out)
        self.assertIn("keep  sub\\b.bin" if os.name == "nt" else "keep  sub/b.bin", out)

    def test_report_shows_relative_paths(self) -> None:
        code, out, _ = self.run_cli(str(self.root))

        rel = os.path.join("sub", "b.bin")
        self.assertIn(f"keep  {rel}", out)
        self.assertIn(f"dupe  a.bin", out)

    def test_json_output(self) -> None:
        code, out, _ = self.run_cli("--json", str(self.root))

        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["duplicates"])
        self.assertEqual(len(data["groups"]), 1)
        self.assertEqual(data["groups"][0]["size"], len(b"same content"))
        self.assertEqual(data["total_waste"], len(b"same content"))

    def test_delete_removes_redundant_copies(self) -> None:
        code, out, _ = self.run_cli("--delete", "--yes", str(self.root))

        self.assertEqual(code, 0)
        remaining = {p.name for p in self.root.rglob("*.bin")}
        self.assertEqual(remaining, {"b.bin", "c.bin"})  # newest copy of the pair survives
        self.assertIn("cleaned up", out)

    def test_delete_aborts_without_confirmation_in_non_tty(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        original_stdin = sys.stdin
        sys.stdin = io.StringIO("")  # looks like a closed, non-tty stdin
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--delete", str(self.root)])
        finally:
            sys.stdin = original_stdin

        self.assertEqual(code, 0)
        self.assertIn("refusing to --delete", err.getvalue())
        self.assertEqual(len(list(self.root.rglob("*.bin"))), 3)

    def test_move_to_collects_redundant_copies(self) -> None:
        quarantine = self.root / "quarantine"
        code, _out, _ = self.run_cli("--move-to", str(quarantine), str(self.root))

        self.assertEqual(code, 0)
        self.assertEqual([p.name for p in quarantine.iterdir()], ["a.bin"])
        stray = [p for p in self.root.rglob("*.bin") if quarantine not in p.parents]
        self.assertEqual(len(stray), 2)

    def test_keep_first_prefers_alphabetical_copy(self) -> None:
        code, _out, _ = self.run_cli("--delete", "--yes", "--keep", "first", str(self.root))

        self.assertEqual(code, 0)
        remaining = {p.name for p in self.root.rglob("*.bin")}
        self.assertEqual(remaining, {"a.bin", "c.bin"})  # "a.bin" sorts before "sub/b.bin"

    def test_fail_on_dupes_exit_code(self) -> None:
        code, _out, _ = self.run_cli("--fail-on-dupes", str(self.root))
        self.assertEqual(code, 1)

    def test_missing_directory_exits_two(self) -> None:
        code, _out, err = self.run_cli(str(self.root / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)

    def test_min_size_filters(self) -> None:
        code, out, _ = self.run_cli("--min-size", "8k", str(self.root))
        self.assertEqual(code, 0)
        self.assertIn("No duplicate files found", out)

    def test_parse_size(self) -> None:
        self.assertEqual(parse_size("4096"), 4096)
        self.assertEqual(parse_size("8k"), 8192)
        self.assertEqual(parse_size("1.5m"), int(1.5 * 2**20))
        self.assertEqual(parse_size("2G"), 2 * 2**30)

    def test_parse_size_invalid(self) -> None:
        import argparse

        with self.assertRaises(argparse.ArgumentTypeError):
            parse_size("bogus")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_size("-5")


if __name__ == "__main__":
    unittest.main()
