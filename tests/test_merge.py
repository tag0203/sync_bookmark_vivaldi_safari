from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bsync.merge import MergeEngine
from bsync.models import Bookmark, SyncRecord, EPOCH_UTC
from tests.conftest import make_bookmark, make_sync_record

_NOW = datetime(2026, 1, 22, 7, 0, 0, tzinfo=timezone.utc)
_OLDER = datetime(2026, 1, 21, 7, 0, 0, tzinfo=timezone.utc)

URL = "https://example.com"


def _merge(base, vivaldi, safari, strategy="newer"):
    return MergeEngine(strategy=strategy).merge(base, vivaldi, safari)


class TestMergeEngine:
    # (F,T,F) Vivaldi のみに存在 → Safari へ追加
    def test_vivaldi_only_new(self):
        bm = make_bookmark(url=URL)
        result = _merge({}, {URL: bm}, {})
        assert len(result.to_add_safari) == 1
        assert result.to_add_safari[0].url == URL
        assert not result.to_add_vivaldi

    # (F,F,T) Safari のみに存在 → Vivaldi へ追加
    def test_safari_only_new(self):
        bm = make_bookmark(url=URL)
        result = _merge({}, {}, {URL: bm})
        assert len(result.to_add_vivaldi) == 1
        assert result.to_add_vivaldi[0].url == URL
        assert not result.to_add_safari

    # (T,F,F) Base のみ（両方で削除済み） → 何もしない
    def test_base_only_both_deleted(self):
        base = {URL: make_sync_record(url=URL)}
        result = _merge(base, {}, {})
        assert not result.to_add_vivaldi
        assert not result.to_add_safari
        assert not result.to_delete_vivaldi
        assert not result.to_delete_safari

    # (T,T,F) Safari が削除 → Vivaldi からも削除
    def test_vivaldi_deleted(self):
        base = {URL: make_sync_record(url=URL)}
        bm = make_bookmark(url=URL)
        result = _merge(base, {URL: bm}, {})
        assert URL in result.to_delete_vivaldi
        assert not result.to_delete_safari

    # (T,F,T) Vivaldi が削除 → Safari からも削除
    def test_safari_deleted(self):
        base = {URL: make_sync_record(url=URL)}
        bm = make_bookmark(url=URL)
        result = _merge(base, {}, {URL: bm})
        assert URL in result.to_delete_safari
        assert not result.to_delete_vivaldi

    # (T,T,T) 変更なし → 何もしない
    def test_no_change(self):
        base = {URL: make_sync_record(url=URL, title="Example")}
        viv = make_bookmark(url=URL, title="Example", folder_path=["bookmark_bar"])
        saf = make_bookmark(url=URL, title="Example", folder_path=["BookmarksBar"])
        result = _merge(base, {URL: viv}, {URL: saf})
        assert not any([
            result.to_add_vivaldi, result.to_add_safari,
            result.to_delete_vivaldi, result.to_delete_safari,
            result.to_update_vivaldi, result.to_update_safari,
            result.conflicts,
        ])

    # Vivaldi のみ変更 → Safari に更新
    def test_vivaldi_changed(self):
        base = {URL: make_sync_record(url=URL, title="OldTitle")}
        viv = make_bookmark(url=URL, title="NewTitle", folder_path=["bookmark_bar"])
        saf = make_bookmark(url=URL, title="OldTitle", folder_path=["BookmarksBar"])
        result = _merge(base, {URL: viv}, {URL: saf})
        assert len(result.to_update_safari) == 1
        assert result.to_update_safari[0].title == "NewTitle"
        assert not result.to_update_vivaldi

    # Safari のみ変更 → Vivaldi に更新
    def test_safari_changed(self):
        base = {URL: make_sync_record(url=URL, title="OldTitle")}
        viv = make_bookmark(url=URL, title="OldTitle", folder_path=["bookmark_bar"])
        saf = make_bookmark(url=URL, title="NewTitle", folder_path=["BookmarksBar"])
        result = _merge(base, {URL: viv}, {URL: saf})
        assert len(result.to_update_vivaldi) == 1
        assert result.to_update_vivaldi[0].title == "NewTitle"
        assert not result.to_update_safari

    # 両方変更、Vivaldi が新しい → Vivaldi 優先 (newer)
    def test_conflict_newer_vivaldi(self):
        base = {URL: make_sync_record(url=URL, title="OldTitle")}
        viv = make_bookmark(url=URL, title="VivTitle", date_modified=_NOW)
        saf = make_bookmark(url=URL, title="SafTitle", date_modified=_OLDER)
        result = _merge(base, {URL: viv}, {URL: saf}, strategy="newer")
        assert len(result.conflicts) == 1
        assert result.conflicts[0].resolution == "vivaldi_newer"
        assert result.conflicts[0].resolved_title == "VivTitle"
        assert any(bm.title == "VivTitle" for bm in result.to_update_safari)

    # 両方変更、Safari が新しい → Safari 優先 (newer)
    def test_conflict_newer_safari(self):
        base = {URL: make_sync_record(url=URL, title="OldTitle")}
        viv = make_bookmark(url=URL, title="VivTitle", date_modified=_OLDER)
        saf = make_bookmark(url=URL, title="SafTitle", date_modified=_NOW)
        result = _merge(base, {URL: viv}, {URL: saf}, strategy="newer")
        assert len(result.conflicts) == 1
        assert result.conflicts[0].resolution == "safari_newer"
        assert result.conflicts[0].resolved_title == "SafTitle"
        assert any(bm.title == "SafTitle" for bm in result.to_update_vivaldi)

    # strategy="vivaldi" → 常に Vivaldi 優先
    def test_conflict_strategy_vivaldi(self):
        base = {URL: make_sync_record(url=URL, title="OldTitle")}
        viv = make_bookmark(url=URL, title="VivTitle", date_modified=_OLDER)
        saf = make_bookmark(url=URL, title="SafTitle", date_modified=_NOW)
        result = _merge(base, {URL: viv}, {URL: saf}, strategy="vivaldi")
        assert result.conflicts[0].resolution == "vivaldi"
        assert result.conflicts[0].resolved_title == "VivTitle"

    # strategy="safari" → 常に Safari 優先
    def test_conflict_strategy_safari(self):
        base = {URL: make_sync_record(url=URL, title="OldTitle")}
        viv = make_bookmark(url=URL, title="VivTitle", date_modified=_NOW)
        saf = make_bookmark(url=URL, title="SafTitle", date_modified=_OLDER)
        result = _merge(base, {URL: viv}, {URL: saf}, strategy="safari")
        assert result.conflicts[0].resolution == "safari"
        assert result.conflicts[0].resolved_title == "SafTitle"

    # (F,T,T) 独立した追加、タイトルが同じ → スキップ
    def test_independent_add_same_title(self):
        viv = make_bookmark(url=URL, title="Example")
        saf = make_bookmark(url=URL, title="Example")
        result = _merge({}, {URL: viv}, {URL: saf})
        assert not result.to_add_vivaldi
        assert not result.to_add_safari
        assert not result.conflicts

    # (F,T,T) 独立した追加、タイトルが異なる → 競合
    def test_independent_add_different_title(self):
        viv = make_bookmark(url=URL, title="VivTitle", date_modified=_NOW)
        saf = make_bookmark(url=URL, title="SafTitle", date_modified=_OLDER)
        result = _merge({}, {URL: viv}, {URL: saf})
        assert len(result.conflicts) == 1

    # 競合が conflicts リストに記録される
    def test_conflict_logged(self):
        base = {URL: make_sync_record(url=URL, title="OldTitle")}
        viv = make_bookmark(url=URL, title="VivTitle", date_modified=_NOW)
        saf = make_bookmark(url=URL, title="SafTitle", date_modified=_OLDER)
        result = _merge(base, {URL: viv}, {URL: saf})
        assert len(result.conflicts) == 1
        c = result.conflicts[0]
        assert c.url == URL
        assert c.vivaldi_title == "VivTitle"
        assert c.safari_title == "SafTitle"

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValueError):
            MergeEngine(strategy="invalid")
