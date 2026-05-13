from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .merge import (
    MergeEngine,
    apply_additions_to_safari_tree,
    apply_additions_to_vivaldi_tree,
    apply_deletions_to_tree,
    apply_updates_to_tree,
)
from .models import MergeResult
from .safari import SafariPermissionError, SafariReader, SafariWriter, is_safari_running
from .snapshot import SnapshotManager
from .vivaldi import VivaldiReader, VivaldiWriter, is_vivaldi_running

BSYNC_DIR = Path("~/.bsync").expanduser()
BACKUP_DIR = BSYNC_DIR / "backups"
PENDING_PATH = BSYNC_DIR / "pending.json"
LOG_PATH = BSYNC_DIR / "bsync.log"

console = Console()


def _setup_logger() -> None:
    logger = logging.getLogger("bsync")
    if not logger.handlers:
        BSYNC_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_PATH)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)


def _ensure_data_dir() -> None:
    BSYNC_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(exist_ok=True)
    _setup_logger()


def _run_sync(
    dry_run: bool,
    strategy: str,
    console: Console,
    vivaldi_path: Path | None = None,
    safari_path: Path | None = None,
) -> MergeResult:
    from .vivaldi import DEFAULT_PATH as VIV_DEFAULT
    from .safari import DEFAULT_PATH as SAF_DEFAULT

    viv_path = vivaldi_path or VIV_DEFAULT
    saf_path = safari_path or SAF_DEFAULT

    # --- 読み込み ---
    try:
        viv_reader = VivaldiReader(viv_path)
        viv_tree, viv_original = viv_reader.read()
    except FileNotFoundError:
        console.print(f"[red]Vivaldi ブックマークファイルが見つかりません: {viv_path}[/red]")
        sys.exit(1)

    try:
        saf_reader = SafariReader(saf_path)
        saf_tree, saf_original = saf_reader.read()
    except SafariPermissionError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)
    except FileNotFoundError:
        console.print(f"[red]Safari ブックマークファイルが見つかりません: {saf_path}[/red]")
        sys.exit(1)

    viv_flat = viv_reader.flatten(viv_tree)
    saf_flat = saf_reader.flatten(saf_tree)

    # --- スナップショット読み込み ---
    snapshot_mgr = SnapshotManager()
    base = snapshot_mgr.load()

    # --- マージ ---
    engine = MergeEngine(strategy=strategy)
    result = engine.merge(base, viv_flat, saf_flat)

    # --- 表示 ---
    _print_result(result, dry_run, console)

    if dry_run:
        return result

    # --- Vivaldi が起動中かチェック ---
    viv_running = is_vivaldi_running()
    saf_running = is_safari_running()

    if saf_running:
        console.print("[yellow]警告: Safari が起動中です。書き込み後に Safari の再起動が必要な場合があります。[/yellow]")

    # --- Safari へ書き込み ---
    if result.to_add_safari or result.to_delete_safari or result.to_update_safari:
        apply_additions_to_safari_tree(saf_tree, result.to_add_safari)
        apply_deletions_to_tree(saf_tree, result.to_delete_safari)
        apply_updates_to_tree(saf_tree, result.to_update_safari)
        saf_writer = SafariWriter(saf_path, backup_dir=BACKUP_DIR)
        try:
            saf_writer.write(saf_tree, saf_original)
            console.print("[green]Safari ブックマークを更新しました。[/green]")
        except PermissionError as e:
            console.print(f"[red]Safari 書き込みエラー: {e}[/red]")

    # --- Vivaldi へ書き込み ---
    if result.to_add_vivaldi or result.to_delete_vivaldi or result.to_update_vivaldi:
        apply_additions_to_vivaldi_tree(viv_tree, result.to_add_vivaldi)
        apply_deletions_to_tree(viv_tree, result.to_delete_vivaldi)
        apply_updates_to_tree(viv_tree, result.to_update_vivaldi)

        if viv_running:
            console.print("[yellow]Vivaldi が起動中のため、変更を pending.json にキューします。[/yellow]")
            _save_pending(result)
        else:
            viv_writer = VivaldiWriter(viv_path, backup_dir=BACKUP_DIR)
            try:
                viv_writer.write(viv_tree, viv_original)
                console.print("[green]Vivaldi ブックマークを更新しました。[/green]")
            except Exception as e:
                console.print(f"[red]Vivaldi 書き込みエラー: {e}[/red]")

    # --- スナップショット保存 ---
    # 書き込み後の最新状態で再 flatten
    viv_flat_new = viv_reader.flatten(viv_tree)
    saf_flat_new = saf_reader.flatten(saf_tree)
    snapshot_mgr.save(viv_flat_new, saf_flat_new, result)

    # --- 競合ログ ---
    if result.conflicts:
        logger = logging.getLogger("bsync")
        for c in result.conflicts:
            logger.warning(
                "競合 URL=%s vivaldi=%r safari=%r → %s (%s)",
                c.url, c.vivaldi_title, c.safari_title, c.resolved_title, c.resolution,
            )

    return result


