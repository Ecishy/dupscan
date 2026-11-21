"""Tests for dupscan's scanner core."""

import os
import tempfile
import unittest
from pathlib import Path

from dupscan.scanner import ScanError, find_duplicates, pick_keep


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class ScannerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)


class FindDuplicatesTests(ScannerTestCase):
    def test_finds_identical_files_across_subdirectories(self) -> None:
        write(self.root / "a.bin", b"same content")
        write(self.root / "sub" / "b.bin", b"same content")
        write(self.root / "c.bin", b"unique")

        groups = find_duplicates(self.root)

        self.assertEqual(len(groups), 1)
        self.assertEqual({p.name for p in groups[0].files}, {"a.bin", "b.bin"})
        self.assertEqual(groups[0].size, len(b"same content"))
        self.assertEqual(groups[0].waste, len(b"same content"))

    def test_different_content_same_size_is_not_grouped(self) -> None:
        write(self.root / "a.bin", b"A" * 64)
        write(self.root / "b.bin", b"B" * 64)

        self.assertEqual(find_duplicates(self.root), [])

    def test_different_sizes_are_not_grouped(self) -> None:
        write(self.root / "a.bin", b"same content")
        write(self.root / "b.bin", b"same content extra")

        self.assertEqual(find_duplicates(self.root), [])

    def test_min_size_filters_small_files(self) -> None:
        write(self.root / "a.bin", b"same content")
        write(self.root / "b.bin", b"same content")

        self.assertEqual(find_duplicates(self.root, min_size=100), [])

    def test_zero_byte_files_ignored_by_default(self) -> None:
        write(self.root / "a.bin", b"")
        write(self.root / "b.bin", b"")

        self.assertEqual(find_duplicates(self.root), [])

    def test_exclude_matches_names_and_relative_paths(self) -> None:
        write(self.root / "skipme.bin", b"same content")
        write(self.root / "keep" / "same.bin", b"same content")
        write(self.root / "out" / "same.bin", b"same content")

        groups = find_duplicates(self.root, exclude=("out/*",))

        self.assertEqual(len(groups), 1)
        self.assertEqual({p.name for p in groups[0].files}, {"skipme.bin", "same.bin"})

    def test_exclude_by_directory_name(self) -> None:
        write(self.root / "a.bin", b"same content")
        write(self.root / "out" / "b.bin", b"same content")

        self.assertEqual(find_duplicates(self.root, exclude=("out",)), [])

    def test_groups_sorted_by_waste_descending(self) -> None:
        write(self.root / "big-a", b"x" * 40)
        write(self.root / "big-b", b"x" * 40)
        write(self.root / "small-a", b"y" * 10)
        write(self.root / "small-b", b"y" * 10)

        groups = find_duplicates(self.root)

        self.assertEqual([g.waste for g in groups], [40, 10])

    def test_missing_root_raises(self) -> None:
        with self.assertRaises(ScanError):
            find_duplicates(self.root / "does-not-exist")

    def test_file_root_raises(self) -> None:
        target = self.root / "file.bin"
        write(target, b"data")

        with self.assertRaises(ScanError):
            find_duplicates(target)

    def test_symlinks_are_skipped(self) -> None:
        write(self.root / "real.bin", b"same content")
        try:
            (self.root / "link.bin").symlink_to(self.root / "real.bin")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")

        self.assertEqual(find_duplicates(self.root), [])


class PickKeepTests(ScannerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.older = self.root / "older.txt"
        self.newer = self.root / "newer.txt"
        write(self.older, b"x")
        write(self.newer, b"x")
        os.utime(self.older, (1000, 1000))
        os.utime(self.newer, (2000, 2000))

    def test_newest_is_kept(self) -> None:
        keep, dupes = pick_keep([self.older, self.newer], "newest")
        self.assertEqual(keep, self.newer)
        self.assertEqual(dupes, [self.older])

    def test_oldest_is_kept(self) -> None:
        keep, dupes = pick_keep([self.older, self.newer], "oldest")
        self.assertEqual(keep, self.older)
        self.assertEqual(dupes, [self.newer])

    def test_first_is_alphabetical(self) -> None:
        keep, dupes = pick_keep([self.newer, self.older], "first")
        self.assertEqual(keep, self.newer)  # "newer.txt" sorts before "older.txt"
        self.assertEqual(dupes, [self.older])

    def test_invalid_mode_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pick_keep([self.older], "bogus")


if __name__ == "__main__":
    unittest.main()
