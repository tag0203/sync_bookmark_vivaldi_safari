from __future__ import annotations

import os
import plistlib
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Bookmark, BookmarkFolder, BookmarkTree, EPOCH_UTC

DEFAULT_PATH = Path("~/Library/Safari/Bookmarks.plist").expanduser()

# 2001-01-01 から 1970-01-01 までの秒数
_APPLE_EPOCH_OFFSET = 978_307_200.0


class SafariPermissionError(PermissionError):
    GUIDANCE = (
        "bsync には Safari のブックマークへのフルディスクアクセス権限が必要です。\n"
        "システム設定 > プライバシーとセキュリティ > フルディスクアクセス で\n"
        "Terminal (またはお使いのターミナルアプリ) を追加してください。"
    )


def _apple_ts_to_datetime(ts) -> datetime:
    if ts is None:
        return EPOCH_UTC
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    try:
        return datetime.fromtimestamp(float(ts) + _APPLE_EPOCH_OFFSET, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return EPOCH_UTC


def _datetime_to_apple_ts(dt: datetime) -> float:
    return dt.timestamp() - _APPLE_EPOCH_OFFSET


class SafariReader:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path

    def read(self) -> tuple[BookmarkTree, dict]:
        try:
            with open(self.path, "rb") as f:
                data = plistlib.load(f, fmt=plistlib.FMT_BINARY)
        except PermissionError as e:
            raise SafariPermissionError(SafariPermissionError.GUIDANCE) from e
        except plistlib.InvalidFileException:
            # XML plist フォールバック（テスト用）
            with open(self.path, "rb") as f:
                data = plistlib.load(f)
        tree = self._parse_root(data)
        return tree, data

    def _parse_root(self, data: dict) -> BookmarkTree:
        bar = _empty_folder("BookmarksBar", ["BookmarksBar"])
        other = _empty_folder("BookmarksMenu", ["BookmarksMenu"])

        for child in data.get("Children", []):
            title = child.get("Title", "")
            if title == "BookmarksBar":
                parsed = self._parse_folder(child, [])
                bar = parsed
            elif title == "BookmarksMenu":
                parsed = self._parse_folder(child, [])
                other = parsed
            # ReadingList / その他はスキップ

        return BookmarkTree(bar=bar, other=other, source="safari")

    def _parse_folder(self, node: dict, parent_path: list[str]) -> BookmarkFolder:
        title = node.get("Title", "")
        guid = node.get("WebBookmarkUUID", str(uuid.uuid4()))
        path = parent_path + [title] if title else parent_path
        children = []
        for child in node.get("Children", []):
            parsed = self._parse_node(child, path)
            if parsed is not None:
                children.append(parsed)
        date_added = _apple_ts_to_datetime(node.get("DateAdded"))
        return BookmarkFolder(
            title=title,
            guid=guid,
            children=children,
            date_added=date_added,
            date_modified=date_added,
            folder_path=parent_path,
        )

    def _parse_node(self, node: dict, parent_path: list[str]) -> Bookmark | BookmarkFolder | None:
        bm_type = node.get("WebBookmarkType", "")
        if bm_type == "WebBookmarkTypeLeaf":
            url = node.get("URLString", "")
            if not url:
                return None
            uri_dict = node.get("URIDictionary", {})
            title = uri_dict.get("title", "")
            guid = node.get("WebBookmarkUUID", str(uuid.uuid4()))
            date_added = _apple_ts_to_datetime(node.get("DateAdded"))
            return Bookmark(
                title=title,
                url=url,
                guid=guid,
                date_added=date_added,
                date_modified=date_added,
                folder_path=parent_path,
            )
        elif bm_type == "WebBookmarkTypeList":
            return self._parse_folder(node, parent_path)
        # WebBookmarkTypeProxy（区切り線等）はスキップ
        return None

    def flatten(self, tree: BookmarkTree) -> dict[str, Bookmark]:
        result: dict[str, Bookmark] = {}
        _flatten_folder(tree.bar, result)
        _flatten_folder(tree.other, result)
        return result


def _flatten_folder(folder: BookmarkFolder, result: dict[str, Bookmark]) -> None:
    for child in folder.children:
        if isinstance(child, Bookmark):
            result[child.url] = child
        else:
            _flatten_folder(child, result)


def _empty_folder(name: str, path: list[str]) -> BookmarkFolder:
    return BookmarkFolder(
        title=name,
        guid=str(uuid.uuid4()),
        children=[],
        date_added=EPOCH_UTC,
        date_modified=EPOCH_UTC,
        folder_path=path,
    )


class SafariWriter:
    def __init__(self, path: Path = DEFAULT_PATH, backup_dir: Path | None = None) -> None:
        self.path = path
        self.backup_dir = backup_dir

    def write(self, tree: BookmarkTree, original_data: dict) -> None:
        if self.backup_dir:
            self._backup(original_data)
        output = self._build_root_dict(tree, original_data)
        self._atomic_write(output)

    def _backup(self, original_data: dict) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"safari_{ts}.plist"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        with open(backup_path, "wb") as f:
            plistlib.dump(original_data, f, fmt=plistlib.FMT_BINARY)

    def _build_root_dict(self, tree: BookmarkTree, original_data: dict) -> dict:
        output = dict(original_data)
        new_children = []
        bar_added = False
        other_added = False

        for child in original_data.get("Children", []):
            title = child.get("Title", "")
            if title == "BookmarksBar":
                new_children.append(self._folder_to_dict(tree.bar))
                bar_added = True
            elif title == "BookmarksMenu":
                new_children.append(self._folder_to_dict(tree.other))
                other_added = True
            else:
                # ReadingList 等はそのまま保持
                new_children.append(child)

        if not bar_added:
            new_children.insert(0, self._folder_to_dict(tree.bar))
        if not other_added:
            new_children.append(self._folder_to_dict(tree.other))

        output["Children"] = new_children
        return output

    def _folder_to_dict(self, folder: BookmarkFolder) -> dict:
        children = []
        for child in folder.children:
            if isinstance(child, BookmarkFolder):
                children.append(self._folder_to_dict(child))
            else:
                children.append(self._bookmark_to_dict(child))
        node: dict = {
            "Title": folder.title,
            "WebBookmarkType": "WebBookmarkTypeList",
            "WebBookmarkUUID": folder.guid,
            "Children": children,
        }
        if folder.date_added != EPOCH_UTC:
            node["DateAdded"] = _datetime_to_apple_ts(folder.date_added)
        return node

    def _bookmark_to_dict(self, bm: Bookmark) -> dict:
        node: dict = {
            "URLString": bm.url,
            "URIDictionary": {"title": bm.title},
            "WebBookmarkType": "WebBookmarkTypeLeaf",
            "WebBookmarkUUID": bm.guid,
        }
        if bm.date_added != EPOCH_UTC:
            node["DateAdded"] = _datetime_to_apple_ts(bm.date_added)
        return node

    def _atomic_write(self, data: dict) -> None:
        dir_ = self.path.parent
        fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def is_safari_running() -> bool:
    try:
        import psutil
        for proc in psutil.process_iter(attrs=["name"]):
            if proc.info["name"] == "Safari":
                return True
    except Exception:
        pass
    return False


def find_or_create_folder(
    folder: BookmarkFolder, path: list[str], start_index: int = 0
) -> BookmarkFolder:
    if start_index >= len(path):
        return folder
    target = path[start_index]
    for child in folder.children:
        if isinstance(child, BookmarkFolder) and child.title == target:
            return find_or_create_folder(child, path, start_index + 1)
    new_folder = _empty_folder(target, folder.folder_path + [target])
    folder.children.append(new_folder)
    return find_or_create_folder(new_folder, path, start_index + 1)
