import asyncio
import os

import aiofiles
import re
import json
import sys
from datetime import datetime
from pathlib import Path
from random import SystemRandom
from typing import Optional, Tuple, List

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from src.config.reporting_period import reporting_fiscal_year

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from playwright_chromium_shared import (
    chromium_launch_options,
    is_browser_closed_error,
    teardown_playwright_bundle,
)

# Constants
BASE_URL = "https://www.saudiexchange.sa/wps/portal/saudiexchange/companies/company-profile-main/"
PDF_DIR = Path("data/pdfs")
PDF_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_INPUT_SELECTOR = "#query-input"
DEFAULT_STOP_PDFS_FLAG = "data/runtime/stop_pdfs_pipeline.flag"
DEFAULT_PDFS_PROGRESS_FILE = "data/runtime/pdfs_progress.json"

# OS-backed RNG for human-like mouse jitter and delays (not for secrets/tokens).
_HUMANIZE_RNG = SystemRandom()

# Statement type priorities (most preferred first)
STATEMENT_PRIORITIES = [
    "annual",
    "quarterly", 
    "interim",
    "financial",
    "report"
]

# Fiscal focus: Q1–Q3 under Tadawul column `target_year` + Annual under column `target_year − 1`.
# Default column year matches reporting_period (Jan–Apr → prior calendar year). Override: REPORTING_FISCAL_YEAR=2026.
#
# IMPORTANT — local filenames vs PDF period titles:
# Saved names are `{symbol}_{q1|q2|q3|annual}_{year}.pdf` where `year` is the **year shown in Tadawul’s
# table header for that cell**, not the “period ended …” date inside the PDF. The exchange may label a
# column “2025” while the report covers e.g. three months ended March 2024, or the PDF filename on the
# server (see date in /Resources/fsPdf/…_YYYY-MM-DD_…) reflects **publication** date. Do not assume the
# four-digit suffix equals calendar period-end inside the document.
try:
    target_year = int(os.environ.get("REPORTING_FISCAL_YEAR", str(reporting_fiscal_year())))
except ValueError:
    target_year = reporting_fiscal_year()


def _env_headless() -> bool:
    """Default headless=True; set PLAYWRIGHT_HEADLESS=0 for a visible browser (debug)."""
    v = os.environ.get("PLAYWRIGHT_HEADLESS", "1").strip().lower()
    return v not in ("0", "false", "no")


async def _safe_close_page(page: Optional[Page]) -> None:
    if page is None:
        return
    try:
        await page.close()
    except Exception:
        pass


def get_company_symbols_from_json():
    """Get company symbols from the existing JSON file."""
    try:
        json_path = Path("frontend/public/foreign_ownership_data.json")
        if not json_path.exists():
            print(f" JSON file not found: {json_path}")
            return []
        
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        symbols = [item['symbol'] for item in data if item.get('symbol')]
        # Optional limit for testing
        try:
            limit = int(os.environ.get("LIMIT_COMPANIES", "0"))
            if limit > 0:
                symbols = symbols[:limit]
        except Exception:
            pass
        print(f" Found {len(symbols)} company symbols from JSON file")
        return symbols
        
    except Exception as e:
        print(f" Error reading JSON file: {e}")
        return []

