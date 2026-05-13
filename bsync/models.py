from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Union


EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class Bookmark:
    title: str
    url: str
    guid: str
    date_added: datetime
    date_modified: datetime
    folder_path: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


@dataclass
class BookmarkFolder:
    title: str
    guid: str
    children: list[Union[Bookmark, "BookmarkFolder"]]
    date_added: datetime
    date_modified: datetime
    folder_path: list[str] = field(default_factory=list)


@dataclass
class BookmarkTree:
    bar: BookmarkFolder
    other: BookmarkFolder
    source: str  # "vivaldi" | "safari"


@dataclass
class SyncRecord:
    url: str
    title: str
    guid_vivaldi: str | None
    uuid_safari: str | None
    date_added_unix: float
    date_modified_unix: float
    folder_path_vivaldi: list[str] = field(default_factory=list)
    folder_path_safari: list[str] = field(default_factory=list)


@dataclass
class ConflictRecord:
    url: str
    vivaldi_title: str
    safari_title: str
    resolved_title: str
    resolution: str  # "vivaldi_newer" | "safari_newer" | "vivaldi" | "safari" | "equal"


@dataclass
class MergeResult:
    to_add_vivaldi: list[Bookmark] = field(default_factory=list)
    to_add_safari: list[Bookmark] = field(default_factory=list)
    to_delete_vivaldi: list[str] = field(default_factory=list)
    to_delete_safari: list[str] = field(default_factory=list)
    to_update_vivaldi: list[Bookmark] = field(default_factory=list)
    to_update_safari: list[Bookmark] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
