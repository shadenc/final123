
#!/usr/bin/env python3
"""
Quarterly Net Profit Scraper for Saudi Exchange
Scrapes quarterly net profit data from company financial information pages
"""

import asyncio
import json

import aiofiles
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import os

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
OUTPUT_DIR = Path("data/results")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "quarterly_net_profit.json"
SEARCH_INPUT_SELECTOR = "#query-input"

# Substrings used to detect quarterly (not annual-only) period columns on income tables
_QUARTERLY_COLUMN_DATE_SNIPPETS = (
    "2025-06-30",
    "2025-03-31",
    "2024-09-30",
    "2024-06-30",
)


def _table_text_has_quarterly_snippets(table_text: str) -> bool:
    t = table_text.lower()
    return any(s in t for s in _QUARTERLY_COLUMN_DATE_SNIPPETS)


async def _find_statement_of_income_table(tables: List[Any]) -> Optional[Any]:
    for i, table in enumerate(tables):
        try:
            table_text = await table.text_content()
            print(f"📊 Table {i} content preview: {table_text[:200]}...")
            if "statement of income" not in table_text.lower():
                continue
            if _table_text_has_quarterly_snippets(table_text):
                print(f"✅ Found Statement of Income table {i} with quarterly dates")
                return table
            print(f"📊 Found Statement of Income table {i} but it's annual data")
        except Exception as e:
            print(f"⚠️  Error reading table {i}: {e}")

    print("🔍 Looking for any table with quarterly dates...")
    for i, table in enumerate(tables):
        try:
            table_text = await table.text_content()
            if _table_text_has_quarterly_snippets(table_text):
                print(f"✅ Found table {i} with quarterly dates")
                return table
        except Exception:
            continue
    return None


async def _append_iso_dates_from_cells(cells, label: str) -> List[str]:
    out: List[str] = []
    for i, cell in enumerate(cells):
        try:
            text = (await cell.text_content() or "").strip()
            print(f"  {label} {i}: '{text}'")
            if text and len(text) == 10 and text.count("-") == 2:
                out.append(text)
        except Exception as e:
            print(f"⚠️  Error reading {label} {i}: {e}")
    return out


async def _collect_quarterly_date_strings(statement_of_income_table: Any) -> List[str]:
    header_cells = await statement_of_income_table.query_selector_all("thead tr th")
    print(f"📅 Table headers: {len(header_cells)} cells")
    quarterly_dates = await _append_iso_dates_from_cells(header_cells, "Header")

    if quarterly_dates:
        return quarterly_dates

    print("❌ No quarterly dates found in headers, checking table body...")
    body_rows = await statement_of_income_table.query_selector_all("tbody tr")
    if not body_rows:
        return quarterly_dates
    first_row_cells = await body_rows[0].query_selector_all("td")
    print(f"📊 First row has {len(first_row_cells)} cells")
    quarterly_dates.extend(await _append_iso_dates_from_cells(first_row_cells, "Cell"))
    return quarterly_dates


def _iso_dates_to_quarter_labels(quarterly_dates: List[str]) -> List[str]:
    quarters: List[str] = []
    for date in quarterly_dates:
        try:
            year, month, _day = date.split("-")
            month = int(month)
            if month <= 3:
                quarters.append(f"Q1 {year}")
            elif month <= 6:
                quarters.append(f"Q2 {year}")
            elif month <= 9:
                quarters.append(f"Q3 {year}")
            else:
                quarters.append(f"Q4 {year}")
        except (ValueError, AttributeError):
            quarters.append(date)
    return quarters


async def _find_net_profit_data_row(statement_of_income_table: Any) -> Optional[Any]:
    rows = await statement_of_income_table.query_selector_all("tbody tr")
    print(f"🔍 Looking through {len(rows)} rows for Net Profit...")
    for i, row in enumerate(rows):
        try:
            cells = await row.query_selector_all("td")
            if not cells:
                continue
            first_cell_text = (await cells[0].text_content() or "").strip().lower()
            if "net profit (loss) before zakat and tax" in first_cell_text:
                print(f"✅ Found Net Profit row {i}: '{first_cell_text}'")
                return row
            if i < 5:
                print(f"  Row {i}: '{first_cell_text}'")
        except Exception as e:
            print(f"⚠️  Error reading row {i}: {e}")
    return None


