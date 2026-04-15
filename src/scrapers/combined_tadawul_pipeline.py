#!/usr/bin/env python3
"""
Single browser visit per company: quarterly net profit (Financial Information) then PDF downloads
(Financial Statements) — no second search/Visit Profile for the same symbol.

Run from project root: python src/scrapers/combined_tadawul_pipeline.py
"""

from __future__ import annotations

import asyncio
import json

import aiofiles
import os
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from playwright.async_api import Browser, BrowserContext, Page

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from playwright_chromium_shared import teardown_playwright_bundle

from hybrid_financial_downloader import (
    DEFAULT_STOP_PDFS_FLAG,
    DEFAULT_PDFS_PROGRESS_FILE,
    download_pdf_with_stealth,
    get_all_financial_reports,
    get_company_symbols_from_json,
    navigate_to_company_profile,
    setup_stealth_browser,
)
from scrape_quarterly_net_profit import (
    OUTPUT_FILE as NET_PROFIT_OUTPUT,
    navigate_to_financial_information,
    scrape_quarterly_net_profit,
)


def _stop_requested() -> bool:
    pdf = Path(os.environ.get("STOP_FLAG_FILE", DEFAULT_STOP_PDFS_FLAG))
    net = Path(os.environ.get("STOP_FLAG_FILE_NET", "data/runtime/stop_net_profit.flag"))
    return pdf.exists() or net.exists()


