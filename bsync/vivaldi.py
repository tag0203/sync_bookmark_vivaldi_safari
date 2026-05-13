from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Bookmark, BookmarkFolder, BookmarkTree, EPOCH_UTC

DEFAULT_PATH = Path("~/Library/Application Support/Vivaldi/Default/Bookmarks").expanduser()

# 1601-01-01 から 1970-01-01 までのマイクロ秒数
_CHROME_EPOCH_OFFSET_US = 11_644_473_600_000_000


def _chrome_ts_to_datetime(ts_str: str) -> datetime:
    try:
        ts = int(ts_str)
    except (ValueError, TypeError):
        return EPOCH_UTC
    if ts <= 0:
        return EPOCH_UTC
    unix_us = ts - _CHROME_EPOCH_OFFSET_US
    return datetime.fromtimestamp(unix_us / 1_000_000, tz=timezone.utc)


def _datetime_to_chrome_ts(dt: datetime) -> str:
    unix_us = int(dt.timestamp() * 1_000_000)
    return str(unix_us + _CHROME_EPOCH_OFFSET_US)


class VivaldiReader:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path

    def read(self) -> tuple[BookmarkTree, dict]:
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        tree = self._parse_root(data)
        return tree, data

    def _parse_root(self, data: dict) -> BookmarkTree:
        roots = data["roots"]
        bar = self._parse_node(roots["bookmark_bar"], []) if "bookmark_bar" in roots else _empty_folder("bookmark_bar", ["bookmark_bar"])
        other = self._parse_node(roots["other"], []) if "other" in roots else _empty_folder("other", ["other"])
        # bar/other は必ずフォルダ
        if isinstance(bar, Bookmark):
            bar = _empty_folder("bookmark_bar", ["bookmark_bar"])
        if isinstance(other, Bookmark):
            other = _empty_folder("other", ["other"])
        return BookmarkTree(bar=bar, other=other, source="vivaldi")

    def _parse_node(self, node: dict, parent_path: list[str]) -> Bookmark | BookmarkFolder:
        node_type = node.get("type", "url")
        if node_type == "folder":
            title = node.get("name", "")
            guid = node.get("guid", str(uuid.uuid4()))
            path = parent_path + [node.get("name", "")]
            children = [
                self._parse_node(child, path)
                for child in node.get("children", [])
            ]
            return BookmarkFolder(
                title=title,
                guid=guid,
                children=children,
                date_added=_chrome_ts_to_datetime(node.get("date_added", "0")),
                date_modified=_chrome_ts_to_datetime(node.get("date_modified", "0")),
                folder_path=parent_path,
            )
        else:
            title = node.get("name", "")
            url = node.get("url", "")
            guid = node.get("guid", str(uuid.uuid4()))
            date_added = _chrome_ts_to_datetime(node.get("date_added", "0"))
            return Bookmark(
                title=title,
                url=url,
                guid=guid,
                date_added=date_added,
                date_modified=date_added,  # URL ノードには date_modified が存在しない
                folder_path=parent_path,
                meta=node.get("meta_info", {}),
            )

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


class VivaldiWriter:
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
        backup_path = self.backup_dir / f"vivaldi_{ts}.json"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(original_data, f, ensure_ascii=False, indent=2)

    def _build_root_dict(self, tree: BookmarkTree, original_data: dict) -> dict:
        output = dict(original_data)
        roots = dict(original_data.get("roots", {}))
        max_id = _max_id(original_data)
        counter = [max_id + 1]

        roots["bookmark_bar"] = self._folder_to_dict(tree.bar, counter)
        roots["other"] = self._folder_to_dict(tree.other, counter)
        output["roots"] = roots
        output["checksum"] = ""  # Vivaldi が次回起動時に再計算する
        return output

    def _folder_to_dict(self, folder: BookmarkFolder, counter: list[int]) -> dict:
        node_id = str(counter[0])
        counter[0] += 1
        children = []
        for child in folder.children:
            if isinstance(child, BookmarkFolder):
                children.append(self._folder_to_dict(child, counter))
            else:
                children.append(self._bookmark_to_dict(child, counter))
        return {
            "type": "folder",
            "id": node_id,
            "guid": folder.guid,
            "name": folder.title,
            "date_added": _datetime_to_chrome_ts(folder.date_added),
            "date_last_used": "0",
            "date_modified": _datetime_to_chrome_ts(folder.date_modified),
            "children": children,
        }

    def _bookmark_to_dict(self, bm: Bookmark, counter: list[int]) -> dict:
        node_id = str(counter[0])
        counter[0] += 1
        node: dict = {
            "type": "url",
            "id": node_id,
            "guid": bm.guid,
            "name": bm.title,
            "url": bm.url,
            "date_added": _datetime_to_chrome_ts(bm.date_added),
            "date_last_used": "0",
            "meta_info": bm.meta if bm.meta else {"Thumbnail": "AUTOGENERATED"},
        }
        return node

    def _atomic_write(self, data: dict) -> None:
        dir_ = self.path.parent
        fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=3)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def _max_id(data: dict) -> int:
    max_val = 0

    def _walk(node: dict) -> None:
        nonlocal max_val
        try:
            max_val = max(max_val, int(node.get("id", 0)))
        except (ValueError, TypeError):
            pass
        for child in node.get("children", []):
            _walk(child)

    for root in data.get("roots", {}).values():
        if isinstance(root, dict):
            _walk(root)
    return max_val


def is_vivaldi_running() -> bool:
    try:
        import psutil
        for proc in psutil.process_iter(attrs=["name"]):
            if proc.info["name"] == "Vivaldi":
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
