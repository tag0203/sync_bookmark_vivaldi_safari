import json
import plistlib
from copy import deepcopy
from io import StringIO
from unittest.mock import Mock

import pytest
from rich.console import Console

from bsync import cli, safari, vivaldi, watcher
from bsync.merge import MergeEngine
from bsync.models import MergeResult
from bsync.pending import PendingChange
from bsync.snapshot import SnapshotManager
from tests.conftest import SAFARI_SIMPLE_DATA, VIVALDI_SIMPLE_DATA, make_bookmark, make_sync_record


QUEUED_URL = "https://queued-addition.invalid"


@pytest.fixture
def pending_sync(tmp_path, monkeypatch):
    vivaldi_path = tmp_path / "Bookmarks"
    safari_path = tmp_path / "Bookmarks.plist"
    pending_path = tmp_path / "pending.json"
    snapshot = SnapshotManager(tmp_path / "last_sync.json")

    vivaldi_path.write_text(json.dumps(VIVALDI_SIMPLE_DATA), encoding="utf-8")
    safari_data = deepcopy(SAFARI_SIMPLE_DATA)
    safari_bookmarks = safari_data["Children"][0]["Children"]
    new_bookmark = dict(safari_bookmarks[0], URLString=QUEUED_URL, WebBookmarkUUID="QUEUED-UUID")
    safari_bookmarks.append(new_bookmark)
    safari_path.write_bytes(plistlib.dumps(safari_data, fmt=plistlib.FMT_BINARY))

    monkeypatch.setattr(vivaldi, "DEFAULT_PATH", vivaldi_path)
    monkeypatch.setattr(safari, "DEFAULT_PATH", safari_path)
    monkeypatch.setattr(cli, "BSYNC_DIR", tmp_path)
    monkeypatch.setattr(cli, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(cli, "PENDING_PATH", pending_path)
    monkeypatch.setattr(cli, "SnapshotManager", lambda: snapshot)
    monkeypatch.setattr(cli, "is_safari_running", lambda: False)

    def sync(*, running=False, dry_run=False, **options):
        monkeypatch.setattr(cli, "is_vivaldi_running", lambda: running)
        return cli._run_sync(dry_run, "newer", Console(file=StringIO()), **options)

    return sync, pending_path, snapshot, vivaldi_path, safari_path


def test_existing_pending_addition_overrides_false_safari_deletion(pending_sync):
    sync, pending_path, snapshot, vivaldi_path, safari_path = pending_sync
    pending_path.write_text(json.dumps({"to_add_vivaldi": [{"url": QUEUED_URL}]}), encoding="utf-8")
    # Older versions recorded the queued addition as if it had already been written.
    snapshot.save(
        {QUEUED_URL: make_bookmark(url=QUEUED_URL)},
        {QUEUED_URL: make_bookmark(url=QUEUED_URL)},
        MergeResult(),
    )
    safari_before = safari_path.read_bytes()
    pending_before = pending_path.read_bytes()

    preview = sync(dry_run=True)

    assert [bookmark.url for bookmark in preview.to_add_vivaldi] == [QUEUED_URL]
    assert not preview.to_delete_safari
    assert pending_path.read_bytes() == pending_before

    sync()

    reader = vivaldi.VivaldiReader(vivaldi_path)
    assert QUEUED_URL in reader.flatten(reader.read()[0])
    assert safari_path.read_bytes() == safari_before
    assert not pending_path.exists()
    assert not sync(dry_run=True).to_add_vivaldi


def test_failed_vivaldi_write_preserves_pending_and_previous_snapshot(pending_sync, monkeypatch):
    sync, pending_path, snapshot, vivaldi_path, _ = pending_sync
    vivaldi_before = vivaldi_path.read_bytes()
    sync(running=True)
    assert pending_path.exists()
    assert QUEUED_URL not in snapshot.load()

    with monkeypatch.context() as patch:
        patch.setattr(vivaldi.VivaldiWriter, "write", Mock(side_effect=OSError("write failed")))
        sync()

    assert pending_path.exists()
    assert QUEUED_URL not in snapshot.load()
    assert vivaldi_path.read_bytes() == vivaldi_before

    sync()
    assert QUEUED_URL in snapshot.load()
    assert not pending_path.exists()


def test_pending_outside_selected_folder_remains_queued(pending_sync):
    sync, pending_path, snapshot, vivaldi_path, safari_path = pending_sync
    sync(running=True)
    browser_files = (vivaldi_path.read_bytes(), safari_path.read_bytes())

    sync(vivaldi_folders="other", safari_folders="BookmarksMenu")

    assert pending_path.exists()
    assert QUEUED_URL not in snapshot.load()
    assert (vivaldi_path.read_bytes(), safari_path.read_bytes()) == browser_files


def test_watch_processes_pending_at_startup_when_vivaldi_is_closed(pending_sync, monkeypatch):
    sync, pending_path, _, vivaldi_path, _ = pending_sync
    sync(running=True)
    monkeypatch.setattr(cli, "is_vivaldi_running", lambda: False)
    monkeypatch.setattr(watcher, "is_vivaldi_running", lambda: False)
    monkeypatch.setattr(watcher, "PENDING_PATH", pending_path)
    monkeypatch.setattr(watcher, "Observer", Mock())
    monkeypatch.setattr(watcher.signal, "signal", Mock())
    bookmark_watcher = watcher.BookmarkWatcher()
    monkeypatch.setattr(watcher.time, "sleep", lambda _: bookmark_watcher._stop_event.set())

    bookmark_watcher.start()

    reader = vivaldi.VivaldiReader(vivaldi_path)
    assert QUEUED_URL in reader.flatten(reader.read()[0])
    assert not pending_path.exists()


def test_legacy_queued_deletion_survives_missing_snapshot_record():
    bookmark = make_bookmark(url=QUEUED_URL)
    result = MergeEngine().merge(
        {}, {QUEUED_URL: bookmark}, {}, pending={QUEUED_URL: PendingChange("delete")}
    )

    assert result.to_delete_vivaldi == [QUEUED_URL]
    assert not result.to_add_safari


def test_queued_update_respects_another_edit_in_vivaldi():
    vivaldi_bookmark = make_bookmark(url=QUEUED_URL, title="New Vivaldi title")
    safari_bookmark = make_bookmark(url=QUEUED_URL, title="New Safari title")
    base = make_sync_record(url=QUEUED_URL, title="Original title")
    pending = {QUEUED_URL: PendingChange("update", {"previous_vivaldi_title": base.title})}

    result = MergeEngine(strategy="vivaldi").merge(
        {QUEUED_URL: base}, {QUEUED_URL: vivaldi_bookmark}, {QUEUED_URL: safari_bookmark},
        pending=pending,
    )

    assert result.to_update_safari == [vivaldi_bookmark]
    assert not result.to_update_vivaldi