def _write_dual_progress(
    pdfs_path: Path,
    net_path: Path,
    *,
    status: str,
    processed: int,
    success: int,
    failed: int,
    current_symbol: str = "",
) -> None:
    payload = {
        "status": status,
        "processed": processed,
        "success": success,
        "failed": failed,
        "current_symbol": current_symbol,
        "mode": "combined",
    }
    try:
        pdfs_path.parent.mkdir(parents=True, exist_ok=True)
        net_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pdfs_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        with open(net_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def _net_profit_has_data(net_data: Optional[Dict]) -> bool:
    return bool(net_data and net_data.get("quarterly_net_profit"))


async def _download_report_pdfs_for_company(
    page: Page, symbol: str, reports: List[Tuple[str, int, str]]
) -> bool:
    all_ok = True
    stop_pdf = Path(os.environ.get("STOP_FLAG_FILE", DEFAULT_STOP_PDFS_FLAG))
    for stype, year, pdf_url in reports:
        if stop_pdf.exists():
            print(" Stop requested. Halting PDF downloads for this company.")
            all_ok = False
            break
        dl_ok = await download_pdf_with_stealth(page, pdf_url, symbol, year, stype)
        if not dl_ok:
            all_ok = False
    return all_ok


async def process_company_single_visit(
    context: BrowserContext,
    browser: Browser,
    symbol: str,
) -> Tuple[bool, Optional[Dict]]:
    """
    One search → profile → net profit tab → scrape → Financial Statements tab → PDFs.
    """
    page: Optional[Page] = None
    try:
        if not browser.is_connected():
            return False, None
        if _stop_requested():
            return False, None

        page = await context.new_page()
        await page.mouse.move(random.randint(100, 500), random.randint(100, 300))
        await asyncio.sleep(random.uniform(0.5, 1.5))

        if not await navigate_to_company_profile(page, symbol):
            return False, None

        net_data: Optional[Dict] = None
        if await navigate_to_financial_information(page, symbol):
            net_data = await scrape_quarterly_net_profit(page, symbol)
        else:
            print(f"  {symbol}: Financial Information navigation failed; continuing to PDFs only.")

        if _stop_requested():
            return False, net_data

        reports = await get_all_financial_reports(page, symbol, already_on_profile=True)
        if not reports:
            print(f"  {symbol}: No PDF reports matched filter.")
            return _net_profit_has_data(net_data), net_data

        all_ok = await _download_report_pdfs_for_company(page, symbol, reports)
        useful_np = _net_profit_has_data(net_data)
        return (all_ok or useful_np), net_data
    except Exception as e:
        print(f" Combined processing error for {symbol}: {e}")
        return False, None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass


def _merge_net_profit_file(symbol: str, new_data: Dict) -> None:
    existing_list: List = []
    if NET_PROFIT_OUTPUT.exists():
        try:
            with open(NET_PROFIT_OUTPUT, "r", encoding="utf-8") as f:
                existing_list = json.load(f)
        except Exception:
            existing_list = []
    found = False
    for i, row in enumerate(existing_list):
        if str(row.get("company_symbol", "")).strip() == str(symbol):
            merged = {**row, **new_data}
            merged["quarterly_net_profit"] = {
                **(row.get("quarterly_net_profit") or {}),
                **(new_data.get("quarterly_net_profit") or {}),
            }
            existing_list[i] = merged
            found = True
            break
    if not found:
        existing_list.append(new_data)
    NET_PROFIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(NET_PROFIT_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(existing_list, f, indent=2, ensure_ascii=False)


async def _ensure_connected_browser(
    playwright, browser: Browser, context: BrowserContext
):
    if browser.is_connected():
        return playwright, browser, context
    print(" Relaunching browser...")
    await teardown_playwright_bundle(playwright, browser)
    return await setup_stealth_browser()


async def _silent_relaunch_if_disconnected(
    playwright,
    browser: Browser,
    context: BrowserContext,
):
    """After a company run: relaunch browser if it died (no extra console line)."""
    if browser.is_connected():
        return playwright, browser, context
    await teardown_playwright_bundle(playwright, browser)
    return await setup_stealth_browser()


def _limit_companies_hit(iteration_index: int):
    """Return limit N if iteration_index >= N and LIMIT_COMPANIES>0, else None."""
    try:
        limit = int(os.environ.get("LIMIT_COMPANIES", "0"))
    except Exception:
        return None
    if limit and iteration_index >= limit:
        return limit
    return None


async def _write_combined_done_files(
    pdfs_progress: Path, net_progress: Path, payload: dict
) -> None:
    done_json = json.dumps(payload, ensure_ascii=False)
    for p in (pdfs_progress, net_progress):
        try:
            async with aiofiles.open(p, "w", encoding="utf-8") as f:
                await f.write(done_json)
        except Exception:
            pass


async def _delay_before_next_combined_company(i: int, total: int) -> None:
    if i < total and not _stop_requested():
        delay = random.uniform(3, 7)
        print(f" Waiting {delay:.1f}s before next company...")
        await asyncio.sleep(delay)


async def run_combined_pipeline() -> None:
    companies = get_company_symbols_from_json()
    if not companies:
        print(" No company symbols found.")
        return

    pdfs_progress = Path(os.environ.get("PROGRESS_FILE", DEFAULT_PDFS_PROGRESS_FILE))
    net_progress = Path(os.environ.get("PROGRESS_FILE_NET", "data/runtime/net_profit_progress.json"))

    print(f" Combined pipeline: {len(companies)} companies (one search + profile visit each)")
    print("   Order: net profit first, then financial statement PDFs (same browser session)")

    playwright, browser, context = await setup_stealth_browser()
    processed = 0
    success = 0
    failed = 0

    try:
        _write_dual_progress(
            pdfs_progress, net_progress, status="running", processed=0, success=0, failed=0
        )

        for i, symbol in enumerate(companies, 1):
            if _stop_requested():
                print(" Stop requested. Ending combined pipeline.")
                break

            print(f"\n{'='*50}\n [{i}/{len(companies)}] {symbol} (single visit)\n{'='*50}")

            playwright, browser, context = await _ensure_connected_browser(
                playwright, browser, context
            )

            ok, net_data = await process_company_single_visit(context, browser, symbol)

            if net_data and net_data.get("quarterly_net_profit"):
                _merge_net_profit_file(symbol, net_data)
                print(f" Net profit merged for {symbol}")

            processed += 1
            if ok:
                success += 1
            else:
                failed += 1

            _write_dual_progress(
                pdfs_progress,
                net_progress,
                status="running",
                processed=processed,
                success=success,
                failed=failed,
                current_symbol=symbol,
            )

            playwright, browser, context = await _silent_relaunch_if_disconnected(
                playwright, browser, context
            )

            lim = _limit_companies_hit(i)
            if lim is not None:
                print(f"\n Stopping after {lim} companies")
                break

            await _delay_before_next_combined_company(i, len(companies))

        await _write_combined_done_files(
            pdfs_progress,
            net_progress,
            {
                "status": "completed",
                "processed": processed,
                "success": success,
                "failed": failed,
                "mode": "combined",
            },
        )

        print(f"\n Combined pipeline finished: ok~{success} failed~{failed}")
    finally:
        await teardown_playwright_bundle(playwright, browser)


if __name__ == "__main__":
    asyncio.run(run_combined_pipeline())
