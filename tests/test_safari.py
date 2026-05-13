from __future__ import annotations

import io
import plistlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from bsync.models import Bookmark, BookmarkFolder, EPOCH_UTC
from bsync.safari import (
    SafariPermissionError,
    SafariReader,
    SafariWriter,
    _apple_ts_to_datetime,
    find_or_create_folder,
)
from tests.conftest import SAFARI_SIMPLE_DATA, SAFARI_WITH_PROXY_DATA


def _make_reader_from_dict(data: dict) -> tuple[SafariReader, object, dict]:
    reader = SafariReader(Path("/fake/Bookmarks.plist"))
    buf = io.BytesIO()
    plistlib.dump(data, buf, fmt=plistlib.FMT_XML)
    buf.seek(0)
    with patch("builtins.open", return_value=buf):
        # plistlib.load の戻り値をモック
        with patch("plistlib.load", return_value=data):
            tree, original = reader.read()
    return reader, tree, original


class TestSafariReader:
    def test_read_simple(self):
        reader, tree, _ = _make_reader_from_dict(SAFARI_SIMPLE_DATA)
        assert tree.source == "safari"
        assert len(tree.bar.children) == 1
        bm = tree.bar.children[0]
        assert isinstance(bm, Bookmark)
        assert bm.url == "https://example.com"
        assert bm.title == "Example"
        assert bm.guid == "LEAF-UUID-0001"

    def test_read_skips_reading_list(self):
        reader, tree, _ = _make_reader_from_dict(SAFARI_SIMPLE_DATA)
        # bar と other のみ; ReadingList はスキップされている
        def _has_reading_list(folder: BookmarkFolder) -> bool:
            if folder.title == "com.apple.ReadingList":
                return True
            for child in folder.children:
                if isinstance(child, BookmarkFolder) and _has_reading_list(child):
                    return True
            return False
        assert not _has_reading_list(tree.bar)
        assert not _has_reading_list(tree.other)

    def test_read_skips_proxy(self):
        reader, tree, _ = _make_reader_from_dict(SAFARI_WITH_PROXY_DATA)
        assert len(tree.bar.children) == 1
        assert isinstance(tree.bar.children[0], Bookmark)
        assert tree.bar.children[0].url == "https://apple.com"

    def test_timestamp_conversion(self):
        dt = _apple_ts_to_datetime(666543210.0)
        assert dt.tzinfo is not None
        assert dt.year > 2020

    def test_timestamp_none(self):
        dt = _apple_ts_to_datetime(None)
        assert dt == EPOCH_UTC

    def test_timestamp_datetime_object(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dt = _apple_ts_to_datetime(base)
        assert dt == base

    def test_permission_error_raises_custom(self):
        reader = SafariReader(Path("/fake/Bookmarks.plist"))
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with pytest.raises(SafariPermissionError):
                reader.read()

    def test_write_roundtrip(self, tmp_path):
        dst = tmp_path / "Bookmarks.plist"
        with open(dst, "wb") as f:
            plistlib.dump(SAFARI_SIMPLE_DATA, f, fmt=plistlib.FMT_BINARY)
        reader = SafariReader(dst)
        tree, original = reader.read()
        writer = SafariWriter(dst)
        writer.write(tree, original)
        with open(dst, "rb") as f:
            result = plistlib.load(f, fmt=plistlib.FMT_BINARY)
        bar = next(c for c in result["Children"] if c.get("Title") == "BookmarksBar")
        assert bar["Children"][0]["URLString"] == "https://example.com"

    def test_write_preserves_reading_list(self, tmp_path):
        dst = tmp_path / "Bookmarks.plist"
        with open(dst, "wb") as f:
            plistlib.dump(SAFARI_SIMPLE_DATA, f, fmt=plistlib.FMT_BINARY)
        reader = SafariReader(dst)
        tree, original = reader.read()
        writer = SafariWriter(dst)
        writer.write(tree, original)
        with open(dst, "rb") as f:
            result = plistlib.load(f, fmt=plistlib.FMT_BINARY)
        titles = [c.get("Title", "") for c in result["Children"]]
        assert "com.apple.ReadingList" in titles

    def test_write_backup_created(self, tmp_path):
        dst = tmp_path / "Bookmarks.plist"
        backup_dir = tmp_path / "backups"
        with open(dst, "wb") as f:
            plistlib.dump(SAFARI_SIMPLE_DATA, f, fmt=plistlib.FMT_BINARY)
        reader = SafariReader(dst)
        tree, original = reader.read()
        writer = SafariWriter(dst, backup_dir=backup_dir)
        writer.write(tree, original)
        backups = list(backup_dir.glob("safari_*.plist"))
        assert len(backups) == 1


class TestSafariFindOrCreateFolder:
    def test_find_existing(self):
        child = BookmarkFolder(
            title="Work", guid="w", children=[], date_added=EPOCH_UTC, date_modified=EPOCH_UTC, folder_path=["BookmarksBar"]
        )
        root = BookmarkFolder(
            title="BookmarksBar", guid="r", children=[child], date_added=EPOCH_UTC, date_modified=EPOCH_UTC, folder_path=[]
        )
        found = find_or_create_folder(root, ["Work"])
        assert found is child

    def test_create_new(self):
        root = BookmarkFolder(
            title="BookmarksBar", guid="r", children=[], date_added=EPOCH_UTC, date_modified=EPOCH_UTC, folder_path=[]
        )
        created = find_or_create_folder(root, ["NewFolder"])
        assert created.title == "NewFolder"
        assert len(root.children) == 1
