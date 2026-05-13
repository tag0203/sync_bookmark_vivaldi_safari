from __future__ import annotations

import uuid
from datetime import timezone

from .models import (
    Bookmark,
    BookmarkFolder,
    BookmarkTree,
    ConflictRecord,
    MergeResult,
    SyncRecord,
    EPOCH_UTC,
)

# Vivaldi ルートフォルダ名 → Safari ルートフォルダ名 のマッピング
_VIV_TO_SAF_ROOT = {"bookmark_bar": "BookmarksBar", "other": "BookmarksMenu"}
_SAF_TO_VIV_ROOT = {v: k for k, v in _VIV_TO_SAF_ROOT.items()}


class MergeEngine:
    def __init__(self, strategy: str = "newer") -> None:
        if strategy not in ("newer", "vivaldi", "safari"):
            raise ValueError(f"Unknown strategy: {strategy}")
        self.strategy = strategy

    def merge(
        self,
        base: dict[str, SyncRecord],
        vivaldi: dict[str, Bookmark],
        safari: dict[str, Bookmark],
    ) -> MergeResult:
        result = MergeResult()
        all_urls = set(base) | set(vivaldi) | set(safari)

        for url in all_urls:
            in_base = url in base
            in_viv = url in vivaldi
            in_saf = url in safari

            if not in_base and in_viv and not in_saf:
                # Vivaldi のみに存在 → Safari へ追加
                result.to_add_safari.append(vivaldi[url])

            elif not in_base and not in_viv and in_saf:
                # Safari のみに存在 → Vivaldi へ追加
                result.to_add_vivaldi.append(safari[url])

            elif in_base and not in_viv and not in_saf:
                # Base のみ（両方で削除済み）→ 何もしない
                pass

            elif in_base and in_viv and not in_saf:
                # Safari で削除 → Vivaldi からも削除
                result.to_delete_vivaldi.append(url)

            elif in_base and not in_viv and in_saf:
                # Vivaldi で削除 → Safari からも削除
                result.to_delete_safari.append(url)

            elif in_base and in_viv and in_saf:
                # 全てに存在 → 変更を比較
                self._handle_existing(url, base[url], vivaldi[url], safari[url], result)

            elif not in_base and in_viv and in_saf:
                # Base になく両方に存在（独立した追加）
                self._handle_independent_add(url, vivaldi[url], safari[url], result)

        return result

    def _handle_existing(
        self,
        url: str,
        base: SyncRecord,
        viv: Bookmark,
        saf: Bookmark,
        result: MergeResult,
    ) -> None:
        viv_changed = _is_changed_vivaldi(viv, base)
        saf_changed = _is_changed_safari(saf, base)

        if not viv_changed and not saf_changed:
            pass  # 変更なし
        elif viv_changed and not saf_changed:
            result.to_update_safari.append(viv)
        elif not viv_changed and saf_changed:
            result.to_update_vivaldi.append(saf)
        else:
            self._resolve_conflict(url, viv, saf, result)

    def _handle_independent_add(
        self,
        url: str,
        viv: Bookmark,
        saf: Bookmark,
        result: MergeResult,
    ) -> None:
        if viv.title == saf.title:
            pass  # タイトルが同じ → 同一追加とみなしてスキップ
        else:
            self._resolve_conflict(url, viv, saf, result)

    def _resolve_conflict(
        self,
        url: str,
        viv: Bookmark,
        saf: Bookmark,
        result: MergeResult,
    ) -> None:
        if self.strategy == "vivaldi":
            winner = viv
            resolution = "vivaldi"
            result.to_update_safari.append(winner)
        elif self.strategy == "safari":
            winner = saf
            resolution = "safari"
            result.to_update_vivaldi.append(winner)
        else:  # "newer"
            if viv.date_modified >= saf.date_modified:
                winner = viv
                resolution = "vivaldi_newer"
                result.to_update_safari.append(winner)
            else:
                winner = saf
                resolution = "safari_newer"
                result.to_update_vivaldi.append(winner)

        result.conflicts.append(
            ConflictRecord(
                url=url,
                vivaldi_title=viv.title,
                safari_title=saf.title,
                resolved_title=winner.title,
                resolution=resolution,
            )
        )


