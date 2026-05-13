from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import Bookmark, MergeResult, SyncRecord

SNAPSHOT_PATH = Path("~/.bsync/last_sync.json").expanduser()


class SnapshotManager:
    def __init__(self, path: Path = SNAPSHOT_PATH) -> None:
        self.path = path

    def load(self) -> dict[str, SyncRecord]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return self._from_json(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            # 破損したスナップショットをバックアップして空で返す
            broken = self.path.with_suffix(".broken.json")
            try:
                self.path.rename(broken)
            except OSError:
                pass
            return {}

    def save(
        self,
        vivaldi_flat: dict[str, Bookmark],
        safari_flat: dict[str, Bookmark],
        merge_result: MergeResult,
    ) -> None:
        all_urls = set(vivaldi_flat) | set(safari_flat)
        bookmarks: dict[str, dict] = {}

        for url in all_urls:
            viv_bm = vivaldi_flat.get(url)
            saf_bm = safari_flat.get(url)
            record = self._build_record(url, viv_bm, saf_bm)
            bookmarks[url] = _record_to_dict(record)

        snapshot = {
            "version": 1,
            "synced_at": datetime.now(tz=timezone.utc).timestamp(),
            "bookmarks": bookmarks,
        }
        self._atomic_write(snapshot)

    def _build_record(
        self,
        url: str,
        viv_bm: Bookmark | None,
        saf_bm: Bookmark | None,
    ) -> SyncRecord:
        bm = viv_bm or saf_bm
        assert bm is not None
        return SyncRecord(
            url=url,
            title=bm.title,
            guid_vivaldi=viv_bm.guid if viv_bm else None,
            uuid_safari=saf_bm.guid if saf_bm else None,
            date_added_unix=bm.date_added.timestamp(),
            date_modified_unix=bm.date_modified.timestamp(),
            folder_path_vivaldi=viv_bm.folder_path if viv_bm else [],
            folder_path_safari=saf_bm.folder_path if saf_bm else [],
        )

    def _from_json(self, data: dict) -> dict[str, SyncRecord]:
        result: dict[str, SyncRecord] = {}
        for url, rec in data.get("bookmarks", {}).items():
            result[url] = SyncRecord(
                url=rec["url"],
                title=rec["title"],
                guid_vivaldi=rec.get("guid_vivaldi"),
                uuid_safari=rec.get("uuid_safari"),
                date_added_unix=float(rec.get("date_added_unix", 0)),
                date_modified_unix=float(rec.get("date_modified_unix", 0)),
                folder_path_vivaldi=rec.get("folder_path_vivaldi", []),
                folder_path_safari=rec.get("folder_path_safari", []),
            )
        return result

    def _atomic_write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def _record_to_dict(rec: SyncRecord) -> dict:
    return {
        "url": rec.url,
        "title": rec.title,
        "guid_vivaldi": rec.guid_vivaldi,
        "uuid_safari": rec.uuid_safari,
        "date_added_unix": rec.date_added_unix,
        "date_modified_unix": rec.date_modified_unix,
        "folder_path_vivaldi": rec.folder_path_vivaldi,
        "folder_path_safari": rec.folder_path_safari,
    }
