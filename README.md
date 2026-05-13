# bsync

macOS 上で **Vivaldi** と **Safari** のブックマークを双方向同期する CLI ツール。

Safari は iCloud 経由で iOS Safari と自動同期されるため、iPhone/iPad で追加したブックマークも Vivaldi へ反映できます。

---

## 機能

- **双方向同期**: Vivaldi → Safari、Safari → Vivaldi の両方向に対応
- **3ウェイマージ**: 前回同期スナップショットを使った差分検出で「追加/削除/変更/競合」を正確に判定
- **競合解決**: タイムスタンプ優先（デフォルト）/ Vivaldi 優先 / Safari 優先 を選択可能
- **watch モード**: FSEvents でファイル変更を監視して自動同期
- **Vivaldi 起動中対応**: 書き込みキュー（`~/.bsync/pending.json`）を使い、Vivaldi 終了後に適用
- **バックアップ/復元**: 同期前に自動バックアップ、手動での復元も可能
- **LaunchAgent 登録**: ログイン時に自動起動する macOS LaunchAgent を設定

---

## 必要要件

- macOS 12 以降
- Python 3.11 以降
- Safari のブックマークファイルへのアクセスに **フルディスクアクセス** 権限が必要

> **フルディスクアクセスの設定**  
> システム設定 → プライバシーとセキュリティ → フルディスクアクセス  
> で Terminal（またはお使いのターミナルアプリ）を追加してください。

---

## セットアップ

```bash
git clone https://github.com/tag0203/sync_bookmark_vivaldi_safari.git
cd sync_bookmark_vivaldi_safari

# 仮想環境を作成して依存ライブラリをインストール
python3 -m venv .venv
.venv/bin/pip install click rich watchdog psutil
```

---

## 使い方

リポジトリのルートディレクトリで `python -m bsync` を実行します。

```bash
python -m bsync --help
```

### 一回だけ同期

```bash
python -m bsync sync
```

差分のみ確認（書き込みなし）:

```bash
python -m bsync sync --dry-run
```

競合解決戦略を指定:

```bash
python -m bsync sync --strategy vivaldi   # 常に Vivaldi 優先
python -m bsync sync --strategy safari    # 常に Safari 優先
python -m bsync sync --strategy newer     # タイムスタンプが新しい方を採用（デフォルト）
```

### 現在の状態を確認

```bash
python -m bsync status
```

### ファイル変更を監視して自動同期（watch モード）

```bash
python -m bsync watch
python -m bsync watch --interval 10   # デバウンス秒数を変更（デフォルト: 5秒）
```

### バックアップ

```bash
python -m bsync backup
# → ~/.bsync/backups/vivaldi_YYYYMMDD_HHMMSS.json
# → ~/.bsync/backups/safari_YYYYMMDD_HHMMSS.plist
```

### 復元

```bash
python -m bsync restore --browser vivaldi --file ~/.bsync/backups/vivaldi_20260101_120000.json
python -m bsync restore --browser safari  --file ~/.bsync/backups/safari_20260101_120000.plist
```

### LaunchAgent として常駐（ログイン時に自動起動）

```bash
python -m bsync install-agent
```

---

## マージ戦略

前回同期時のスナップショット（`~/.bsync/last_sync.json`）を Base として3ウェイマージを行います。

| 状態 | 判定 | 対処 |
|---|---|---|
| Vivaldi のみに存在 | Vivaldi で追加 | Safari へ追加 |
| Safari のみに存在 | Safari で追加（iOS 含む） | Vivaldi へ追加 |
| Base のみに存在 | 両方で削除済み | 何もしない |
| Base + Vivaldi にあり Safari にない | Safari で削除 | Vivaldi からも削除 |
| Base + Safari にあり Vivaldi にない | Vivaldi で削除 | Safari からも削除 |
| 両方で変更・内容一致 | 同じ変更 | どちらかに統一 |
| 両方で変更・内容不一致 | **競合** | `--strategy` に従い解決 |

---

## データディレクトリ

```
~/.bsync/
├── last_sync.json      # 前回同期状態のスナップショット
├── pending.json        # Vivaldi 起動中に書き込めなかった変更のキュー
├── bsync.log           # 競合・エラーのログ
└── backups/
    ├── vivaldi_YYYYMMDD_HHMMSS.json
    └── safari_YYYYMMDD_HHMMSS.plist
```

---

## ブックマークファイルの仕様

| | Vivaldi | Safari |
|---|---|---|
| パス | `~/Library/Application Support/Vivaldi/Default/Bookmarks` | `~/Library/Safari/Bookmarks.plist` |
| 形式 | JSON (Chromium 標準) | バイナリ plist |
| タイムスタンプ起算 | 1601-01-01 (マイクロ秒) | 2001-01-01 (秒) |
| 起動中書き込み | キューに積む | 警告のみ |

---

## 開発・テスト

```bash
# テスト用依存ライブラリを追加インストール
.venv/bin/pip install pytest pytest-mock

# テスト実行（実際のブラウザファイルには触れません）
PYTHONPATH=. .venv/bin/pytest tests/ -v
```

### テスト構成

- `tests/test_vivaldi.py` — Vivaldi JSON 読み書き・タイムスタンプ変換（11ケース）
- `tests/test_safari.py` — Safari plist 読み書き・権限エラー処理（12ケース）
- `tests/test_merge.py` — 3ウェイマージ全ケース・競合解決（16ケース）

---

## ライセンス

MIT — 詳細は [LICENSE](LICENSE) を参照してください。
