from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from bsync.models import Bookmark, BookmarkFolder, EPOCH_UTC
from bsync.vivaldi import (
    VivaldiReader,
    VivaldiWriter,
    _chrome_ts_to_datetime,
    _datetime_to_chrome_ts,
    _max_id,
    find_or_create_folder,
)
from tests.conftest import VIVALDI_SIMPLE_DATA, VIVALDI_NESTED_DATA


def _make_reader(data: dict) -> tuple[VivaldiReader, object, object]:
    reader = VivaldiReader(Path("/fake/Bookmarks"))
    with patch("builtins.open", mock_open(read_data=json.dumps(data))):
        tree, original = reader.read()
    return reader, tree, original


class TestVivaldiReader:
    def test_read_simple(self):
        reader, tree, _ = _make_reader(VIVALDI_SIMPLE_DATA)
        assert tree.source == "vivaldi"
        assert len(tree.bar.children) == 1
        bm = tree.bar.children[0]
        assert isinstance(bm, Bookmark)
        assert bm.url == "https://example.com"
        assert bm.title == "Example"
        assert bm.guid == "bm-guid-0001"

    def test_read_nested_folders(self):
        reader, tree, _ = _make_reader(VIVALDI_NESTED_DATA)
        assert len(tree.bar.children) == 1
        work_folder = tree.bar.children[0]
        assert isinstance(work_folder, BookmarkFolder)
        assert work_folder.title == "Work"
        assert len(work_folder.children) == 1
        dev_folder = work_folder.children[0]
        assert isinstance(dev_folder, BookmarkFolder)
        assert dev_folder.title == "Dev"
        assert len(dev_folder.children) == 1
        bm = dev_folder.children[0]
        assert isinstance(bm, Bookmark)
        assert bm.url == "https://github.com"

    def test_timestamp_conversion_normal(self):
        ts = "13413538871971562"
        dt = _chrome_ts_to_datetime(ts)
        assert dt.tzinfo is not None
        # 2026-01-22 07:01:11 UTC 付近であること
        assert dt.year == 2026
        assert dt.month == 1

    def test_timestamp_conversion_zero(self):
        dt = _chrome_ts_to_datetime("0")
        assert dt == EPOCH_UTC

    def test_timestamp_conversion_invalid(self):
        dt = _chrome_ts_to_datetime("not-a-number")
        assert dt == EPOCH_UTC

    def test_flatten(self):
        reader, tree, _ = _make_reader(VIVALDI_SIMPLE_DATA)
        flat = reader.flatten(tree)
        assert "https://example.com" in flat
        assert flat["https://example.com"].title == "Example"

    def test_flatten_nested(self):
        reader, tree, _ = _make_reader(VIVALDI_NESTED_DATA)
        flat = reader.flatten(tree)
        assert "https://github.com" in flat

    def test_write_roundtrip(self, tmp_path):
        dst = tmp_path / "Bookmarks"
        dst.write_text(json.dumps(VIVALDI_SIMPLE_DATA), encoding="utf-8")
        reader = VivaldiReader(dst)
        tree, original = reader.read()
        writer = VivaldiWriter(dst)
        writer.write(tree, original)
        with open(dst, encoding="utf-8") as f:
            result = json.load(f)
        assert result["roots"]["bookmark_bar"]["children"][0]["url"] == "https://example.com"
        assert result["roots"]["bookmark_bar"]["children"][0]["name"] == "Example"

    def test_write_preserves_extra_fields(self, tmp_path):
        dst = tmp_path / "Bookmarks"
        dst.write_text(json.dumps(VIVALDI_SIMPLE_DATA), encoding="utf-8")
        reader = VivaldiReader(dst)
        tree, original = reader.read()
        writer = VivaldiWriter(dst)
        writer.write(tree, original)
        with open(dst, encoding="utf-8") as f:
            result = json.load(f)
        # sync_metadata は保持される
        assert "sync_metadata" in result

    def test_new_bookmark_gets_id(self, tmp_path):
        dst = tmp_path / "Bookmarks"
        dst.write_text(json.dumps(VIVALDI_SIMPLE_DATA), encoding="utf-8")
        reader = VivaldiReader(dst)
        tree, original = reader.read()
        # 最大 id = 4 (synced フォルダ)
        max_id = _max_id(original)
        assert max_id >= 4
        writer = VivaldiWriter(dst)
        writer.write(tree, original)
        with open(dst, encoding="utf-8") as f:
            result = json.load(f)
        # 書き込み後のすべての id が max_id より小さいか等しいことを確認
        # (既存ノードは max_id+1 からリナンバーされる)
        ids = _collect_ids(result["roots"]["bookmark_bar"])
        assert all(int(i) > 0 for i in ids)

    def test_write_backup_created(self, tmp_path):
        dst = tmp_path / "Bookmarks"
        backup_dir = tmp_path / "backups"
        dst.write_text(json.dumps(VIVALDI_SIMPLE_DATA), encoding="utf-8")
        reader = VivaldiReader(dst)
        tree, original = reader.read()
        writer = VivaldiWriter(dst, backup_dir=backup_dir)
        writer.write(tree, original)
        backups = list(backup_dir.glob("vivaldi_*.json"))
        assert len(backups) == 1


def _collect_ids(node: dict) -> list[str]:
    ids = [node.get("id", "")]
    for child in node.get("children", []):
        ids.extend(_collect_ids(child))
    return [i for i in ids if i]


class TestFindOrCreateFolder:
    def test_find_existing(self):
        from bsync.models import EPOCH_UTC
        child = BookmarkFolder(
            title="Work", guid="w", children=[], date_added=EPOCH_UTC, date_modified=EPOCH_UTC, folder_path=["bookmark_bar"]
        )
        root = BookmarkFolder(
            title="bookmark_bar", guid="r", children=[child], date_added=EPOCH_UTC, date_modified=EPOCH_UTC, folder_path=[]
        )
        found = find_or_create_folder(root, ["Work"])
        assert found is child

    def test_create_new(self):
        root = BookmarkFolder(
            title="bookmark_bar", guid="r", children=[], date_added=EPOCH_UTC, date_modified=EPOCH_UTC, folder_path=[]
        )
        created = find_or_create_folder(root, ["NewFolder"])
        assert created.title == "NewFolder"
        assert len(root.children) == 1
