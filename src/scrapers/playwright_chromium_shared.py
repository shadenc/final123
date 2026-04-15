"""
Shared Chromium launch/teardown for Playwright scrapers.

Bundled Chromium can SIGSEGV on some macOS + GPU combinations. Mitigations:
- Minimal launch flags (avoid duplicate --disable-features, VizDisplayCompositor, --disable-web-security).
- Optional PLAYWRIGHT_CHANNEL=chrome to use installed Google Chrome instead of bundled Chromium.
"""
from __future__ import annotations

import os
from typing import Any, Dict


def chromium_launch_options(headless: bool) -> Dict[str, Any]:
    """Build kwargs for playwright.chromium.launch(...)."""
    opts: Dict[str, Any] = {"headless": headless}
    channel = os.environ.get("PLAYWRIGHT_CHANNEL", "").strip()
    if channel:
        opts["channel"] = channel
    # Keep extras minimal; heavy flags increase crash risk on macOS headless Chromium.
    opts["args"] = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]
    return opts


def is_browser_closed_error(exc: BaseException) -> bool:
    """True when Playwright lost the browser (crash, SIGSEGV, user closed window)."""
    if type(exc).__name__ == "TargetClosedError":
        return True
    msg = str(exc).lower()
    return "has been closed" in msg or ("target page" in msg and "closed" in msg)


async def teardown_playwright_bundle(playwright: Any, browser: Any) -> None:
    if browser is not None:
        try:
            if getattr(browser, "is_connected", lambda: False)():
                await browser.close()
        except Exception:
            pass
    if playwright is not None:
        try:
            await playwright.stop()
        except Exception:
            pass