async def setup_stealth_browser():
    """Setup Playwright browser with stealth configuration from download_pdf_playwright.py."""
    playwright = await async_playwright().start()

    ch = os.environ.get("PLAYWRIGHT_CHANNEL", "").strip()
    if ch:
        print(f" Using Chromium channel: {ch} (set PLAYWRIGHT_CHANNEL to use system Chrome, etc.)")

    browser = await playwright.chromium.launch(**chromium_launch_options(_env_headless()))
    
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        accept_downloads=True,
        locale='en-US',
        timezone_id='America/New_York',
        permissions=['geolocation'],
        extra_http_headers={
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        }
    )
    
    # Add stealth scripts from download_pdf_playwright.py
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
        
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
        });
        
        window.chrome = {
            runtime: {},
        };
        
        Object.defineProperty(navigator, 'permissions', {
            get: () => ({
                query: () => Promise.resolve({ state: 'granted' }),
            }),
        });
    """)
    
    return playwright, browser, context

async def navigate_to_company_profile(page: Page, symbol: str) -> bool:
    """Navigate to the company profile page using the working approach from download_annual_reports.py."""
    search_url = "https://www.saudiexchange.sa/wps/portal/saudiexchange/hidden/search/!ut/p/z0/04_Sj9CPykssy0xPLMnMz0vMAfIjo8ziTR3NDIw8LAz8DTxCnA3MDILdzUJDLAyNHI30C7IdFQEEx_vC/"
    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
    print(f"Navigated to search page for symbol {symbol}")
    try:
        # Focus the input, fill the symbol, and submit search
        await page.wait_for_selector(SEARCH_INPUT_SELECTOR, timeout=5000)
        await page.click(SEARCH_INPUT_SELECTOR)
        await page.fill(SEARCH_INPUT_SELECTOR, symbol)
        await page.wait_for_timeout(500)
        # Use only JS click to submit
        await page.evaluate("document.querySelector('div.srchBlueBtn').click()")
        await page.wait_for_timeout(2000)
        links = await page.query_selector_all("a.pageLink")
        if os.environ.get("DEBUG_PDF_NAV"):
            print("--- <a.pageLink> elements on the page ---")
            for i, link in enumerate(links):
                text = (await link.text_content() or "").strip()
                href = await link.get_attribute("href")
                print(f'{i}: text="{text}", href="{href}"')
            print("--- end of <a.pageLink> debug ---")
        # Find and click the 'Visit Profile' button by text
        visit_links = []
        for link in links:
            text = (await link.text_content() or "").strip().lower()
            if text == "visit profile":
                visit_links.append(link)
        if not visit_links:
            print(f" No 'Visit Profile' link found for symbol {symbol}")
            return False
        await visit_links[0].click()
        await page.wait_for_load_state('domcontentloaded')
        print(f" Clicked 'Visit Profile' for symbol {symbol}")
        return True
    except Exception as e:
        print(f" Search failed for {symbol}: {e}")
        return False

_FINANCIAL_PDF_EXTRACT_JS = """
() => {
  const norm = (s) => (s || "").trim().toLowerCase().replace(/\\s+/g, " ");
  /**
   * Map each header cell index to its 4-digit year (DOM order — e.g. 2025,2024,… left-to-right).
   * Do not sort years: column i under "2025" must pair with tbody td[i], not sorted order.
   *
   * Tadawul often uses TWO thead rows (section titles, then years/dates). Using only the first
   * thead row yields no years → we wrongly fell back to tbody row 1 and mis-aligned columns → wrong PDF year.
   * Pick the thead row that contains the most year-like tokens (usually the real sub-header row).
   */
  function extractYearColumns(table) {
    function fromRow(row) {
      if (!row) return [];
      const cells = row.querySelectorAll("th, td");
      const out = [];
      for (let i = 0; i < cells.length; i++) {
        const t = cells[i].innerText || "";
        const m = t.match(/\\b(20\\d{2})\\b/);
        if (m) out.push({ year: parseInt(m[1], 10), col: i });
      }
      return out;
    }
    let best = [];
    const thead = table.querySelector("thead");
    if (thead) {
      for (const row of thead.querySelectorAll("tr")) {
        const c = fromRow(row);
        if (c.length > best.length) best = c;
      }
    }
    if (best.length) return best;
    return fromRow(table.querySelector("tbody tr"));
  }

  /** PDF link: href may be relative, query-param, or mixed case — not always href$=.pdf */
  function hrefFromTd(td) {
    if (!td) return null;
    for (const a of td.querySelectorAll("a[href]")) {
      const h = (a.getAttribute("href") || "").trim();
      if (!h) continue;
      const low = h.toLowerCase();
      if (low.includes(".pdf") || low.includes("pdf") && low.includes("content")) return h;
    }
    return null;
  }

  const matchers = [
    {
      st: "annual",
      test: (l) =>
        /\\bannual\\b/i.test(l) ||
        /سنوي|التقرير\\s*السنوي|القوائم\\s*المالية\\s*السنوية|قوائم\\s*سنوية/.test(l),
    },
    {
      st: "q1",
      test: (l) =>
        /\\bq[\\s-]*1\\b/i.test(l) ||
        /first\\s+quarter|quarter\\s*1/i.test(l) ||
        /الربع\\s*الأول|ربع\\s*1|الفترة\\s*الأولى/.test(l),
    },
    {
      st: "q2",
      test: (l) =>
        /\\bq[\\s-]*2\\b/i.test(l) ||
        /second\\s+quarter/i.test(l) ||
        /الربع\\s*الثاني|ربع\\s*2/.test(l),
    },
    {
      st: "q3",
      test: (l) =>
        /\\bq[\\s-]*3\\b/i.test(l) ||
        /third\\s+quarter/i.test(l) ||
        /الربع\\s*الثالث|ربع\\s*3/.test(l),
    },
    {
      st: "q4",
      test: (l) =>
        /\\bq[\\s-]*4\\b/i.test(l) ||
        /fourth\\s+quarter/i.test(l) ||
        /الربع\\s*الرابع|ربع\\s*4/.test(l),
    },
  ];

  const tables = Array.from(document.querySelectorAll("table"));
  const found = [];

  for (const table of tables) {
    const yearCols = extractYearColumns(table);
    if (!yearCols.length) continue;
    const html = (table.innerHTML || "").toLowerCase();
    if (!html.includes(".pdf") && !html.includes("pdf")) continue;

    const bodyRows = Array.from(table.querySelectorAll("tbody tr"));
    if (!bodyRows.length) continue;

    for (const { st, test } of matchers) {
      for (const row of bodyRows) {
        const tds = row.querySelectorAll("td");
        if (tds.length < 2) continue;
        const lab = norm(tds[0].textContent || "");
        if (!lab) continue;
        if (!test(lab)) continue;
        yearCols.forEach(({ year, col }) => {
          const td = tds[col];
          if (!td) return;
          const href = hrefFromTd(td);
          if (href) found.push([st, year, href]);
        });
        break;
      }
    }
    if (found.length) break;
  }
  return found;
}
"""

_FETCH_PDF_BYTES_JS = """
async () => {
    try {
        const response = await fetch(window.location.href);
        const arrayBuffer = await response.arrayBuffer();
        return Array.from(new Uint8Array(arrayBuffer));
    } catch (error) {
        console.error('Error fetching PDF:', error);
        return null;
    }
}
"""


async def _click_financial_statements_tab(page: Page) -> bool:
    tabs = await page.query_selector_all("li")
    try:
        target_text = "financial statements and reports"
        for tab in tabs:
            tab_text = (await tab.text_content() or "").strip().lower()
            if target_text in tab_text:
                await tab.scroll_into_view_if_needed()
                await tab.click()
                print(f" Clicked tab: {tab_text}")
                return True
        print(" 'Financial Statements and Reports' tab not found by substring.")
        return False
    except PlaywrightTimeoutError:
        print(" Timeout while trying to find financial tab.")
        return False


async def _wait_financial_statements_table(page: Page) -> bool:
    try:
        await page.wait_for_selector("table", timeout=10000)
        print("Table found, waiting for content to load...")
        await page.wait_for_timeout(2000)
        return True
    except Exception as e:
        print(f"Could not find financial statements table: {e}")
        return False


def _parse_js_report_tuples(raw, symbol: str) -> List[Tuple[str, int, str]]:
    found_reports: List[Tuple[str, int, str]] = []
    for item in raw or []:
        if len(item) != 3:
            continue
        st, yr, href = item[0], int(item[1]), item[2]
        found_reports.append((str(st).lower().strip(), yr, str(href)))
        print(f" Found {st.upper()} PDF URL for {symbol} {yr}: {href}")
    return found_reports


async def _maybe_debug_financial_tables(page: Page) -> None:
    if not os.environ.get("DEBUG_PDF_TABLE"):
        return
    snippet = await page.evaluate(
        "() => Array.from(document.querySelectorAll('table')).map(t => (t.innerText || '').slice(0, 400))"
    )
    print(f"[DEBUG_PDF_TABLE] table text snippets: {repr(snippet)[:2000]}")


def _filter_reports_for_fiscal_year(
    found_reports: List[Tuple[str, int, str]], symbol: str
) -> List[Tuple[str, int, str]]:
    filtered: List[Tuple[str, int, str]] = []
    for stype, year, pdf_url in found_reports:
        if (year == target_year and stype in ["q1", "q2", "q3"]) or (
            year == target_year - 1 and stype == "annual"
        ):
            filtered.append((stype, year, pdf_url))
    print(
        f"[DEBUG] Will download for {symbol}: "
        f"{[f'{stype}_{year}' for stype, year, _ in filtered]}"
    )
    return filtered


async def _fetch_filtered_financial_reports_from_statements_tab(page: Page, symbol: str) -> List[Tuple[str, int, str]]:
    """Open Financial Statements and Reports tab and return filtered (stype, year, url) tuples."""
    if not await _click_financial_statements_tab(page):
        return []
    if not await _wait_financial_statements_table(page):
        return []

    raw = await page.evaluate(_FINANCIAL_PDF_EXTRACT_JS)
    found_reports = _parse_js_report_tuples(raw, symbol)

    if not found_reports:
        print(
            " No Annual/Q PDF rows with .pdf links found (wrong table or layout changed). "
            "Tip: run with DEBUG_PDF_TABLE=1 for table text snippets."
        )
        await _maybe_debug_financial_tables(page)

    return _filter_reports_for_fiscal_year(found_reports, symbol)


async def get_all_financial_reports(page: Page, symbol: str, already_on_profile: bool = False):
    """
    Find financial report PDFs filtered for target_year / annual.

    If already_on_profile is True, skip search + Visit Profile (caller already landed on the company
    profile — e.g. after scraping net profit in the same tab session).
    """
    if not already_on_profile:
        if not await navigate_to_company_profile(page, symbol):
            return []
        print("On company profile page, waiting for content...")
        await page.wait_for_timeout(3000)
    else:
        print(" Same session: already on company profile — opening Financial Statements tab (no second search).")
        await page.wait_for_timeout(800)
    return await _fetch_filtered_financial_reports_from_statements_tab(page, symbol)

def _pdf_url_embedded_date(pdf_url: str) -> Optional[str]:
    """Return YYYY-MM-DD from Tadawul fsPdf path if present (often publication date)."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", pdf_url)
    return m.group(1) if m else None