async def _extract_net_profit_by_quarters(net_profit_row: Any, quarters: List[str]) -> Dict[str, Optional[float]]:
    cells = await net_profit_row.query_selector_all("td")
    net_profit_values: Dict[str, Optional[float]] = {}
    print(f"📊 Net Profit row has {len(cells)} cells")
    for i, quarter in enumerate(quarters):
        if i + 1 >= len(cells):
            continue
        cell = cells[i + 1]
        value_text = (await cell.text_content() or "").strip()
        if value_text and value_text != "-":
            clean_value = value_text.replace(",", "").replace(" ", "")
            try:
                numeric_value = float(clean_value)
                net_profit_values[quarter] = numeric_value
                print(f"💰 {quarter}: {numeric_value:,.0f}")
            except ValueError:
                print(f"⚠️  Could not parse value for {quarter}: '{value_text}'")
                net_profit_values[quarter] = None
        else:
            net_profit_values[quarter] = None
            print(f"⚠️  No value for {quarter}")
    return net_profit_values


async def _open_stealth_page(context: BrowserContext) -> Page:
    page = await context.new_page()
    await page.mouse.move(random.randint(100, 500), random.randint(100, 300))
    await asyncio.sleep(random.uniform(0.5, 1.5))
    return page


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
            print(f"❌ JSON file not found: {json_path}")
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
        print(f"📋 Found {len(symbols)} company symbols from JSON file")
        return symbols
        
    except Exception as e:
        print(f"❌ Error reading JSON file: {e}")
        return []

async def setup_stealth_browser():
    """Setup Playwright browser with stealth configuration."""
    playwright = await async_playwright().start()

    ch = os.environ.get("PLAYWRIGHT_CHANNEL", "").strip()
    if ch:
        print(f"🌐 Using Chromium channel: {ch}")

    browser = await playwright.chromium.launch(**chromium_launch_options(_env_headless()))
    
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        locale='en-US',
        timezone_id='Asia/Riyadh',
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
    
    # Add stealth scripts
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
    """Navigate to the company profile page using search."""
    search_url = "https://www.saudiexchange.sa/wps/portal/saudiexchange/hidden/search/!ut/p/z0/04_Sj9CPykssy0xPLMnMz0vMAfIjo8ziTR3NDIw8LAz8DTxCnA3MDILdzUJDLAyNHI30C7IdFQEEx_vC/"
    
    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        print(f"🔍 Navigating to search page for symbol {symbol}")
        
        # Wait for search input and fill symbol
        await page.wait_for_selector(SEARCH_INPUT_SELECTOR, timeout=5000)
        await page.click(SEARCH_INPUT_SELECTOR)
        await page.fill(SEARCH_INPUT_SELECTOR, symbol)
        await page.wait_for_timeout(500)
        
        # Submit search using JavaScript
        await page.evaluate("document.querySelector('div.srchBlueBtn').click()")
        await page.wait_for_timeout(2000)
        
        # Find and click 'Visit Profile' link
        links = await page.query_selector_all("a.pageLink")
        visit_links = []
        
        for link in links:
            text = (await link.text_content() or "").strip().lower()
            if text == "visit profile":
                visit_links.append(link)
        
        if not visit_links:
            print(f"❌ No 'Visit Profile' link found for symbol {symbol}")
            return False
        
        await visit_links[0].click()
        await page.wait_for_load_state('domcontentloaded')
        print(f"✅ Successfully navigated to profile for {symbol}")
        return True
        
    except Exception as e:
        print(f"❌ Navigation failed for {symbol}: {e}")
        return False

async def navigate_to_financial_information(page: Page, symbol: str) -> bool:
    """Navigate to FINANCIAL INFORMATION tab and click Quarterly."""
    try:
        print(f"📊 Looking for FINANCIAL INFORMATION tab for {symbol}...")

        await page.wait_for_timeout(3000)

        # Locators avoid stale/wrong ElementHandles that triggered '_object' errors on click.
        fi_tab = page.locator("#balancesheet").first
        if await fi_tab.count() == 0:
            fi_tab = page.get_by_role("tab", name=re.compile(r"financial\s+information", re.I))
        await fi_tab.wait_for(state="visible", timeout=20000)
        tab_label = await fi_tab.text_content()
        tab_id = await fi_tab.get_attribute("id")
        print(f"✅ Found FINANCIAL INFORMATION tab: '{(tab_label or '').strip()}' (ID: {tab_id})")
        await fi_tab.scroll_into_view_if_needed()
        await fi_tab.click()
        await page.wait_for_timeout(2000)
        print(f"✅ Clicked FINANCIAL INFORMATION tab for {symbol}")

        print(f"🔍 Looking for Quarterly tab for {symbol}...")
        await page.wait_for_timeout(2000)

        quarterly = page.get_by_text("Quarterly", exact=True).first
        try:
            await quarterly.wait_for(state="visible", timeout=12000)
        except Exception:
            quarterly = page.locator("a, li, button, span").filter(
                has_text=re.compile(r"^\s*Quarterly\s*$", re.I)
            ).first
            await quarterly.wait_for(state="visible", timeout=12000)

        await quarterly.scroll_into_view_if_needed()
        await quarterly.click()
        await page.wait_for_timeout(2000)
        print(f"✅ Clicked Quarterly tab for {symbol}")

        return True

    except Exception as e:
        print(f"❌ Failed to navigate to financial information for {symbol}: {e}")
        print("🔍 Trying to find any financial data table...")
        tables = await page.query_selector_all("table")
        if tables:
            print(f"📊 Found {len(tables)} tables, proceeding to scrape...")
            return True
        return False

