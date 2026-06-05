from __future__ import annotations

from .models import Bookmark, SyncRecord


def parse_folder_specs(raw: str) -> list[list[str]]:
    """"bookmark_bar/Tech,other" → [["bookmark_bar", "Tech"], ["other"]]"""
    if not raw or not raw.strip():
        return []
    result = []
    for spec in raw.split(","):
        parts = [p.strip() for p in spec.strip().split("/") if p.strip()]
        if parts:
            result.append(parts)
    return result


def _path_matches(folder_path: list[str], prefix: list[str]) -> bool:
    return len(folder_path) >= len(prefix) and folder_path[: len(prefix)] == prefix


def _matches_any(folder_path: list[str], prefixes: list[list[str]]) -> bool:
    return any(_path_matches(folder_path, p) for p in prefixes)


def filter_flat_vivaldi(
    flat: dict[str, Bookmark], prefixes: list[list[str]]
) -> dict[str, Bookmark]:
    if not prefixes:
        return flat
    return {url: bm for url, bm in flat.items() if _matches_any(bm.folder_path, prefixes)}


def filter_flat_safari(
    flat: dict[str, Bookmark], prefixes: list[list[str]]
) -> dict[str, Bookmark]:
    """Safari 形式パス (BookmarksBar/...) を直接 prefix と比較してフィルタする。"""
    if not prefixes:
        return flat
    return {url: bm for url, bm in flat.items() if _matches_any(bm.folder_path, prefixes)}


def filter_snapshot(
    base: dict[str, SyncRecord],
    vivaldi_prefixes: list[list[str]],
    safari_prefixes: list[list[str]],
) -> dict[str, SyncRecord]:
    """
    Vivaldi/Safari それぞれの prefixes でフィルタする。
    指定されている側のパスが一致するレコードのみ残す（AND 条件）。
    両方未指定の場合はフィルタなし。
    """
    if not vivaldi_prefixes and not safari_prefixes:
        return base
    result = {}
    for url, rec in base.items():
        if vivaldi_prefixes and not _matches_any(rec.folder_path_vivaldi, vivaldi_prefixes):
            continue
        if safari_prefixes and not _matches_any(rec.folder_path_safari, safari_prefixes):
            continue
        result[url] = rec
    return result