def _pdf_download_stop_path() -> Path:
    return Path(os.environ.get("STOP_FLAG_FILE", DEFAULT_STOP_PDFS_FLAG))


def _tadawul_absolute_pdf_url(pdf_url: str) -> str:
    if pdf_url.startswith("http"):
        return pdf_url
    return f"https://www.saudiexchange.sa{pdf_url}"


def _log_pdf_http_status_messages(status: int, content_type: str) -> None:
    print(f"   PDF response HTTP {status} Content-Type: {content_type[:100]}")
    if status == 403 or status == 401:
        print(
            "  Access refused by server — possible bot/WAF block, geo restriction, or session required. "
            "Try PLAYWRIGHT_HEADLESS=0, slower delays between companies, or run from a normal network."
        )
    elif status == 429:
        print("  Rate limited (HTTP 429) — increase delay between companies or retry later.")
    elif status >= 400:
        print(f"  Unexpected HTTP {status} when fetching PDF.")


async def _write_pdf_bytes_from_page(
    page: Page, pdf_path: Path, filename: str, symbol: str
) -> bool:
    print(f" Successfully accessed PDF for {symbol}")
    pdf_content = await page.evaluate(_FETCH_PDF_BYTES_JS)
    if not pdf_content:
        print(f" Failed to get PDF content for {symbol}")
        return False
    async with aiofiles.open(pdf_path, 'wb') as f:
        await f.write(bytes(pdf_content))
    print(f" Downloaded {filename} ({len(pdf_content)} bytes)")
    return True