async def scrape_quarterly_net_profit(page: Page, symbol: str) -> Optional[Dict]:
    """Scrape quarterly net profit data from the financial table."""
    try:
        print(f"📈 Scraping quarterly net profit data for {symbol}...")

        await page.wait_for_selector("table", timeout=10000)
        await page.wait_for_timeout(2000)

        tables = await page.query_selector_all("table")
        print(f"🔍 Found {len(tables)} tables on the page")

        statement_of_income_table = await _find_statement_of_income_table(tables)
        if not statement_of_income_table:
            print(f"❌ Statement of Income table not found for {symbol}")
            return None

        quarterly_dates = await _collect_quarterly_date_strings(statement_of_income_table)
        if not quarterly_dates:
            print(f"❌ No quarterly dates found for {symbol}")
            return None

        print(f"📅 Found quarterly dates: {quarterly_dates}")
        quarters = _iso_dates_to_quarter_labels(quarterly_dates)
        print(f"📅 Converted to quarters: {quarters}")

        net_profit_row = await _find_net_profit_data_row(statement_of_income_table)
        if not net_profit_row:
            print(f"❌ Net Profit row not found for {symbol}")
            return None

        net_profit_values = await _extract_net_profit_by_quarters(net_profit_row, quarters)
        if not net_profit_values:
            print(f"❌ No net profit values extracted for {symbol}")
            return None

        result = {
            "company_symbol": symbol,
            "scraped_date": datetime.now().isoformat(),
            "quarterly_net_profit": net_profit_values,
        }
        print(f"✅ Successfully scraped quarterly net profit data for {symbol}")
        return result

    except Exception as e:
        print(f"❌ Error scraping net profit for {symbol}: {e}")
        import traceback

        traceback.print_exc()
        return None

async def _load_existing_net_profit_map() -> Dict[str, dict]:
    existing_map: Dict[str, dict] = {}
    if not OUTPUT_FILE.exists():
        return existing_map
    try:
        async with aiofiles.open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            raw_existing = await f.read()
        existing_list = json.loads(raw_existing)
        for item in existing_list:
            sym = str(item.get("company_symbol", "")).strip()
            if sym:
                existing_map[sym] = item
        print(f"🔄 Loaded existing net profit data for {len(existing_map)} companies to merge")
    except Exception as e:
        print(f"⚠️ Failed to load existing net profit file, starting fresh merge: {e}")
        return {}
    return existing_map


async def _persist_net_profit_merge_snapshot(existing_map: dict) -> None:
    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        merge_payload = json.dumps(
            list(existing_map.values()), indent=2, ensure_ascii=False
        )
        async with aiofiles.open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            await f.write(merge_payload)
        print(f"💾 Incrementally updated: {OUTPUT_FILE}")
    except Exception as e:
        print(f"⚠️ Failed to write incremental update: {e}")


