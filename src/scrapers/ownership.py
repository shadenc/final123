"""Module for scraping foreign ownership data from Tadawul.

This module provides both async and sync interfaces for scraping foreign ownership data
from Tadawul, with support for database integration and file-based output.

Example:
    # Async usage with database:
    async with TadawulOwnershipScraper(base_url="https://www.saudiexchange.sa") as scraper:
        data = await scraper.get_foreign_ownership_table()
        await scraper.save_ownership_data(db_session, data)

    # Sync usage for file output:
    scraper = TadawulOwnershipScraper(base_url="https://www.saudiexchange.sa")
    scraper.scrape_to_files(output_dir="data/ownership")
"""
import json
import csv
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
import aiohttp
from bs4 import BeautifulSoup

from playwright.async_api import async_playwright, TimeoutError
from playwright.sync_api import sync_playwright

from playwright_chromium_shared import chromium_launch_options, is_browser_closed_error

# Database models removed - using file-based output only

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SELECTOR_OWNERSHIP_TABLE_ROWS = "table tbody tr"

# Extract entirely in the browser — iterating hundreds of ElementHandles triggers
# "The object has been collected to prevent unbounded heap growth" in Playwright Python.
_OWNERSHIP_TABLE_EVAL = """(selector) => {
  const rows = Array.from(document.querySelectorAll(selector));
  const out = [];
  for (const row of rows) {
    const tds = row.querySelectorAll('td');
    if (tds.length >= 5) {
      out.push({
        symbol: tds[0].innerText.trim(),
        company_name: tds[1].innerText.trim(),
        foreign_ownership: tds[2].innerText.trim(),
        max_allowed: tds[3].innerText.trim(),
        investor_limit: tds[4].innerText.trim()
      });
    }
  }
  return out;
}"""


def _extract_ownership_rows_sync(page) -> List[Dict[str, str]]:
    """Pull table rows as plain dicts via one evaluate (no ElementHandle churn)."""
    raw = page.evaluate(_OWNERSHIP_TABLE_EVAL, SELECTOR_OWNERSHIP_TABLE_ROWS)
    if not isinstance(raw, list):
        return []
    return [dict(x) for x in raw]


async def _extract_ownership_rows_async(page) -> List[Dict[str, str]]:
    raw = await page.evaluate(_OWNERSHIP_TABLE_EVAL, SELECTOR_OWNERSHIP_TABLE_ROWS)
    if not isinstance(raw, list):
        return []
    return [dict(x) for x in raw]