async def _maybe_log_blocked_html_body(response) -> None:
    if response.status != 200:
        return
    try:
        snippet = (await response.text())[:600]
        low = snippet.lower()
        if any(
            w in low
            for w in ("access denied", "forbidden", "not authorized", "blocked", "captcha")
        ):
            print(f"  Response body looks like an error/login page: {snippet[:280]!r}")
    except Exception:
        pass


async def download_pdf_with_stealth(page: Page, pdf_url: str, symbol: str, year: int, statement_type: str) -> bool:
    """Download PDF; saved filename uses Tadawul column year — see module docstring."""
    try:
        stop_flag_path = _pdf_download_stop_path()
        if stop_flag_path.exists():
            print(" Stop requested. Skipping new PDF download request.")
            return False
        filename = f"{symbol}_{statement_type}_{year}.pdf"
        pdf_path = PDF_DIR / filename
        if pdf_path.exists():
            print(f"  {filename} already exists, skipping...")
            return True
        url_date = _pdf_url_embedded_date(pdf_url)
        if url_date:
            print(
                f" Downloading {filename}… (Tadawul column year={year}; "
                f"server path date={url_date} — may be publication date, not period-end inside PDF)"
            )
        else:
            print(f" Downloading {filename}… (Tadawul column year={year})")
        pdf_url = _tadawul_absolute_pdf_url(pdf_url)
        response = await page.goto(pdf_url, wait_until='networkidle')
        if stop_flag_path.exists():
            print(" Stop requested after navigation. Aborting download save.")
            return False
        status = response.status
        content_type = response.headers.get('content-type', '') or ""
        _log_pdf_http_status_messages(status, content_type)
        if 'pdf' in content_type.lower():
            return await _write_pdf_bytes_from_page(page, pdf_path, filename, symbol)
        print(f" Did not get PDF content for {symbol} (HTTP {status}, Content-Type: {content_type})")
        await _maybe_log_blocked_html_body(response)
        return False
    except Exception as e:
        print(f" Download error for {symbol}: {e}")
        return False


