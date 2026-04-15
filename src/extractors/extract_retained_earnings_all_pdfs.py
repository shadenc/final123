#!/usr/bin/env python3
"""
Simple Retained Earnings Extraction from Financial Statement PDFs
Focused on extracting only retained earnings values with minimal complexity
"""

import fitz  # PyMuPDF
import re
import json
from pathlib import Path
import sqlite3
from datetime import datetime
import openai
import os
from typing import Dict, List, Optional, Tuple
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# OpenAI API key: set OPENAI_API_KEY in the environment or in a project-root .env file
openai.api_key = os.getenv("OPENAI_API_KEY")

class EvidenceScreenshotGenerator:
    """Simple screenshot generator for evidence"""
    
    def generate_highlight_screenshot(self, pdf_path: str, search_value: str, _company_symbol: str) -> Optional[str]:
        """Generate screenshot highlighting the found value and unit text on the same page"""
        try:
            import fitz
            
            doc = fitz.open(pdf_path)
            screenshots_dir = Path("output/screenshots")
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            
            # Get PDF filename for unique naming
            pdf_filename = Path(pdf_path).stem  # Remove .pdf extension
            
            # Find page with the search value and highlight it
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                if search_value in text:
                    # Found the page, now highlight the value
                    page = self._highlight_value_on_page(page, search_value)
                    
                    # Also try to highlight the unit declaration on the same page
                    try:
                        self._highlight_units_on_page(page, text)
                    except Exception as e:
                        logger.warning(f"Unit highlight failed: {e}")
                    
                    # Take screenshot with highlighting - use unique filename
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better quality
                    screenshot_path = screenshots_dir / f"{company_symbol}_{pdf_filename}_evidence.png"
                    pix.save(str(screenshot_path))
                    doc.close()
                    return str(screenshot_path)
            
            doc.close()
            return None
            
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
            return None
    
    def _highlight_value_on_page(self, page, search_value: str):
        """Highlight the found value on the page with a yellow highlighter effect"""
        try:
            # Search for the text on the page
            text_instances = page.search_for(search_value)
            
            if text_instances:
                # Get the first instance and highlight it
                rect = text_instances[0]  # First occurrence
                
                # Draw a yellow highlighter effect around the found text
                highlight_rect = page.add_rect_annot(rect)
                highlight_rect.set_colors(stroke=(1, 1, 0))  # Yellow stroke
                highlight_rect.set_colors(fill=(1, 1, 0))    # Yellow fill
                highlight_rect.set_opacity(0.3)  # Semi-transparent yellow
                
                logger.info(f"Highlighted value '{search_value}' on page with yellow highlighter")
            
            return page
            
        except Exception as e:
            logger.error(f"Highlighting error: {e}")
            return page

    def _highlight_units_on_page(self, page, _page_text: str) -> None:
        """Attempt to find and highlight unit declaration text on the page.
        Draw a second rectangle (green) around the first matching unit phrase.
        """
        # Candidate phrases (simple contains search via page.search_for)
        unit_phrases = [
            # English
            "in millions of saudi riyals",
            "in million of saudi riyals",
            "in millions",
            "in million",
            "millions of saudi riyals",
            "in thousands of saudi riyals",
            "in thousand of saudi riyals",
            "in thousands",
            "in thousand",
            "thousands of saudi riyals",
            "saudi riyals",
            "SAR",
            # Arabic
            "بالملايين",
            "الملايين",
            "مليون",
            "بالآلاف",
            "بالالاف",
            "ألف",
            "الآلاف",
            "بالريال السعودي",
            "ريال سعودي",
            "ريال"
        ]
        # Try longer phrases first for specificity
        unit_phrases.sort(key=len, reverse=True)
        for phrase in unit_phrases:
            areas = page.search_for(phrase, quads=False)
            if areas:
                rect = areas[0]
                try:
                    unit_rect = page.add_rect_annot(rect)
                    # Differentiate color from value highlight (green)
                    unit_rect.set_colors(stroke=(0, 1, 0))
                    unit_rect.set_colors(fill=(0, 1, 0))
                    unit_rect.set_opacity(0.25)
                    logger.info(f"Highlighted unit phrase '{phrase}' on page")
                except Exception as e:
                    logger.warning(f"Failed to draw unit rectangle: {e}")
                break

RETAINED_EARNINGS_LABEL = "retained earnings"


