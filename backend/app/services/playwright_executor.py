"""
Single-thread pool for **sync** Playwright (``handle_browser_opening`` / Siebel).

Playwright's sync driver binds to one OS thread (greenlet + internal asyncio loop).
``handle_browser_opening._get_playwright()`` discards the driver if ``threading.get_ident()``
changes, so starting sync Playwright from **different** worker threads ⇒ duplicate
``sync_playwright().start()`` and errors such as "Sync API inside the asyncio loop".

All automation must submit work to this executor. ``max_workers=1`` keeps one stable thread.
"""
from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_PLAYWRIGHT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pw_automation_")


def _shutdown_playwright_executor() -> None:
    try:
        _PLAYWRIGHT_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        _PLAYWRIGHT_EXECUTOR.shutdown(wait=False)


atexit.register(_shutdown_playwright_executor)


def get_playwright_executor() -> ThreadPoolExecutor:
    return _PLAYWRIGHT_EXECUTOR


def run_playwright_callable_sync(fn: Callable[[], T]) -> T:
    """
    Run ``fn`` on the Playwright worker thread and block until done.

    Use from **sync** code paths (e.g. bulk worker) so Playwright shares the same thread as
    ``await loop.run_in_executor(get_playwright_executor(), ...)`` from FastAPI.
    """
    return _PLAYWRIGHT_EXECUTOR.submit(fn).result()