def _print_result(result: MergeResult, dry_run: bool, console: Console) -> None:
    prefix = "[dim]dry-run[/dim] " if dry_run else ""

    table = Table(title=f"{prefix}同期結果サマリー")
    table.add_column("操作", style="bold")
    table.add_column("Vivaldi", justify="right")
    table.add_column("Safari", justify="right")
    table.add_row("追加", str(len(result.to_add_vivaldi)), str(len(result.to_add_safari)))
    table.add_row("削除", str(len(result.to_delete_vivaldi)), str(len(result.to_delete_safari)))
    table.add_row("更新", str(len(result.to_update_vivaldi)), str(len(result.to_update_safari)))
    console.print(table)

    if result.conflicts:
        console.print(f"[yellow]⚠ {len(result.conflicts)} 件の競合が発生しました[/yellow]")
        for c in result.conflicts:
            console.print(
                f"  [yellow]競合[/yellow] {c.url}\n"
                f"    Vivaldi: {c.vivaldi_title!r}  Safari: {c.safari_title!r}\n"
                f"    → {c.resolved_title!r} ({c.resolution})"
            )

    for bm in result.to_add_vivaldi:
        console.print(f"  [green]+[/green] Vivaldi追加: {bm.title!r}  {bm.url}")
    for bm in result.to_add_safari:
        console.print(f"  [green]+[/green] Safari追加:  {bm.title!r}  {bm.url}")
    for url in result.to_delete_vivaldi:
        console.print(f"  [red]-[/red] Vivaldi削除: {url}")
    for url in result.to_delete_safari:
        console.print(f"  [red]-[/red] Safari削除:  {url}")
    for bm in result.to_update_vivaldi:
        console.print(f"  [blue]~[/blue] Vivaldi更新: {bm.title!r}  {bm.url}")
    for bm in result.to_update_safari:
        console.print(f"  [blue]~[/blue] Safari更新:  {bm.title!r}  {bm.url}")


def _save_pending(result: MergeResult) -> None:
    import json
    pending = {
        "to_add_vivaldi": [
            {"title": bm.title, "url": bm.url, "guid": bm.guid,
             "date_added": bm.date_added.isoformat(),
             "date_modified": bm.date_modified.isoformat(),
             "folder_path": bm.folder_path}
            for bm in result.to_add_vivaldi
        ],
        "to_delete_vivaldi": result.to_delete_vivaldi,
        "to_update_vivaldi": [
            {"title": bm.title, "url": bm.url, "guid": bm.guid,
             "date_added": bm.date_added.isoformat(),
             "date_modified": bm.date_modified.isoformat(),
             "folder_path": bm.folder_path}
            for bm in result.to_update_vivaldi
        ],
    }
    BSYNC_DIR.mkdir(parents=True, exist_ok=True)
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


@click.group()
@click.version_option(__version__, prog_name="bsync")
def main() -> None:
    """Vivaldi/Safari ブックマーク双方向同期ツール。"""


@main.command()
@click.option("--dry-run", is_flag=True, help="書き込みを行わずに差分のみ表示する")
@click.option(
    "--strategy",
    type=click.Choice(["vivaldi", "safari", "newer"]),
    default="newer",
    show_default=True,
    help="競合解決戦略",
)
def sync(dry_run: bool, strategy: str) -> None:
    """ブックマークを双方向同期する。"""
    _ensure_data_dir()
    _run_sync(dry_run=dry_run, strategy=strategy, console=console)


@main.command()
@click.option("--interval", default=5, show_default=True, type=int, help="デバウンス秒数")
@click.option(
    "--strategy",
    type=click.Choice(["vivaldi", "safari", "newer"]),
    default="newer",
    show_default=True,
    help="競合解決戦略",
)
def watch(interval: int, strategy: str) -> None:
    """ファイル変更を監視して自動同期する。"""
    _ensure_data_dir()
    from .watcher import BookmarkWatcher
    BookmarkWatcher(interval=interval, strategy=strategy).start()