class RetainedEarningsExtractor:
    def __init__(self):
        self.target_years = []
        self.most_recent_year = None
    
    def detect_years(self, text: str) -> List[int]:
        """Detect available years in the financial statement"""
        current_year = datetime.now().year
        
        # Look for 4-digit years (2020-2030 range)
        year_pattern = r'\b(20[2-3][0-9])\b'
        years_found = re.findall(year_pattern, text)
        
        # Convert to integers and filter realistic years
        realistic_years = []
        for year in set(int(y) for y in years_found):
            if current_year - 10 <= year <= current_year + 1:
                realistic_years.append(year)
        
        # Sort by most recent first
        realistic_years.sort(reverse=True)
        self.target_years = realistic_years
        self.most_recent_year = realistic_years[0] if realistic_years else None
        
        logger.info(f"Detected years: {realistic_years}")
        return realistic_years

    # --- New: Unit detection helpers ---
    def _detect_units_from_text(self, text: str) -> Dict[str, object]:
        """Detect unit declarations in nearby text. Returns dict with unit and multiplier."""
        try:
            lowered = text.lower()
            # English patterns
            english_million = re.search(r"all\s+amounts?.*in\s+millions?\s+of\s+saudi\s+riyals|in\s+millions\b|millions\s+of\s+saudi\s+riyals", lowered)
            english_thousand = re.search(r"all\s+amounts?.*in\s+thousands?\s+of\s+saudi\s+riyals|in\s+thousands\b|thousands\s+of\s+saudi\s+riyals", lowered)
            english_sar = re.search(r"saudi\s+riyals?|\bSAR\b", lowered)
            
            # Arabic patterns (approximate common variants)
            arabic_million = re.search(r"بالملايين|\bمليون\b|\bالملايين\b", text)
            arabic_thousand = re.search(r"بال[اآ]لاف|\bألف\b|\bال[اآ]لاف\b", text)
            arabic_sar = re.search(r"بالريال\s+السعودي|\bريال\b", text)
            
            if english_million or arabic_million:
                return { 'unit_detected': 'million_SAR', 'applied_multiplier': 1_000_000 }
            if english_thousand or arabic_thousand:
                return { 'unit_detected': 'thousand_SAR', 'applied_multiplier': 1_000 }
            if english_sar or arabic_sar:
                return { 'unit_detected': 'SAR', 'applied_multiplier': 1 }
            
            # Default when nothing explicit found
            return { 'unit_detected': 'unknown', 'applied_multiplier': 1 }
        except Exception as e:
            logger.warning(f"Unit detection error: {e}")
            return { 'unit_detected': 'unknown', 'applied_multiplier': 1 }

    def _find_page_for_value(self, pdf_path: str, search_value: str) -> Optional[int]:
        """Find the first page (1-based) that contains the given search value."""
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                if page.search_for(str(search_value)):
                    doc.close()
                    return page_num + 1
            doc.close()
            return None
        except Exception as e:
            logger.warning(f"Failed to locate page for value '{search_value}': {e}")
            return None

    def _detect_units_for_pdf(self, pdf_path: str, page_num: Optional[int] = None, search_value: Optional[str] = None) -> Dict[str, object]:
        """
        Detect units by reading text from a specific page if provided; otherwise, try to locate
        the page via the search_value. Falls back to first page if needed.
        """
        try:
            doc = fitz.open(pdf_path)
            target_page_index = None
            if page_num is not None and 1 <= page_num <= len(doc):
                target_page_index = page_num - 1
            elif search_value is not None:
                for p in range(len(doc)):
                    if doc[p].search_for(str(search_value)):
                        target_page_index = p
                        break
            
            # Fallback to first page if not found
            if target_page_index is None:
                target_page_index = 0
            
            page_text = doc[target_page_index].get_text()
            doc.close()
            return self._detect_units_from_text(page_text)
        except Exception as e:
            logger.warning(f"Failed to detect units for PDF: {e}")
            return { 'unit_detected': 'unknown', 'applied_multiplier': 1 }

    def _spire_match_retained_column(
        self,
        table,
        retained_row_index: int,
        col_index: int,
        page_index: int,
        pdf_path: str,
        year: int,
    ) -> Optional[Dict]:
        cell_data = table.GetText(retained_row_index, col_index).strip()
        if str(year) not in cell_data:
            return None
        for row_idx in range(table.GetRowCount()):
            value_cell = table.GetText(row_idx, col_index).strip()
            if not value_cell or not value_cell.replace(',', '').isdigit():
                continue
            numeric_value = float(value_cell.replace(',', ''))
            if numeric_value < 10000:
                continue
            units = self._detect_units_for_pdf(
                pdf_path, page_num=page_index + 1, search_value=value_cell
            )
            scaled_value = numeric_value * units['applied_multiplier']
            return {
                'success': True,
                'value': value_cell,
                'numeric_value': scaled_value,
                'method': 'spire_pdf',
                'year': year,
                'page': page_index + 1,
                'unit_detected': units['unit_detected'],
                'applied_multiplier': units['applied_multiplier'],
            }
        return None

    def _spire_scan_table(self, table, page_index: int, pdf_path: str) -> Optional[Dict]:
        for row_index in range(table.GetRowCount()):
            first_col = table.GetText(row_index, 0).strip().lower()
            if first_col != RETAINED_EARNINGS_LABEL:
                continue
            for year in self.target_years:
                for col_index in range(table.GetColumnCount()):
                    hit = self._spire_match_retained_column(
                        table, row_index, col_index, page_index, pdf_path, year
                    )
                    if hit:
                        return hit
        return None

    def extract_with_spire_pdf(self, pdf_path: str) -> Optional[Dict]:
        """Extract using Spire.PDF if available"""
        try:
            from spire.pdf import PdfDocument, PdfTableExtractor
        except ImportError:
            return None

        try:
            doc = PdfDocument()
            doc.LoadFromFile(pdf_path)
            extractor = PdfTableExtractor(doc)

            for page_index in range(doc.Pages.Count):
                tables = extractor.ExtractTable(page_index)
                if not tables:
                    continue
                for table in tables:
                    hit = self._spire_scan_table(table, page_index, pdf_path)
                    if hit:
                        doc.Close()
                        return hit
            doc.Close()
            return None
        except Exception as e:
            logger.error(f"Spire.PDF error: {e}")
            return None

    def _camelot_numeric_hit(
        self, pdf_path: str, df, year: int, col_idx: int
    ) -> Optional[Dict]:
        for row_idx in range(len(df)):
            value = df.iloc[row_idx, col_idx]
            if not value or not str(value).replace(',', '').isdigit():
                continue
            numeric_value = float(str(value).replace(',', ''))
            if numeric_value < 10000:
                continue
            page_num = self._find_page_for_value(pdf_path, str(value))
            units = self._detect_units_for_pdf(
                pdf_path, page_num=page_num, search_value=str(value)
            )
            scaled_value = numeric_value * units['applied_multiplier']
            return {
                'success': True,
                'value': str(value),
                'numeric_value': scaled_value,
                'method': 'camelot',
                'year': year,
                'page': page_num if page_num else 1,
                'unit_detected': units['unit_detected'],
                'applied_multiplier': units['applied_multiplier'],
            }
        return None

    def _camelot_scan_retained_row(
        self, pdf_path: str, df, row
    ) -> Optional[Dict]:
        if RETAINED_EARNINGS_LABEL not in str(row.iloc[0]).lower():
            return None
        for year in self.target_years:
            for col_idx, col_name in enumerate(df.columns):
                if str(year) not in str(col_name):
                    continue
                hit = self._camelot_numeric_hit(pdf_path, df, year, col_idx)
                if hit:
                    return hit
        return None

    def _camelot_scan_dataframe(self, pdf_path: str, df) -> Optional[Dict]:
        for _, row in df.iterrows():
            hit = self._camelot_scan_retained_row(pdf_path, df, row)
            if hit:
                return hit
        return None

    def extract_with_camelot(self, pdf_path: str) -> Optional[Dict]:
        """Extract using Camelot if available"""
        try:
            import camelot
        except ImportError:
            return None

        try:
            tables = camelot.read_pdf(pdf_path, flavor="stream")
            for table in tables:
                hit = self._camelot_scan_dataframe(pdf_path, table.df)
                if hit:
                    return hit
            return None
        except Exception as e:
            logger.error(f"Camelot error: {e}")
            return None

    def _fitz_extractable_char_count(self, pdf_path: str) -> int:
        """Total character count from embedded text (scanned PDFs ~0)."""
        try:
            doc = fitz.open(pdf_path)
            n = sum(len(doc[i].get_text()) for i in range(len(doc)))
            doc.close()
            return n
        except Exception:
            return 0

    def _retained_hit_from_number_string(
        self,
        number: str,
        pdf_path: str,
        method: str,
        page_override: Optional[int],
        unit_source_text: Optional[str],
    ) -> Optional[Dict]:
        clean_value = number.replace(",", "")
        if not clean_value.isdigit():
            return None
        numeric_value = float(clean_value)
        if numeric_value < 10000 or numeric_value in self.target_years:
            return None
        page_num = (
            page_override
            if page_override is not None
            else self._find_page_for_value(pdf_path, number)
        )
        if unit_source_text is not None:
            units = self._detect_units_from_text(unit_source_text)
        else:
            units = self._detect_units_for_pdf(
                pdf_path, page_num=page_num, search_value=number
            )
        scaled_value = numeric_value * units["applied_multiplier"]
        return {
            "success": True,
            "value": number,
            "numeric_value": scaled_value,
            "method": method,
            "year": self.most_recent_year,
            "page": page_num if page_num else 1,
            "unit_detected": units["unit_detected"],
            "applied_multiplier": units["applied_multiplier"],
        }

    def _find_first_retained_value_in_text(
        self,
        text: str,
        pdf_path: str,
        method: str,
        page_override: Optional[int] = None,
        unit_source_text: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Find 'retained earnings' and the first large numeric in the same line + following lines.
        Requires self.target_years / self.most_recent_year already set by detect_years().
        """
        if not self.target_years:
            return None
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if RETAINED_EARNINGS_LABEL not in line.lower():
                continue
            window_lines = lines[i : min(len(lines), i + 12)]
            window_text = "\n".join(window_lines)
            for number in re.findall(r"([\d,]+)", window_text):
                hit = self._retained_hit_from_number_string(
                    number, pdf_path, method, page_override, unit_source_text
                )
                if hit:
                    return hit
        return None

    def extract_with_regex(self, pdf_path: str) -> Optional[Dict]:
        """Extract using embedded PDF text (PyMuPDF) + regex."""
        try:
            doc = fitz.open(pdf_path)
            text = "".join(page.get_text() for page in doc)
            doc.close()

            self.detect_years(text)
            if not self.target_years:
                return None
            return self._find_first_retained_value_in_text(text, pdf_path, "regex")
        except Exception as e:
            logger.error(f"Regex error: {e}")
            return None

    def extract_with_ocr(self, pdf_path: str) -> Optional[Dict]:
        """
        Fallback for scanned PDFs (no text layer): render pages and run Tesseract.
        Requires tesseract on PATH and pytesseract + Pillow.
        """
        try:
            import io

            import pytesseract
            from PIL import Image
        except ImportError:
            logger.warning("OCR skipped: install pytesseract and Pillow")
            return None
        try:
            pytesseract.get_tesseract_version()
        except Exception as e:
            logger.warning(f"OCR skipped: Tesseract not usable ({e})")
            return None

        try:
            doc = fitz.open(pdf_path)
            page_texts: List[str] = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                txt = pytesseract.image_to_string(img, lang="eng+ara")
                page_texts.append(txt)
            doc.close()
        except Exception as e:
            logger.warning(f"OCR render/read failed: {e}")
            return None

        full_text = "\n".join(page_texts)
        self.detect_years(full_text)
        if not self.target_years:
            logger.info("OCR: no calendar years found in OCR text")
            return None

        unit_blob = full_text[:8000]
        for idx, text in enumerate(page_texts):
            if RETAINED_EARNINGS_LABEL not in text.lower():
                continue
            merged_units = (text + "\n" + unit_blob)[:12000]
            result = self._find_first_retained_value_in_text(
                text,
                pdf_path,
                "ocr",
                page_override=idx + 1,
                unit_source_text=merged_units,
            )
            if result:
                logger.info(f"Retained earnings found via OCR on page {idx + 1}")
                return result
        return None

    def extract_retained_earnings(self, pdf_path: str) -> Dict:
        """Main extraction method with fallback chain"""
        logger.info(f"Processing: {pdf_path}")
        
        # Try Spire.PDF first (most reliable)
        result = self.extract_with_spire_pdf(pdf_path)
        if result:
            return result
        
        # Try Camelot
        result = self.extract_with_camelot(pdf_path)
        if result:
            return result
        
        # Try regex on embedded text
        result = self.extract_with_regex(pdf_path)
        if result:
            return result

        # Scanned PDFs: almost no extractable text — OCR (Tesseract)
        if self._fitz_extractable_char_count(pdf_path) < 200:
            result = self.extract_with_ocr(pdf_path)
            if result:
                return result

        return {
            'success': False,
            'error': 'No retained earnings found using any method'
        }

def get_company_symbol_from_filename(filename):
    """Extract company symbol from PDF filename"""
    return filename.split('_')[0]

def save_to_database(results):
    """Save results to SQLite database"""
    conn = sqlite3.connect('data/financial_analysis.db')
    cursor = conn.cursor()
    
    cursor.execute('DROP TABLE IF EXISTS retained_earnings')
    cursor.execute('''
        CREATE TABLE retained_earnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_symbol TEXT,
            pdf_filename TEXT,
            retained_earnings_value REAL,
            year INTEGER,
            method TEXT,
            extraction_date TIMESTAMP
        )
    ''')
    
    for result in results:
        if result.get('success'):
            cursor.execute('''
                INSERT INTO retained_earnings 
                (company_symbol, pdf_filename, retained_earnings_value, year, method, extraction_date)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                result['company_symbol'],
                result['pdf_filename'],
                result.get('numeric_value'),
                result.get('year'),
                result.get('method'),
                datetime.now()
            ))
    
    conn.commit()
    conn.close()


def _persist_partial_retained_results(results: List) -> None:
    try:
        results_dir = Path("data/results")
        results_dir.mkdir(parents=True, exist_ok=True)
        output_file_tmp = results_dir / "retained_earnings_results.partial.json"
        with open(output_file_tmp, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _extraction_stop_requested(stop_flag_file: str) -> bool:
    try:
        return os.path.exists(stop_flag_file)
    except Exception:
        return False


def _process_one_pdf_extraction(
    extractor: RetainedEarningsExtractor,
    evidence_generator: EvidenceScreenshotGenerator,
    pdf_file: Path,
    pdf_index: int,
    total_pdfs: int,
) -> Tuple[Dict, int]:
    print(f"\n[{pdf_index}/{total_pdfs}] Processing: {pdf_file.name}")
    company_symbol = get_company_symbol_from_filename(pdf_file.name)
    result = extractor.extract_retained_earnings(str(pdf_file))
    result['company_symbol'] = company_symbol
    result['pdf_filename'] = pdf_file.name

    if not result['success']:
        print(f"   Error: {result.get('error', 'Unknown error')}")
        return result, 0

    print(f"   Found: {result['value']} (Year: {result['year']})")
    print(f"   Method: {result['method']}")
    try:
        print("   Generating evidence screenshot...")
        screenshot_path = evidence_generator.generate_highlight_screenshot(
            str(pdf_file), result['value'], company_symbol
        )
        if screenshot_path:
            print(f"   Evidence screenshot saved: {screenshot_path}")
        else:
            print("   Failed to generate evidence screenshot")
    except Exception as e:
        print(f"   Error generating evidence screenshot: {e}")
    return result, 1


def main():
    pdf_dir = Path("data/pdfs")
    pdf_files = [f for f in pdf_dir.glob("*.pdf")]

    if not pdf_files:
        print("No PDF files found in data/pdfs/")
        return

    print(f"Found {len(pdf_files)} PDF files to process")

    extractor = RetainedEarningsExtractor()
    evidence_generator = EvidenceScreenshotGenerator()
    results = []
    successful_extractions = 0

    stop_flag_file = os.environ.get(
        "STOP_FLAG_FILE",
        str(Path("data/runtime/stop_pdfs_pipeline.flag").resolve()),
    )

    for i, pdf_file in enumerate(pdf_files, 1):
        if _extraction_stop_requested(stop_flag_file):
            print(" Stop requested. Ending extraction loop early and saving partial results...")
            break
        result, inc = _process_one_pdf_extraction(
            extractor, evidence_generator, pdf_file, i, len(pdf_files)
        )
        successful_extractions += inc
        results.append(result)
        _persist_partial_retained_results(results)
    
    # Save results
    results_dir = Path("data/results")
    results_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = results_dir / "retained_earnings_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    # Save to database
    save_to_database(results)
    
    # Print summary
    print("\n" + "=" * 50)
    print("EXTRACTION SUMMARY")
    print("=" * 50)
    print(f"Total PDFs processed: {len(pdf_files)}")
    print(f"Successful extractions: {successful_extractions}")
    print(f"Success rate: {successful_extractions/len(pdf_files)*100:.1f}%")
    print(f"Results saved to: {output_file}")
    print("Results also saved to database: data/financial_analysis.db")
    
if __name__ == "__main__":
    main() 