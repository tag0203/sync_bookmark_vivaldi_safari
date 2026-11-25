from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .pending import PENDING_PATH
from .vivaldi import DEFAULT_PATH as VIVALDI_PATH, is_vivaldi_running
from .safari import DEFAULT_PATH as SAFARI_PATH

logger = logging.getLogger("bsync")


class DebounceTimer:
    def __init__(self, delay: float, callback: Callable) -> None:
        self.delay = delay
        self.callback = callback
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self.callback)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class _BookmarkEventHandler(FileSystemEventHandler):
    def __init__(self, target_path: Path, debounce_timer: DebounceTimer) -> None:
        self.target_path = target_path
        self.debounce_timer = debounce_timer

    def on_modified(self, event: FileModifiedEvent) -> None:
        if Path(event.src_path).resolve() == self.target_path.resolve():
            self.debounce_timer.reset()


class BookmarkWatcher:
    def __init__(
        self,
        interval: int = 5,
        strategy: str = "newer",
        vivaldi_folders: str | None = None,
        safari_folders: str | None = None,
    ) -> None:
        self.interval = interval
        self.strategy = strategy
        self.vivaldi_folders = vivaldi_folders
        self.safari_folders = safari_folders
        self._stop_event = threading.Event()

    def start(self) -> None:
        from rich.console import Console
        console = Console()
        console.print(f"[cyan]bsync watch[/cyan] 開始 (debounce: {self.interval}s)")

        def on_change() -> None:
            console.print("[yellow]変更を検出しました。同期を実行します…[/yellow]")
            try:
                from .cli import _run_sync
                _run_sync(
                    dry_run=False, strategy=self.strategy, console=console,
                    vivaldi_folders=self.vivaldi_folders, safari_folders=self.safari_folders,
                )
            except Exception as e:
                logger.error("同期中にエラーが発生しました: %s", e)
                console.print(f"[red]エラー: {e}[/red]")

        viv_timer = DebounceTimer(self.interval, on_change)
        saf_timer = DebounceTimer(self.interval, on_change)

        viv_handler = _BookmarkEventHandler(VIVALDI_PATH, viv_timer)
        saf_handler = _BookmarkEventHandler(SAFARI_PATH, saf_timer)

        observer = Observer()
        if VIVALDI_PATH.parent.exists():
            observer.schedule(viv_handler, str(VIVALDI_PATH.parent), recursive=False)
        if SAFARI_PATH.parent.exists():
            observer.schedule(saf_handler, str(SAFARI_PATH.parent), recursive=False)

        observer.start()

        def _shutdown(signum, frame) -> None:  # noqa: ANN001
            self._stop_event.set()

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        # 起動時にも、Vivaldi が終了済みなら既存のキューを処理する。
        vivaldi_was_running = True

        try:
            while not self._stop_event.is_set():
                vivaldi_running = is_vivaldi_running()
                if vivaldi_was_running and not vivaldi_running:
                    # Vivaldi が終了した → pending を書き込み
                    self._flush_pending(console)
                vivaldi_was_running = vivaldi_running
                time.sleep(2)
        finally:
            viv_timer.cancel()
            saf_timer.cancel()
            observer.stop()
            observer.join()
            console.print("[cyan]bsync watch[/cyan] 終了")

    def _flush_pending(self, console) -> None:  # noqa: ANN001
        if not PENDING_PATH.exists():
            return
        try:
            from .cli import _run_sync

            console.print("[green]pending キューの同期を再開します[/green]")
            _run_sync(
                dry_run=False, strategy=self.strategy, console=console,
                vivaldi_folders=self.vivaldi_folders, safari_folders=self.safari_folders,
            )
        except SystemExit as e:
            logger.error("pending の同期が中断しました (終了コード: %s)", e.code)
        except Exception as e:
            logger.error("pending フラッシュ中にエラー: %s", e)
            console.print(f"[red]pending フラッシュエラー: {e}[/red]")
