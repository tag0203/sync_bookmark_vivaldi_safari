from __future__ import annotations

import pytest

from bsync.filters import (
    parse_folder_specs,
    _path_matches,
    _matches_any,
    filter_flat_vivaldi,
    filter_flat_safari,
    filter_snapshot,
)
from tests.conftest import make_bookmark, make_sync_record


# --- parse_folder_specs ---

def test_parse_empty():
    assert parse_folder_specs("") == []


def test_parse_none_like():
    assert parse_folder_specs("   ") == []


def test_parse_root_only():
    assert parse_folder_specs("bookmark_bar") == [["bookmark_bar"]]


def test_parse_nested():
    assert parse_folder_specs("bookmark_bar/Tech") == [["bookmark_bar", "Tech"]]


def test_parse_multi():
    result = parse_folder_specs("bookmark_bar/Tech,other")
    assert result == [["bookmark_bar", "Tech"], ["other"]]


def test_parse_extra_whitespace():
    result = parse_folder_specs(" bookmark_bar / Tech , other ")
    assert result == [["bookmark_bar", "Tech"], ["other"]]


# --- _path_matches ---

def test_path_matches_exact():
    assert _path_matches(["bookmark_bar"], ["bookmark_bar"])


def test_path_matches_prefix():
    assert _path_matches(["bookmark_bar", "Tech", "Python"], ["bookmark_bar", "Tech"])


def test_path_not_matches():
    assert not _path_matches(["bookmark_bar", "Other"], ["bookmark_bar", "Tech"])


def test_path_too_short():
    assert not _path_matches(["bookmark_bar"], ["bookmark_bar", "Tech"])


# --- filter_flat_vivaldi ---

def test_filter_vivaldi_no_prefixes_returns_all():
    flat = {
        "https://a.com": make_bookmark(url="https://a.com", folder_path=["bookmark_bar"]),
        "https://b.com": make_bookmark(url="https://b.com", folder_path=["other"]),
    }
    assert filter_flat_vivaldi(flat, []) is flat


def test_filter_vivaldi_filters_by_root():
    flat = {
        "https://a.com": make_bookmark(url="https://a.com", folder_path=["bookmark_bar"]),
        "https://b.com": make_bookmark(url="https://b.com", folder_path=["other"]),
    }
    result = filter_flat_vivaldi(flat, [["bookmark_bar"]])
    assert "https://a.com" in result
    assert "https://b.com" not in result


def test_filter_vivaldi_includes_subfolder():
    flat = {
        "https://a.com": make_bookmark(url="https://a.com", folder_path=["bookmark_bar", "Tech"]),
        "https://b.com": make_bookmark(url="https://b.com", folder_path=["bookmark_bar", "Other"]),
    }
    result = filter_flat_vivaldi(flat, [["bookmark_bar", "Tech"]])
    assert "https://a.com" in result
    assert "https://b.com" not in result


def test_filter_vivaldi_includes_deep_subfolder():
    flat = {
        "https://a.com": make_bookmark(
            url="https://a.com", folder_path=["bookmark_bar", "Tech", "Python"]
        ),
    }
    result = filter_flat_vivaldi(flat, [["bookmark_bar", "Tech"]])
    assert "https://a.com" in result


# --- filter_flat_safari (Safari形式パスで直接比較) ---

def test_filter_safari_no_prefixes_returns_all():
    flat = {
        "https://a.com": make_bookmark(url="https://a.com", folder_path=["BookmarksBar"]),
    }
    assert filter_flat_safari(flat, []) is flat


def test_filter_safari_bar_root():
    flat = {
        "https://a.com": make_bookmark(url="https://a.com", folder_path=["BookmarksBar"]),
        "https://b.com": make_bookmark(url="https://b.com", folder_path=["BookmarksMenu"]),
    }
    result = filter_flat_safari(flat, [["BookmarksBar"]])
    assert "https://a.com" in result
    assert "https://b.com" not in result


def test_filter_safari_subfolder():
    flat = {
        "https://a.com": make_bookmark(
            url="https://a.com", folder_path=["BookmarksBar", "Tech"]
        ),
        "https://b.com": make_bookmark(
            url="https://b.com", folder_path=["BookmarksBar", "Other"]
        ),
    }
    result = filter_flat_safari(flat, [["BookmarksBar", "Tech"]])
    assert "https://a.com" in result
    assert "https://b.com" not in result


# --- filter_snapshot ---

def test_filter_snapshot_no_prefixes_returns_all():
    base = {"https://a.com": make_sync_record(url="https://a.com")}
    assert filter_snapshot(base, [], []) is base


def test_filter_snapshot_by_vivaldi_path():
    base = {
        "https://a.com": make_sync_record(
            url="https://a.com",
            folder_path_vivaldi=["bookmark_bar"],
            folder_path_safari=["BookmarksBar"],
        ),
        "https://b.com": make_sync_record(
            url="https://b.com",
            folder_path_vivaldi=["other"],
            folder_path_safari=["BookmarksMenu"],
        ),
    }
    result = filter_snapshot(base, [["bookmark_bar"]], [])
    assert "https://a.com" in result
    assert "https://b.com" not in result


def test_filter_snapshot_by_safari_path():
    base = {
        "https://a.com": make_sync_record(
            url="https://a.com",
            folder_path_vivaldi=["bookmark_bar"],
            folder_path_safari=["BookmarksBar"],
        ),
        "https://b.com": make_sync_record(
            url="https://b.com",
            folder_path_vivaldi=["other"],
            folder_path_safari=["BookmarksMenu"],
        ),
    }
    result = filter_snapshot(base, [], [["BookmarksBar"]])
    assert "https://a.com" in result
    assert "https://b.com" not in result


def test_filter_snapshot_and_condition():
    """両方指定した場合は AND 条件で絞り込む。"""
    base = {
        "https://a.com": make_sync_record(
            url="https://a.com",
            folder_path_vivaldi=["bookmark_bar", "Tech"],
            folder_path_safari=["BookmarksBar", "Tech"],
        ),
        "https://b.com": make_sync_record(
            url="https://b.com",
            folder_path_vivaldi=["bookmark_bar", "Tech"],
            folder_path_safari=["BookmarksMenu"],
        ),
    }
    result = filter_snapshot(base, [["bookmark_bar", "Tech"]], [["BookmarksBar"]])
    assert "https://a.com" in result
    assert "https://b.com" not in result