async def _write_net_profit_progress(
    progress_path: Path,
    *,
    status: str,
    processed: int,
    success: int,
    failed: int,
    current_symbol: Optional[str] = None,
) -> None:
    payload: Dict[str, object] = {
        "status": status,
        "processed": processed,
        "success": success,
        "failed": failed,
    }
    if current_symbol is not None:
        payload["current_symbol"] = current_symbol
    try:
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(progress_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


async def _recover_stealth_browser(
    playwright,
    browser: Browser,
    context: BrowserContext,
    message: str,
):
    if browser.is_connected():
        return playwright, browser, context
    print(message)
    await teardown_playwright_bundle(playwright, browser)
    return await setup_stealth_browser()


async def _retry_after_delay(attempt: int, max_retries: int, message: str) -> bool:
    """If more attempts remain, log, sleep, and return True (caller should continue)."""
    if attempt >= max_retries - 1:
        return False
    print(message)
    await asyncio.sleep(random.uniform(2, 5))
    return True


async def process_company_with_retry(
    context: BrowserContext,
    browser: Browser,
    symbol: str,
    max_retries: int = 3,
) -> Optional[Dict]:
    """Process a single company with retry logic."""
    for attempt in range(max_retries):
        page: Optional[Page] = None
        try:
            if not browser.is_connected():
                print(f"⚠️ Browser disconnected before {symbol}; relaunch required.")
                return None
            page = await _open_stealth_page(context)

            if not await navigate_to_company_profile(page, symbol):
                await _safe_close_page(page)
                if await _retry_after_delay(
                    attempt,
                    max_retries,
                    f"🔄 Retrying navigation for {symbol} (attempt {attempt + 2}/{max_retries})...",
                ):
                    continue
                return None

            if not await navigate_to_financial_information(page, symbol):
                await _safe_close_page(page)
                if await _retry_after_delay(
                    attempt,
                    max_retries,
                    f"🔄 Retrying financial info navigation for {symbol} (attempt {attempt + 2}/{max_retries})...",
                ):
                    continue
                return None

            result = await scrape_quarterly_net_profit(page, symbol)
            await _safe_close_page(page)

            if result:
                return result
            if await _retry_after_delay(
                attempt,
                max_retries,
                f"🔄 Retrying scraping for {symbol} (attempt {attempt + 2}/{max_retries})...",
            ):
                continue

        except Exception as e:
            print(f"❌ Error processing {symbol} (attempt {attempt + 1}): {e}")
            await _safe_close_page(page)
            if is_browser_closed_error(e):
                print("⚠️ Browser process lost (crash/close); will relaunch before next company.")
                return None
            if attempt < max_retries - 1:
                await asyncio.sleep(random.uniform(2, 5))

    return None

async def scrape_all_companies_net_profit():
    """Scrape quarterly net profit data for all companies."""
    companies = get_company_symbols_from_json()

    if not companies:
        print("❌ No company symbols found. Please ensure foreign_ownership_data.json exists.")
        return

    print(f"📋 Found {len(companies)} companies to process")

    playwright, browser, context = await setup_stealth_browser()

    try:
        existing_map = await _load_existing_net_profit_map()

        success_count = 0
        failed_count = 0
        progress_path = Path(os.environ.get("PROGRESS_FILE", "data/runtime/net_profit_progress.json"))
        processed = 0

        stop_flag = Path(os.environ.get("STOP_FLAG_FILE", "data/runtime/stop_net_profit.flag"))
        stop_flag.parent.mkdir(parents=True, exist_ok=True)

        for i, symbol in enumerate(companies, 1):
            if stop_flag.exists():
                print("🛑 Stop requested. Ending net profit scraping early.")
                break
            print(f"\n{'='*60}")
            print(f"📊 Processing {symbol} ({i}/{len(companies)})")
            print(f"{'='*60}")

            playwright, browser, context = await _recover_stealth_browser(
                playwright,
                browser,
                context,
                "♻️ Relaunching Chromium after disconnect/crash...",
            )

            result = await process_company_with_retry(context, browser, symbol)

            playwright, browser, context = await _recover_stealth_browser(
                playwright,
                browser,
                context,
                "♻️ Chromium died during run; relaunching before next company...",
            )

            if result:
                success_count += 1
                print(f"✅ Successfully processed {symbol}")
                existing_map[str(symbol)] = result
                await _persist_net_profit_merge_snapshot(existing_map)
            else:
                failed_count += 1
                print(f"❌ Failed to process {symbol}")
            processed += 1
            await _write_net_profit_progress(
                progress_path,
                status="running",
                processed=processed,
                success=success_count,
                failed=failed_count,
                current_symbol=symbol,
            )

            try:
                limit = int(os.environ.get("LIMIT_COMPANIES", "0"))
            except Exception:
                limit = 0
            if limit and i >= limit:
                print(f"\n🛑 Stopping after {limit} companies as requested")
                break

            if i < len(companies) and i < 10:
                delay = random.uniform(3, 7)
                print(f"⏳ Waiting {delay:.1f} seconds before next company...")
                await asyncio.sleep(delay)

        print(f"\n{'='*60}")
        print("📊 SCRAPING SUMMARY")
        print(f"{'='*60}")
        print(f"✅ Successful: {success_count}")
        print(f"❌ Failed: {failed_count}")
        print(
            f"📈 Success Rate: {(success_count/(success_count+failed_count)*100) if (success_count+failed_count)>0 else 0:.1f}%"
        )
        print(f"💾 Data saved to: {OUTPUT_FILE}")
        await _write_net_profit_progress(
            progress_path,
            status="completed",
            processed=processed,
            success=success_count,
            failed=failed_count,
        )

    finally:
        await teardown_playwright_bundle(playwright, browser)

if __name__ == "__main__":
    print("🚀 Starting Quarterly Net Profit Scraper...")
    print("📊 This will scrape quarterly net profit data from Saudi Exchange")
    print("⏳ Please ensure you have a stable internet connection")
    
    # Run the scraper
    asyncio.run(scrape_all_companies_net_profit())
