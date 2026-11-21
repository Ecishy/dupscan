# dupscan

[![CI](https://github.com/Ecishy/dupscan/actions/workflows/ci.yml/badge.svg)](https://github.com/Ecishy/dupscan/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Find duplicate files by content — fast, safe, pure Python.**
No dependencies, no database, no daemon: one command, an honest report, and opt-in cleanup.

```
$ dupscan ~/Downloads
2 duplicate group(s) under C:\Users\you\Downloads
3 redundant file(s), 214.5 MiB wasted

214.3 MiB x2  sha256:9f2a1c33e5b70d84
  keep  installer-v2.1.iso
  dupe  Downloads backup/installer-v2.1 (copy).iso

412.0 KiB x2  sha256:1d4e8a02b6c99f37
  keep  report-final.pdf
  dupe  report-final (1).pdf
```

## Why it's fast

A full hash of every file is wasted work. dupscan narrows the candidate set in
three passes, so expensive full-file SHA-256 only runs on files that are
already strong duplicates:

1. **Bucket by size** — files with different sizes can never be identical.
2. **Hash the first 64 KiB** of same-size files; most near-misses die here.
3. **Full SHA-256** — only for whatever is still ambiguous.

On a typical photo/video library, steps 2–3 end up touching just a few percent
of the bytes on disk.

## Features

- **Pure standard library** — Python 3.9+, nothing to install alongside it.
- **Safe by default** — the scan only reports. Cleanup is always opt-in.
- `--delete` with an explicit confirmation prompt (and `--yes` for scripts);
  refuses to run non-interactively without `--yes`.
- `--move-to DIR` instead of deletion: collect redundant copies in a
  quarantine folder, with automatic `(1)`, `(2)`… name collisions.
- Choose the survivor: `--keep newest|oldest|first`.
- `--exclude` fnmatch patterns, `--min-size` with `k`/`m`/`g` suffixes.
- `--json` for scripting, `--fail-on-dupes` for CI checks.
- Library-friendly: `from dupscan import find_duplicates`.
- Symlinks and Windows junctions are ignored so linked files aren't
  double-counted; empty files are ignored by default (their "duplicates" are
  usually noise).

## Install

From a clone of this repository:

```bash
pip install .
```

or, without installing, straight from the source tree:

```bash
python -m dupscan ~/Downloads
```

(When [published on PyPI](https://pypi.org/project/dupscan/), it will also be
available via `pipx install dupscan`.)

## Usage

```
dupscan [PATH] [options]

  -m, --min-size SIZE      ignore files smaller than SIZE (4096, 8k, 1.5m, 2g)
  -e, --exclude PATTERN    skip fnmatch pattern like '*.tmp' or 'build/*' (repeatable)
  -k, --keep MODE          which copy survives: newest | oldest | first (default: newest)
  -d, --delete             delete redundant copies (asks first unless --yes)
  -M, --move-to DIR        move redundant copies into DIR instead
  -y, --yes                skip the confirmation prompt
  -j, --json               machine-readable report (no cleanup actions)
  --follow-symlinks        descend into symlinked directories
  --fail-on-dupes          exit 1 when duplicates are found (CI-friendly)
  -v, --verbose            log every deleted/moved file to stderr
  -V, --version            print version
```

**Examples**

```bash
# just look
dupscan ~/Projects

# ignore caches and builds, only files over 1 MiB
dupscan ~/Projects -m 1m -e '*.pyc' -e 'node_modules/*'

# dry-run the cleanup you're considering: move instead of delete first
dupscan ~/Downloads --move-to D:/quarantine -v

# then delete for real, keeping the newest copy of each group
dupscan ~/Downloads --delete -k newest
```

**Exit codes:** `0` — finished (duplicates or not); `1` — duplicates found and
`--fail-on-dupes` was passed; `2` — usage or I/O error.

## Use as a library

```python
from pathlib import Path
from dupscan import find_duplicates, pick_keep

for group in find_duplicates(Path.home() / "Downloads", min_size=1024 * 1024):
    keep, redundant = pick_keep(group.files, "newest")
    print(f"{group.waste:>12,} bytes wasted by {[str(p) for p in group.files]}")
```

## How it decides what's a duplicate

Two files are duplicates **iff their full SHA-256 digests match**. SHA-256 is
cryptographic — not a heuristic like checksum64 or fuzzy matching — so a
reported duplicate is a real, byte-identical duplicate. False positives would
require a SHA-256 collision.

Still, keep two habits: run without cleanup flags first, and prefer
`--move-to` over `--delete` until you've eyeballed the quarantine folder.

## 中文说明

`dupscan` 是一个零依赖的重复文件查找命令行工具（Python 3.9+ 标准库实现）。

- **原理**：先按文件大小分桶 → 再哈希每个文件头 64 KiB → 最后仅对仍未分出胜负的文件做完整 SHA-256，因此大目录扫描很快。
- **安全**：默认只报告不删除；`--delete` 会先列出待删文件并确认（非交互环境必须加 `--yes`）；更稳妥的做法是用 `--move-to 目录` 把冗余副本先移出去检查。
- **示例**：

```bash
python -m dupscan ~/Downloads          # 扫描报告
python -m dupscan . -m 1m -e '*.pyc'   # 忽略小于 1 MiB 与 .pyc
python -m dupscan . --delete --yes -k newest   # 删除冗余副本（保留最新）
```

- **退出码**：`0` 正常结束；`1` 发现重复且传了 `--fail-on-dupes`（可用于 CI）；`2` 参数或 IO 错误。

## Development

```bash
pip install .
python -m unittest discover -s tests -v   # 27 tests, stdlib only
```

CI runs the same suite on Linux / macOS / Windows across Python 3.9–3.13.

## Roadmap

- [ ] Progress bar for huge trees (`-v` already logs cleanups)
- [ ] Hardlink detection and `--link` mode
- [ ] `--stdin`/`--from-file` lists for scripting pipelines

## License

[MIT](LICENSE) © Ecishy
