#!/usr/bin/env python3
"""
Quarterly Update Orchestrator
Coordinates all three scrapers to keep the system up-to-date with new quarterly data.
Only processes new quarters to avoid re-downloading existing data.
"""

import asyncio
import json

import aiofiles
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
import random

from playwright.async_api import async_playwright, Browser, Page

# Import our existing scrapers
from ownership import TadawulOwnershipScraper
from hybrid_financial_downloader import setup_stealth_browser, get_all_financial_reports, download_pdf_with_stealth
from scrape_quarterly_net_profit import setup_stealth_browser as setup_net_profit_browser, process_company_with_retry as process_net_profit_company

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

FOREIGN_OWNERSHIP_JSON = "foreign_ownership_data.json"


class QuarterlyUpdateOrchestrator:
    """Orchestrates quarterly updates for all three data sources."""
    
    def __init__(self):
        self.base_dir = Path(".")
        self.data_dir = self.base_dir / "data"
        self.pdf_dir = self.data_dir / "pdfs"
        self.results_dir = self.data_dir / "results"
        self.frontend_dir = self.base_dir / "frontend" / "public"
        
        # Ensure directories exist
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.frontend_dir.mkdir(parents=True, exist_ok=True)
        
        # Quarter definitions
        self.QUARTER_DATES = {
            "Q1": "03-31",
            "Q2": "06-30", 
            "Q3": "09-30",
            "Q4": "12-31"
        }
        
        # Current year and quarter
        self.current_year = datetime.now().year
        self.current_quarter = self._get_current_quarter()
        
        logger.info(f"Initialized orchestrator for {self.current_year} {self.current_quarter}")
    
    def _get_current_quarter(self) -> str:
        """Determine the current quarter based on date."""
        today = date.today()
        month = today.month
        
        if month <= 3:
            return "Q1"
        elif month <= 6:
            return "Q2"
        elif month <= 9:
            return "Q3"
        else:
            return "Q4"
    
    def _get_available_quarters(self) -> List[Tuple[str, int]]:
        """Get list of quarters that should be available based on current date."""
        quarters = []
        
        # Add current year quarters up to current quarter
        for q in ["Q1", "Q2", "Q3", "Q4"]:
            if q <= self.current_quarter:
                quarters.append((q, self.current_year))
        
        # Add previous year Q4 (Annual) for comparison
        quarters.append(("Q4", self.current_year - 1))
        
        return quarters
    
    def _check_existing_pdfs(self, symbol: str) -> Set[str]:
        """Check what PDFs already exist for a company."""
        existing_pdfs = set()
        
        if not self.pdf_dir.exists():
            return existing_pdfs
        
        # Look for existing PDFs for this symbol
        for pdf_file in self.pdf_dir.glob(f"{symbol}_*.pdf"):
            # Extract quarter and year from filename
            filename = pdf_file.stem
            parts = filename.split("_")
            if len(parts) >= 3:
                quarter = parts[1]
                year = parts[2]
                existing_pdfs.add(f"{quarter}_{year}")
        
        return existing_pdfs
    
    def _check_existing_net_profit_data(self, symbol: str) -> Set[str]:
        """Check what net profit data already exists for a company."""
        existing_quarters = set()
        
        net_profit_file = self.results_dir / "quarterly_net_profit.json"
        if not net_profit_file.exists():
            return existing_quarters
        
        try:
            with open(net_profit_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Find this company's data
            for company_data in data:
                if company_data.get("company_symbol") == symbol:
                    quarterly_data = company_data.get("quarterly_net_profit", {})
                    existing_quarters = set(quarterly_data.keys())
                    break
                    
        except Exception as e:
            logger.warning(f"Error reading net profit data: {e}")
        
        return existing_quarters
    
    def _check_existing_ownership_data(self) -> Set[str]:
        """Check what company symbols already exist in ownership data."""
        existing_symbols = set()
        
        ownership_file = self.frontend_dir / FOREIGN_OWNERSHIP_JSON
        if not ownership_file.exists():
            return existing_symbols
        
        try:
            with open(ownership_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            existing_symbols = {item.get("symbol") for item in data if item.get("symbol")}
            
        except Exception as e:
            logger.warning(f"Error reading ownership data: {e}")
        
        return existing_symbols
    
    async def update_foreign_ownership_data(self) -> bool:
        """Update foreign ownership data if needed."""
        logger.info("🔄 Checking foreign ownership data...")
        
        try:
            async with TadawulOwnershipScraper(base_url="https://www.saudiexchange.sa") as scraper:
                new_data = await scraper.get_foreign_ownership_table()
                
                if new_data:
                    # Save to frontend directory
                    output_file = self.frontend_dir / FOREIGN_OWNERSHIP_JSON
                    payload = json.dumps(new_data, ensure_ascii=False, indent=2)
                    async with aiofiles.open(output_file, 'w', encoding='utf-8') as f:
                        await f.write(payload)
                    
                    logger.info(f"✅ Updated foreign ownership data: {len(new_data)} companies")
                    return True
                else:
                    logger.warning("⚠️  No foreign ownership data retrieved")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error updating foreign ownership data: {e}")
            return False
    
    async def update_financial_pdfs(self, symbols: List[str]) -> Dict[str, List[str]]:
        """Update financial PDFs for companies, only downloading new quarters."""
        logger.info("🔄 Updating financial PDFs...")
        
        # Setup browser
        playwright, browser, context = await setup_stealth_browser()
        
        results = {}
        
        try:
            for i, symbol in enumerate(symbols, 1):
                logger.info(f"📊 Processing PDFs for {symbol} ({i}/{len(symbols)})")
                
                # Check what quarters we already have
                existing_quarters = self._check_existing_pdfs(symbol)
                logger.info(f"📁 Existing PDFs for {symbol}: {existing_quarters}")
                
                # Get available reports from Tadawul (use configured context, keep page open for downloads)
                page = await context.new_page()
                try:
                    reports = await get_all_financial_reports(page, symbol)
                    
                    if not reports:
                        logger.warning(f"⚠️  No reports found for {symbol}")
                        results[symbol] = []
                        continue
                    
                    # Filter for new quarters only
                    new_reports = []
                    for stype, year, pdf_url in reports:
                        quarter_key = f"{stype}_{year}"
                        if quarter_key not in existing_quarters:
                            new_reports.append((stype, year, pdf_url))
                    
                    if not new_reports:
                        logger.info(f"✅ {symbol}: All PDFs already up-to-date")
                        results[symbol] = []
                        continue
                    
                    logger.info(f"📥 {symbol}: Downloading {len(new_reports)} new reports")
                    
                    downloaded = []
                    for stype, year, pdf_url in new_reports:
                        success = await download_pdf_with_stealth(page, pdf_url, symbol, year, stype)
                        if success:
                            downloaded.append(f"{stype}_{year}")
                    
                    results[symbol] = downloaded
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass
                
                # Add delay between companies
                if i < len(symbols):
                    delay = random.uniform(2, 5)
                    await asyncio.sleep(delay)
        
        finally:
            await browser.close()
            await playwright.stop()
        
        return results
    
    async def update_net_profit_data(self, symbols: List[str]) -> Dict[str, List[str]]:
        """Update net profit data for companies, only scraping new quarters."""
        logger.info("🔄 Updating net profit data...")
        
        # Setup browser
        playwright, browser, context = await setup_net_profit_browser()
        
        results = {}
        
        try:
            for i, symbol in enumerate(symbols, 1):
                logger.info(f"📊 Processing net profit for {symbol} ({i}/{len(symbols)})")
                
                # Check what quarters we already have
                existing_quarters = self._check_existing_net_profit_data(symbol)
                logger.info(f"📁 Existing net profit data for {symbol}: {existing_quarters}")
                
                # Process company to get new data
                new_data = await process_net_profit_company(context, browser, symbol)
                
                if new_data:
                    # Check what's new
                    new_quarters = []
                    quarterly_data = new_data.get("quarterly_net_profit", {})
                    
                    for quarter, value in quarterly_data.items():
                        if quarter not in existing_quarters and value is not None:
                            new_quarters.append(quarter)
                    
                    if new_quarters:
                        logger.info(f"📈 {symbol}: New quarters: {new_quarters}")
                        
                        # Update the existing data file
                        await self._update_net_profit_file(symbol, new_data)
                        results[symbol] = new_quarters
                    else:
                        logger.info(f"✅ {symbol}: Net profit data already up-to-date")
                        results[symbol] = []
                else:
                    logger.warning(f"⚠️  Failed to get net profit data for {symbol}")
                    results[symbol] = []
                
                # Add delay between companies
                if i < len(symbols):
                    delay = random.uniform(2, 5)
                    await asyncio.sleep(delay)
        
        finally:
            await browser.close()
            await playwright.stop()
        
        return results
    
    async def _update_net_profit_file(self, symbol: str, new_data: Dict):
        """Update the net profit JSON file with new data for a company."""
        net_profit_file = self.results_dir / "quarterly_net_profit.json"
        
        # Load existing data
        existing_data = []
        if net_profit_file.exists():
            try:
                async with aiofiles.open(net_profit_file, 'r', encoding='utf-8') as f:
                    raw = await f.read()
                existing_data = json.loads(raw)
            except Exception as e:
                logger.warning(f"Error reading existing net profit data: {e}")
        
        # Find and update existing company data, or add new
        company_found = False
        for i, company in enumerate(existing_data):
            if company.get("company_symbol") == symbol:
                # Update existing company data
                existing_quarters = company.get("quarterly_net_profit", {})
                new_quarters = new_data.get("quarterly_net_profit", {})
                
                # Merge new quarters with existing
                merged_quarters = {**existing_quarters, **new_quarters}
                existing_data[i]["quarterly_net_profit"] = merged_quarters
                existing_data[i]["last_updated"] = datetime.now().isoformat()
                
                company_found = True
                break
        
        if not company_found:
            # Add new company data
            new_data["last_updated"] = datetime.now().isoformat()
            existing_data.append(new_data)
        
        # Save updated data
        out_payload = json.dumps(existing_data, indent=2, ensure_ascii=False)
        async with aiofiles.open(net_profit_file, 'w', encoding='utf-8') as f:
            await f.write(out_payload)
        
        logger.info(f"💾 Updated net profit data for {symbol}")
    
    async def run_quarterly_update(self, force_full_update: bool = False) -> Dict:
        """Run the complete quarterly update process."""
        logger.info("🚀 Starting Quarterly Update Process")
        logger.info(f"📅 Current: {self.current_year} {self.current_quarter}")
        
        start_time = datetime.now()
        
        # Step 1: Update foreign ownership data
        ownership_success = await self.update_foreign_ownership_data()
        
        # Step 2: Get company symbols
        if ownership_success:
            ownership_file = self.frontend_dir / FOREIGN_OWNERSHIP_JSON
            async with aiofiles.open(ownership_file, 'r', encoding='utf-8') as f:
                raw_own = await f.read()
            ownership_data = json.loads(raw_own)
            
            symbols = [item['symbol'] for item in ownership_data if item.get('symbol')]
            logger.info(f"📋 Processing {len(symbols)} companies")
        else:
            logger.error("❌ Cannot proceed without ownership data")
            return {"success": False, "error": "Ownership data update failed"}
        
        # Step 3: Update financial PDFs
        pdf_results = await self.update_financial_pdfs(symbols)
        
        # Step 4: Update net profit data
        net_profit_results = await self.update_net_profit_data(symbols)
        
        # Summary
        end_time = datetime.now()
        duration = end_time - start_time
        
        summary = {
            "success": True,
            "timestamp": start_time.isoformat(),
            "duration_seconds": duration.total_seconds(),
            "companies_processed": len(symbols),
            "pdf_updates": pdf_results,
            "net_profit_updates": net_profit_results,
            "total_new_pdfs": sum(len(quarters) for quarters in pdf_results.values()),
            "total_new_quarters": sum(len(quarters) for quarters in net_profit_results.values())
        }
        
        # Save update summary
        summary_file = self.results_dir / "quarterly_update_summary.json"
        summary_payload = json.dumps(summary, indent=2, ensure_ascii=False)
        async with aiofiles.open(summary_file, 'w', encoding='utf-8') as f:
            await f.write(summary_payload)
        
        logger.info("🎉 Quarterly Update Complete!")
        logger.info(f"⏱️  Duration: {duration}")
        logger.info(f"📊 Companies: {len(symbols)}")
        logger.info(f"📁 New PDFs: {summary['total_new_pdfs']}")
        logger.info(f"📈 New Quarters: {summary['total_new_quarters']}")
        
        return summary

async def main():
    """Main function to run the quarterly update."""
    orchestrator = QuarterlyUpdateOrchestrator()
    
    # Run the update
    result = await orchestrator.run_quarterly_update()
    
    if result["success"]:
        print("✅ Quarterly update completed successfully!")
        print(f"📊 Processed {result['companies_processed']} companies")
        print(f"📁 Downloaded {result['total_new_pdfs']} new PDFs")
        print(f"📈 Updated {result['total_new_quarters']} new quarters")
    else:
        print("❌ Quarterly update failed!")
        print(f"Error: {result.get('error', 'Unknown error')}")

if __name__ == "__main__":
    asyncio.run(main())