def _is_changed_vivaldi(bm: Bookmark, base: SyncRecord) -> bool:
    return bm.title != base.title or bm.folder_path != base.folder_path_vivaldi


def _is_changed_safari(bm: Bookmark, base: SyncRecord) -> bool:
    return bm.title != base.title or bm.folder_path != base.folder_path_safari


def apply_additions_to_vivaldi_tree(
    tree: BookmarkTree, bookmarks: list[Bookmark]
) -> None:
    """マージ結果の to_add_vivaldi を BookmarkTree に反映する。"""
    from .vivaldi import find_or_create_folder

    for bm in bookmarks:
        # Safari のフォルダパスを Vivaldi パスに変換
        viv_path = _safari_path_to_vivaldi(bm.folder_path)
        root = tree.bar if (not viv_path or viv_path[0] == "bookmark_bar") else tree.other
        sub_path = viv_path[1:] if viv_path else []
        target_folder = find_or_create_folder(root, sub_path) if sub_path else root
        new_bm = Bookmark(
            title=bm.title,
            url=bm.url,
            guid=str(uuid.uuid4()),
            date_added=bm.date_added,
            date_modified=bm.date_modified,
            folder_path=target_folder.folder_path + [target_folder.title],
        )
        target_folder.children.append(new_bm)


def apply_additions_to_safari_tree(
    tree: BookmarkTree, bookmarks: list[Bookmark]
) -> None:
    """マージ結果の to_add_safari を BookmarkTree に反映する。"""
    from .safari import find_or_create_folder

    for bm in bookmarks:
        # Vivaldi のフォルダパスを Safari パスに変換
        saf_path = _vivaldi_path_to_safari(bm.folder_path)
        root = tree.bar if (not saf_path or saf_path[0] == "BookmarksBar") else tree.other
        sub_path = saf_path[1:] if saf_path else []
        target_folder = find_or_create_folder(root, sub_path) if sub_path else root
        new_bm = Bookmark(
            title=bm.title,
            url=bm.url,
            guid=str(uuid.uuid4()),
            date_added=bm.date_added,
            date_modified=bm.date_modified,
            folder_path=target_folder.folder_path + [target_folder.title],
        )
        target_folder.children.append(new_bm)


def apply_deletions_to_tree(tree: BookmarkTree, urls_to_delete: list[str]) -> None:
    """BookmarkTree から指定 URL のブックマークを削除する。"""
    url_set = set(urls_to_delete)
    _delete_from_folder(tree.bar, url_set)
    _delete_from_folder(tree.other, url_set)


def _delete_from_folder(folder: BookmarkFolder, url_set: set[str]) -> None:
    new_children = []
    for child in folder.children:
        if isinstance(child, Bookmark):
            if child.url not in url_set:
                new_children.append(child)
        else:
            _delete_from_folder(child, url_set)
            new_children.append(child)
    folder.children = new_children


def apply_updates_to_tree(tree: BookmarkTree, bookmarks: list[Bookmark]) -> None:
    """BookmarkTree 内の指定ブックマークのタイトルを更新する。"""
    url_to_bm = {bm.url: bm for bm in bookmarks}
    _update_in_folder(tree.bar, url_to_bm)
    _update_in_folder(tree.other, url_to_bm)


def _update_in_folder(folder: BookmarkFolder, url_to_bm: dict[str, Bookmark]) -> None:
    for child in folder.children:
        if isinstance(child, Bookmark):
            if child.url in url_to_bm:
                child.title = url_to_bm[child.url].title
        else:
            _update_in_folder(child, url_to_bm)


def _vivaldi_path_to_safari(path: list[str]) -> list[str]:
    if not path:
        return ["BookmarksBar"]
    root = path[0]
    saf_root = _VIV_TO_SAF_ROOT.get(root, "BookmarksBar")
    return [saf_root] + path[1:]


def _safari_path_to_vivaldi(path: list[str]) -> list[str]:
    if not path:
        return ["bookmark_bar"]
    root = path[0]
    viv_root = _SAF_TO_VIV_ROOT.get(root, "bookmark_bar")
    return [viv_root] + path[1:]