async def _ensure_pdf_pipeline_browser(playwright, browser: Browser, context: BrowserContext):
    if browser.is_connected():
        return playwright, browser, context
    print(" Relaunching Chromium after disconnect/crash...")
    await teardown_playwright_bundle(playwright, browser)
    return await setup_stealth_browser()


async def _relaunch_pdf_browser_after_company(playwright, browser: Browser, context: BrowserContext):
    if browser.is_connected():
        return playwright, browser, context
    print(" Chromium died during run; relaunching before next company...")
    await teardown_playwright_bundle(playwright, browser)
    return await setup_stealth_browser()


async def _pdf_retry_pause(attempt: int, max_retries: int, symbol: str) -> None:
    if attempt < max_retries - 1:
        print(f" Retrying {symbol} (attempt {attempt + 2}/{max_retries})...")
        await asyncio.sleep(_HUMANIZE_RNG.uniform(2, 5))


async def _download_filtered_reports_with_stop(
    page: Page,
    reports: List[Tuple[str, int, str]],
    symbol: str,
    stop_flag_env: str,
) -> bool:
    all_success = True
    for stype, year, pdf_url in reports:
        if Path(stop_flag_env).exists():
            print(" Stop requested. Halting further report downloads for this company.")
            all_success = False
            break
        if not await download_pdf_with_stealth(page, pdf_url, symbol, year, stype):
            all_success = False
    return all_success


async def process_company_with_retry(
    context: BrowserContext,
    browser: Browser,
    symbol: str,
    max_retries: int = 3,
) -> bool:
    stop_flag_env = os.environ.get("STOP_FLAG_FILE", DEFAULT_STOP_PDFS_FLAG)
    for attempt in range(max_retries):
        page: Optional[Page] = None
        try:
            if not browser.is_connected():
                print(f" Browser disconnected before {symbol}; relaunch required.")
                return False
            if Path(stop_flag_env).exists():
                print(" Stop requested. Aborting company processing.")
                return False
            page = await context.new_page()
            await page.mouse.move(
                _HUMANIZE_RNG.randint(100, 500), _HUMANIZE_RNG.randint(100, 300)
            )
            await asyncio.sleep(_HUMANIZE_RNG.uniform(0.5, 1.5))
            reports = await get_all_financial_reports(page, symbol)
            if not reports:
                await _safe_close_page(page)
                await _pdf_retry_pause(attempt, max_retries, symbol)
                if attempt < max_retries - 1:
                    continue
                return False
            all_success = await _download_filtered_reports_with_stop(
                page, reports, symbol, stop_flag_env
            )
            await _safe_close_page(page)
            if all_success:
                return True
            await _pdf_retry_pause(attempt, max_retries, symbol)
        except Exception as e:
            print(f" Error processing {symbol} (attempt {attempt + 1}): {e}")
            await _safe_close_page(page)
            if is_browser_closed_error(e):
                print(" Browser process lost (crash/close); will relaunch before next company.")
                return False
            if attempt < max_retries - 1:
                await asyncio.sleep(_HUMANIZE_RNG.uniform(2, 5))
    return False