@main.command()
def status() -> None:
    """現在の同期状態を表示する。"""
    _ensure_data_dir()
    from .vivaldi import DEFAULT_PATH as VIV_PATH
    from .safari import DEFAULT_PATH as SAF_PATH
    from .snapshot import SNAPSHOT_PATH

    table = Table(title="bsync 状態")
    table.add_column("項目", style="bold")
    table.add_column("値")

    # ファイルの存在と更新時刻
    for label, path in [("Vivaldi ブックマーク", VIV_PATH), ("Safari ブックマーク", SAF_PATH)]:
        if path.exists():
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            table.add_row(label, f"[green]存在[/green]  最終更新: {mtime}")
        else:
            table.add_row(label, "[red]ファイルなし[/red]")

    # スナップショット
    if SNAPSHOT_PATH.exists():
        snap_mtime = datetime.fromtimestamp(SNAPSHOT_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        table.add_row("最終同期", snap_mtime)
    else:
        table.add_row("最終同期", "[dim]未同期[/dim]")

    # プロセス状態
    table.add_row("Vivaldi プロセス", "[green]起動中[/green]" if is_vivaldi_running() else "[dim]停止[/dim]")
    table.add_row("Safari プロセス", "[green]起動中[/green]" if is_safari_running() else "[dim]停止[/dim]")

    # pending
    if PENDING_PATH.exists():
        import json
        try:
            with open(PENDING_PATH, encoding="utf-8") as f:
                pending = json.load(f)
            n_add = len(pending.get("to_add_vivaldi", []))
            n_del = len(pending.get("to_delete_vivaldi", []))
            n_upd = len(pending.get("to_update_vivaldi", []))
            table.add_row("Pending (Vivaldi未書込)", f"追加:{n_add} 削除:{n_del} 更新:{n_upd}")
        except Exception:
            table.add_row("Pending", "[yellow]読み込みエラー[/yellow]")
    else:
        table.add_row("Pending", "[dim]なし[/dim]")

    console.print(table)


@main.command()
def backup() -> None:
    """両ブラウザのブックマークを手動バックアップする。"""
    _ensure_data_dir()
    from .vivaldi import DEFAULT_PATH as VIV_PATH
    from .safari import DEFAULT_PATH as SAF_PATH

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if VIV_PATH.exists():
        dst = BACKUP_DIR / f"vivaldi_{ts}.json"
        shutil.copy2(VIV_PATH, dst)
        console.print(f"[green]Vivaldi バックアップ:[/green] {dst}")
    else:
        console.print(f"[yellow]Vivaldi ブックマークが見つかりません: {VIV_PATH}[/yellow]")

    if SAF_PATH.exists():
        dst = BACKUP_DIR / f"safari_{ts}.plist"
        shutil.copy2(SAF_PATH, dst)
        console.print(f"[green]Safari バックアップ:[/green] {dst}")
    else:
        console.print(f"[yellow]Safari ブックマークが見つかりません: {SAF_PATH}[/yellow]")


@main.command()
@click.option(
    "--browser",
    type=click.Choice(["vivaldi", "safari"]),
    required=True,
    help="復元対象のブラウザ",
)
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True),
    required=True,
    help="復元するバックアップファイルのパス",
)
def restore(browser: str, file_path: str) -> None:
    """バックアップからブックマークを復元する。"""
    _ensure_data_dir()
    from .vivaldi import DEFAULT_PATH as VIV_PATH
    from .safari import DEFAULT_PATH as SAF_PATH

    src = Path(file_path)
    if browser == "vivaldi":
        dst = VIV_PATH
    else:
        dst = SAF_PATH

    if is_vivaldi_running() and browser == "vivaldi":
        console.print("[red]Vivaldi が起動中です。終了してから復元してください。[/red]")
        sys.exit(1)
    if is_safari_running() and browser == "safari":
        console.print("[yellow]Safari が起動中です。復元後に再起動が必要な場合があります。[/yellow]")

    # 復元前に現在ファイルをバックアップ
    if dst.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_backup = BACKUP_DIR / f"{browser}_pre_restore_{ts}{src.suffix}"
        shutil.copy2(dst, pre_backup)
        console.print(f"[dim]復元前バックアップ: {pre_backup}[/dim]")

    shutil.copy2(src, dst)
    console.print(f"[green]{browser} を復元しました: {dst}[/green]")


@main.command("install-agent")
def install_agent() -> None:
    """launchd エージェントをインストールして自動起動を設定する。"""
    _ensure_data_dir()
    bsync_bin = shutil.which("bsync")
    if not bsync_bin:
        console.print("[red]bsync の実行ファイルが見つかりません。pip install bsync を確認してください。[/red]")
        sys.exit(1)

    plist_path = Path("~/Library/LaunchAgents/com.user.bsync.plist").expanduser()
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.bsync</string>
    <key>ProgramArguments</key>
    <array>
        <string>{bsync_bin}</string>
        <string>watch</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>{LOG_PATH}</string>
</dict>
</plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content, encoding="utf-8")
    console.print(f"[green]LaunchAgent を作成しました: {plist_path}[/green]")

    try:
        subprocess.run(
            ["launchctl", "load", str(plist_path)],
            check=True,
            capture_output=True,
        )
        console.print("[green]launchctl load 完了。ログイン時に bsync watch が自動起動します。[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]launchctl load に失敗しました: {e.stderr.decode()}[/yellow]")
        console.print(f"手動で実行: launchctl load {plist_path}")
