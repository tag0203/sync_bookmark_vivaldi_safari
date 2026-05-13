# bsync — Claude Code コンテキスト

## プロジェクト概要

macOS 上で Vivaldi と Safari のブックマークを双方向同期する Python 製 CLI ツール。
Safari は iCloud 経由で iOS Safari と自動同期されるため、iOS の変更も Vivaldi に反映できる。

## ファイル構成

```
bsync/                    # パッケージ本体
├── models.py             # 共通データモデル（Bookmark / BookmarkFolder / MergeResult 等）
├── vivaldi.py            # VivaldiReader / VivaldiWriter
├── safari.py             # SafariReader / SafariWriter / SafariPermissionError
├── merge.py              # MergeEngine（3ウェイマージ）+ tree 操作ユーティリティ
├── snapshot.py           # SnapshotManager（~/.bsync/last_sync.json）
├── watcher.py            # BookmarkWatcher（watchdog FSEvents + DebounceTimer）
└── cli.py                # Click CLI エントリーポイント（_run_sync が中核）
tests/
├── conftest.py           # フィクスチャ（VIVALDI_SIMPLE_DATA / SAFARI_SIMPLE_DATA 等）
├── test_vivaldi.py
├── test_safari.py
└── test_merge.py
pyproject.toml            # setuptools ベース（hatchling は editable install に問題あり）
```

## 重要な設計判断

### URL を同期の一次キー
Vivaldi と Safari の間で唯一共有できる安定識別子が URL のため、GUID ではなく URL をキーにしている。

### タイムスタンプ変換
- Vivaldi: Chrome time = 1601-01-01 起算マイクロ秒、オフセット `11_644_473_600_000_000`
- Safari: Apple Core Data 時間 = 2001-01-01 起算秒、オフセット `978_307_200.0`
- Safari の `DateAdded` は plistlib が `datetime` オブジェクトか `float` のどちらかを返す（両方に対応済み）

### Vivaldi date_modified
URL ノード（`type: "url"`）には `date_modified` フィールドが実際には存在しない。
書き込み時も含めず、`date_modified = date_added` で代用する。

### Vivaldi checksum
書き込み時は `checksum = ""` にする。Vivaldi は不一致でも soft error として読み込み続け、次回起動時に自動再計算する。

### SafariPermissionError
Safari plist は Full Disk Access がないと `PermissionError` になる。
`safari.py` でカスタム例外に変換し、`cli.py` でキャッチして終了コード 2 を返す。

### Vivaldi 起動中の書き込みキュー
psutil でプロセス名 `"Vivaldi"` を検出（`"Vivaldi Helper (Renderer)"` 等は除外）。
起動中は `~/.bsync/pending.json` にキューし、`watch` モードで終了を検知してから書き込む。

### フォルダルートマッピング
| Vivaldi | Safari |
|---|---|
| `bookmark_bar` | `BookmarksBar` |
| `other` | `BookmarksMenu` |

`merge.py` の `_vivaldi_path_to_safari()` / `_safari_path_to_vivaldi()` が変換する。

## データディレクトリ
```
~/.bsync/
├── last_sync.json   # スナップショット（バージョン 1）
├── pending.json     # Vivaldi 向け未書込みキュー
├── bsync.log        # 競合・エラーログ（WARNING 以上のみ）
└── backups/
    ├── vivaldi_YYYYMMDD_HHMMSS.json
    └── safari_YYYYMMDD_HHMMSS.plist
```

## セットアップ・実行

```bash
# 依存ライブラリのインストール
python3 -m venv .venv
.venv/bin/pip install click rich watchdog psutil

# コマンドの実行（リポジトリルートから）
python -m bsync sync
python -m bsync watch

# テスト実行
.venv/bin/pip install pytest pytest-mock
PYTHONPATH=. .venv/bin/pytest tests/ -v
```

> パッケージインストール不要。`python -m bsync` で直接実行する方針。
> `pyproject.toml` は依存定義と pytest 設定のみに使用（`[build-system]` / `[project.scripts]` は削除済み）。

## CLI コマンド一覧

```
python -m bsync sync [--dry-run] [--strategy vivaldi|safari|newer]
python -m bsync watch [--interval 5]
python -m bsync status
python -m bsync backup
python -m bsync restore --browser vivaldi|safari --file PATH
python -m bsync install-agent
```

## 未実装・今後の課題

- Vivaldi の `checksum` 再計算（現在は `""` で書き込み）
- `pending.json` の CLI からの手動フラッシュコマンド
- Safari が起動中の場合の AppleScript による再読み込み通知
- `bsync restore` 後の Safari への変更反映通知
- GitHub Actions CI（`.github/workflows/test.yml` は作成済み、push 後に有効になる）