class TadawulOwnershipScraper:
    """Scraper for foreign ownership data from Tadawul.
    
    This class provides both async and sync interfaces for scraping foreign ownership data
    from Tadawul. It supports both database integration and file-based output.
    """
    
    def __init__(self, base_url: str, request_delay: float = 1.0, playwright_browser=None, playwright_context=None):
        """Initialize the scraper.
        
        Args:
            base_url: Base URL for Tadawul website
            request_delay: Delay between requests in seconds
            playwright_browser: Optional existing Playwright browser instance
            playwright_context: Optional existing Playwright context
        """
        self.base_url = base_url.rstrip('/')
        self.request_delay = request_delay
        self.session: Optional[aiohttp.ClientSession] = None
        self.playwright_browser = playwright_browser
        self.playwright_context = playwright_context
        self._launched_browser = False

    async def __aenter__(self):
        """Set up async session and browser with proper fingerprinting."""
        self.session = aiohttp.ClientSession()
        if self.playwright_browser is None:
            p = await async_playwright().start()
            self.playwright_browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )
            self._launched_browser = True
        
        if self.playwright_context is None:
            self.playwright_context = await self.playwright_browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                locale='ar-SA',
                timezone_id='Asia/Riyadh',
                geolocation={'latitude': 24.7136, 'longitude': 46.6753},
                permissions=['geolocation'],
                extra_http_headers={
                    'Accept-Language': 'ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )
            await self.playwright_context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up async resources."""
        if self.session:
            await self.session.close()
        if self._launched_browser and self.playwright_browser:
            await self.playwright_browser.close()

    async def get_foreign_ownership_table(self) -> list[dict]:
        """Scrape the foreign ownership table from Tadawul.
        
        Returns:
            List of dictionaries containing foreign ownership data for each company.
            
        Raises:
            ValueError: If the table cannot be found or no data is extracted.
            TimeoutError: If the page takes too long to load.
        """
        if not self.playwright_context:
            raise RuntimeError("Scraper must be used as an async context manager")

        page = await self.playwright_context.new_page()
        url = f"{self.base_url}/wps/portal/saudiexchange/newsandreports/reports-publications/foreign-ownership?locale=ar"
        logger.info(f"Navigating to {url}")
        
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            logger.info("Waiting for table to load...")
            await page.wait_for_selector(SELECTOR_OWNERSHIP_TABLE_ROWS, timeout=30000)

            data = await _extract_ownership_rows_async(page)
            if not data:
                raise ValueError("No data was extracted from the table")
            
            logger.info(f"Successfully extracted data for {len(data)} companies")
            return data
            
        except TimeoutError:
            logger.error("Timeout while waiting for the table to load")
            raise
        finally:
            await page.close()



    def scrape_to_files(self, output_dir: str = ".", debug: bool = False) -> None:
        """Synchronous method to scrape data and save to files.
        
        Args:
            output_dir: Directory where output files will be saved
            debug: If True, runs in visible mode and saves debug info
            
        Raises:
            ValueError: If the table cannot be found or no data is extracted
            TimeoutError: If the page takes too long to load
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Save JSON to frontend/public/ for React app
        frontend_json_path = Path("frontend/public/foreign_ownership_data.json")
        frontend_json_path.parent.mkdir(parents=True, exist_ok=True)

        # Save JSON also to backend data directory
        backend_json_path = output_path / "foreign_ownership_data.json"
        output_path.mkdir(parents=True, exist_ok=True)

        # Save CSV to original output directory
        csv_path = output_path / "foreign_ownership_data.csv"
        
        logger.info("Starting foreign ownership data scraping...")
        # Headed browsers from API subprocess often crash (TargetClosedError) on macOS when
        # another Playwright/Chromium is active — default headless unless debug=True.
        headless = not debug

        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                self._scrape_to_files_sync_playwright(
                    output_path=output_path,
                    frontend_json_path=frontend_json_path,
                    backend_json_path=backend_json_path,
                    csv_path=csv_path,
                    headless=headless,
                    debug=debug,
                )
                return
            except Exception as e:
                last_err = e
                if is_browser_closed_error(e) and attempt < 3:
                    logger.warning(
                        "Foreign ownership scrape lost browser (attempt %s/3): %s — retrying…",
                        attempt,
                        e,
                    )
                    time.sleep(2.0 * attempt)
                    continue
                raise
        if last_err:
            raise last_err

    def _scrape_to_files_sync_playwright(
        self,
        *,
        output_path: Path,
        frontend_json_path: Path,
        backend_json_path: Path,
        csv_path: Path,
        headless: bool,
        debug: bool,
    ) -> None:
        with sync_playwright() as p:
            browser = p.chromium.launch(**chromium_launch_options(headless))

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                locale='ar-SA',
                timezone_id='Asia/Riyadh',
                geolocation={'latitude': 24.7136, 'longitude': 46.6753},
                permissions=['geolocation'],
                extra_http_headers={
                    'Accept-Language': 'ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )
            
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            
            page = context.new_page()
            
            try:
                url = f"{self.base_url}/wps/portal/saudiexchange/newsandreports/reports-publications/foreign-ownership?locale=ar"
                logger.info(f"Navigating to {url}")
                
                # Navigate with longer timeout and wait for network idle
                page.goto(url, wait_until="networkidle", timeout=120000)
                logger.info("Page loaded, waiting for content...")
                
                # Wait for any content to appear
                logger.info("Waiting for any table content...")
                try:
                    # First try waiting for the table
                    page.wait_for_selector("table", timeout=60000)
                    logger.info("Table element found")
                    
                    # Then wait for rows with a shorter timeout
                    page.wait_for_selector(SELECTOR_OWNERSHIP_TABLE_ROWS, timeout=30000)
                    logger.info("Table rows found")
                except TimeoutError:
                    if debug:
                        # Save debug info
                        debug_path = output_path / "debug"
                        debug_path.mkdir(exist_ok=True)
                        page.screenshot(path=str(debug_path / "page.png"))
                        with open(debug_path / "page.html", "w", encoding="utf-8") as f:
                            f.write(page.content())
                        logger.info(f"Debug info saved to {debug_path}")
                    raise
                
                data = _extract_ownership_rows_sync(page)
                logger.info(f"Extracted {len(data)} rows from table (browser-side evaluate)")
                
                if not data:
                    raise ValueError("No data was extracted from the table")
                
                logger.info(f"Successfully extracted data for {len(data)} companies")
                
                # Save as JSON to frontend/public/ for React app
                logger.info(f"Saving data to {frontend_json_path}")
                with open(frontend_json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                # Save as JSON to backend data directory for calculators/APIs
                logger.info(f"Saving data to {backend_json_path}")
                with open(backend_json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                # Save as CSV
                logger.info(f"Saving data to {csv_path}")
                with open(csv_path, "w", newline='', encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=["symbol", "company_name", "foreign_ownership", "max_allowed", "investor_limit"])
                    writer.writeheader()
                    writer.writerows(data)
                
                logger.info("Data successfully saved to frontend/public/foreign_ownership_data.json, data/ownership/foreign_ownership_data.json, and CSV file")
                
            except TimeoutError:
                logger.error("Timeout while waiting for the table to load")
                raise
            except Exception as e:
                logger.error(f"Error during scraping: {e}")
                raise
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass


if __name__ == "__main__":
    scraper = TadawulOwnershipScraper(base_url="https://www.saudiexchange.sa")
    # Default debug=False (headless). Set OWNERSHIP_DEBUG=1 for visible browser when running manually.
    _dbg = os.environ.get("OWNERSHIP_DEBUG", "").strip().lower() in ("1", "true", "yes")
    scraper.scrape_to_files(output_dir="data/ownership", debug=_dbg)