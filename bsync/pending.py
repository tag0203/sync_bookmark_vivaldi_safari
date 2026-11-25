from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import Bookmark, MergeResult
from .snapshot import write_json_atomic

PENDING_PATH = Path("~/.bsync/pending.json").expanduser()

_KEYS = {
    "add": "to_add_vivaldi",
    "delete": "to_delete_vivaldi",
    "update": "to_update_vivaldi",
}


@dataclass
class PendingChange:
    operation: str
    bookmark: dict | None = None


def load_pending(path: Path) -> dict[str, PendingChange]:
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return {}

    if not isinstance(data, dict):
        raise ValueError("pending.json must contain an object")

    changes = {}
    for operation, key in _KEYS.items():
        entries = data.get(key, [])
        if not isinstance(entries, list):
            raise ValueError(f"{key} must contain a list")
        for entry in entries:
            if operation == "delete":
                url, bookmark = entry, None
            else:
                if not isinstance(entry, dict):
                    raise ValueError(f"{key} must contain bookmark objects")
                url, bookmark = entry.get("url"), entry
            if not isinstance(url, str) or url in changes:
                raise ValueError(f"{key} contains an invalid or duplicate URL")
            changes[url] = PendingChange(operation, bookmark)
    return changes


def pending_from_result(
    result: MergeResult, vivaldi: dict[str, Bookmark]
) -> dict[str, PendingChange]:
    changes = {url: PendingChange("delete") for url in result.to_delete_vivaldi}
    for operation, bookmarks in (
        ("add", result.to_add_vivaldi),
        ("update", result.to_update_vivaldi),
    ):
        for bookmark in bookmarks:
            entry = {
                "title": bookmark.title,
                "url": bookmark.url,
                "guid": bookmark.guid,
                "date_added": bookmark.date_added.isoformat(),
                "date_modified": bookmark.date_modified.isoformat(),
                "folder_path": bookmark.folder_path,
            }
            if operation == "update":
                entry["previous_vivaldi_title"] = vivaldi[bookmark.url].title
            changes[bookmark.url] = PendingChange(operation, entry)
    return changes


def save_pending(path: Path, changes: dict[str, PendingChange]) -> None:
    if not changes:
        path.unlink(missing_ok=True)
        return

    data: dict[str, list[str | dict]] = {key: [] for key in _KEYS.values()}
    for url, change in changes.items():
        entry = change.bookmark if change.bookmark is not None else url
        data[_KEYS[change.operation]].append(entry)
    write_json_atomic(path, data)
