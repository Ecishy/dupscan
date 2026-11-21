"""dupscan — find duplicate files by content, using only the standard library."""

from .scanner import DuplicateGroup, ScanError, find_duplicates, pick_keep

__version__ = "0.1.0"

__all__ = ["DuplicateGroup", "ScanError", "find_duplicates", "pick_keep"]