async def download_all_financial_statements():
    """Download the most recent financial statements for all companies."""
    # Get company symbols from JSON file
    companies = get_company_symbols_from_json()
    if not companies:
        print(" No company symbols found. Please run the ownership scraper first.")
        return

    print(f" Found {len(companies)} companies to process")
    print(
        f" Tadawul columns: annual {target_year - 1}, Q1–Q3 {target_year} "
        f"(override REPORTING_FISCAL_YEAR if needed)"
    )

    # Setup browser with stealth configuration
    playwright, browser, context = await setup_stealth_browser()
    
    try:
        # progress reporting
        progress_path = Path(os.environ.get("PROGRESS_FILE", DEFAULT_PDFS_PROGRESS_FILE))
        processed = 0
        success_count = 0
        failed_count = 0
        
        # Stop flag support
        stop_flag = Path(os.environ.get("STOP_FLAG_FILE", DEFAULT_STOP_PDFS_FLAG))
        stop_flag.parent.mkdir(parents=True, exist_ok=True)

        for i, symbol in enumerate(companies, 1):
            if stop_flag.exists():
                print(" Stop requested. Ending PDF pipeline early.")
                break
            print(f"\n{'='*50}")
            print(f" Processing {symbol} ({i}/{len(companies)})")
            print(f"{'='*50}")

            playwright, browser, context = await _ensure_pdf_pipeline_browser(
                playwright, browser, context
            )

            success = await process_company_with_retry(context, browser, symbol)

            playwright, browser, context = await _relaunch_pdf_browser_after_company(
                playwright, browser, context
            )
            
            if success:
                success_count += 1
                print(f" Successfully processed {symbol}")
            else:
                failed_count += 1
                print(f" Failed to process {symbol}")
            processed += 1
            # write progress
            try:
                progress_path.parent.mkdir(parents=True, exist_ok=True)
                progress_payload = json.dumps({
                    "status": "running",
                    "processed": processed,
                    "success": success_count,
                    "failed": failed_count,
                    "current_symbol": symbol
                }, ensure_ascii=False)
                async with aiofiles.open(progress_path, 'w', encoding='utf-8') as f:
                    await f.write(progress_payload)
            except Exception:
                pass
            
            # Add delay between companies
            if i < len(companies):
                # If stop requested, skip waiting and break immediately
                if stop_flag.exists():
                    print(" Stop requested. Skipping wait and ending now.")
                    break
                delay = _HUMANIZE_RNG.uniform(3, 7)
                print(f" Waiting {delay:.1f} seconds before next company...")
                await asyncio.sleep(delay)
        
        # Summary
        print(f"\n{'='*50}")
        print(f" DOWNLOAD SUMMARY")
        print(f"{'='*50}")
        print(f" Successful: {success_count}")
        print(f" Failed: {failed_count}")
        total = success_count + failed_count
        rate = (success_count/total*100) if total > 0 else 0.0
        print(f" Success Rate: {rate:.1f}%")
        # mark done
        try:
            completed_payload = json.dumps({
                "status": "completed",
                "processed": processed,
                "success": success_count,
                "failed": failed_count
            }, ensure_ascii=False)
            async with aiofiles.open(progress_path, 'w', encoding='utf-8') as f:
                await f.write(completed_payload)
        except Exception:
            pass
    finally:
        await teardown_playwright_bundle(playwright, browser)

if __name__ == "__main__":
    asyncio.run(download_all_financial_statements())