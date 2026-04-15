#!/usr/bin/env python3
"""
Generate Evidence Screenshots for Retained Earnings Extraction
Creates highlighted screenshots showing where values were found in PDFs
"""

import fitz
import json
import os
from pathlib import Path
import logging
from typing import Dict, List, Optional, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_PDfs_ROOT = Path("data/pdfs")


def _safe_pdf_path(pdf_filename: str) -> Optional[Path]:
    """Resolve a PDF path under data/pdfs only (blocks path traversal). Same file when input is a plain basename."""
    if not pdf_filename or not isinstance(pdf_filename, str):
        return None
    root = _PDfs_ROOT.resolve()
    try:
        resolved = (root / pdf_filename).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


class EvidenceScreenshotGenerator:
    def __init__(self, output_dir: str = "output/screenshots"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def find_value_in_pdf(self, pdf_path: str, search_value: str) -> Optional[Tuple[int, fitz.Rect]]:
        """
        Find the exact location of a value in a PDF
        Returns (page_number, rectangle) or None
        """
        try:
            doc = fitz.open(pdf_path)
            
            # Try multiple formats of the search value
            search_variants = [
                str(search_value),  # Original format
                str(search_value).replace(',', ''),  # Without commas
                str(search_value).replace(',', ','),  # With commas
            ]
            
            # Try to add formatted variants
            try:
                numeric_value = float(str(search_value).replace(',', ''))
                search_variants.extend([
                    f"{int(numeric_value):,}",  # Formatted with commas
                    f"{int(numeric_value)}",  # Integer format
                ])
            except ValueError:
                pass
            
            # Remove duplicates while preserving order
            seen = set()
            unique_variants = []
            for variant in search_variants:
                if variant not in seen:
                    seen.add(variant)
                    unique_variants.append(variant)
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # Try each variant
                for variant in unique_variants:
                    areas = page.search_for(variant)
                    if areas:
                        doc.close()
                        logger.info(
                            "Found search match in %s page %s",
                            Path(pdf_path).name,
                            page_num + 1,
                        )
                        return (page_num, areas[0])  # Return first match
            
            doc.close()
            logger.warning("Could not find search value in %s", Path(pdf_path).name)
            return None
            
        except Exception:
            logger.exception("Error searching PDF %s", Path(pdf_path).name)
            return None
    
    def generate_highlight_screenshot(self, pdf_path: str, search_value: str, _company_symbol: str) -> Optional[str]:
        """
        Generate a highlighted screenshot showing where the value was found
        Returns the path to the generated screenshot or None
        """
        try:
            # Find the value location
            result = self.find_value_in_pdf(pdf_path, search_value)
            if not result:
                logger.warning("Could not find search value in %s", Path(pdf_path).name)
                return None
            page_num, rect = result
            # Open PDF and get the page
            doc = fitz.open(pdf_path)
            page = doc[page_num]
            # Add highlight annotation
            highlight = page.add_highlight_annot(rect)
            highlight.set_colors(stroke=(1, 1, 0))  # Yellow highlight
            highlight.set_opacity(0.5)
            # Create pixmap with higher DPI for better quality
            mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for better quality
            pix = page.get_pixmap(matrix=mat, dpi=300)
            # Generate output filename using the PDF base name
            base_name = Path(pdf_path).stem
            output_filename = f"{base_name}_evidence.png"
            output_path = self.output_dir / output_filename
            # Save the screenshot
            pix.save(str(output_path))
            doc.close()
            logger.info("Generated evidence screenshot: %s", output_path.name)
            return str(output_path)
        except Exception:
            logger.exception("Error generating screenshot for %s", Path(pdf_path).name)
            return None
    
    def generate_page_screenshot(self, pdf_path: str, page_number: int, company_symbol: str) -> Optional[str]:
        """
        Generate a screenshot of the specified page (no highlight).
        Returns the path to the generated screenshot or None.
        """
        try:
            doc = fitz.open(pdf_path)
            page = doc[page_number - 1]  # Convert 1-based to 0-based
            mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for better quality
            pix = page.get_pixmap(matrix=mat, dpi=300)
            output_filename = f"{company_symbol}_evidence.png"
            output_path = self.output_dir / output_filename
            pix.save(str(output_path))
            doc.close()
            logger.info("Generated page screenshot: %s", output_path.name)
            return str(output_path)
        except Exception:
            logger.exception("Error generating page screenshot for %s", Path(pdf_path).name)
            return None
    
    def generate_all_evidence_screenshots(self, results_file: str = "data/results/retained_earnings_results.json"):
        """
        Generate evidence screenshots for all successful extractions
        """
        try:
            # Load results
            with open(results_file, 'r', encoding='utf-8') as f:
                results = json.load(f)
            
            successful_extractions = [r for r in results if r['success']]
            logger.info(f"Generating evidence screenshots for {len(successful_extractions)} successful extractions")
            
            generated_screenshots = []
            
            for result in successful_extractions:
                company_symbol = result['company_symbol']
                pdf_filename = result['pdf_filename']
                value = result['value']
                
                resolved_pdf = _safe_pdf_path(pdf_filename)
                if resolved_pdf is None:
                    logger.warning("Invalid or unsafe PDF filename skipped")
                    continue
                pdf_path = str(resolved_pdf)
                
                if not os.path.exists(pdf_path):
                    logger.warning("PDF not found: %s", resolved_pdf.name)
                    continue
                
                screenshot_path = self.generate_highlight_screenshot(
                    pdf_path, value, company_symbol
                )
                
                if screenshot_path:
                    generated_screenshots.append({
                        'company_symbol': company_symbol,
                        'value': value,
                        'screenshot_path': screenshot_path,
                        'pdf_filename': pdf_filename
                    })
            
            # Save metadata about generated screenshots
            metadata_file = self.output_dir / "evidence_metadata.json"
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(generated_screenshots, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Generated {len(generated_screenshots)} evidence screenshots")
            logger.info(f"Metadata saved to: {metadata_file}")
            
            return generated_screenshots
            
        except Exception:
            logger.exception("Error generating evidence screenshots")
            return []

def main():
    """Generate evidence screenshots for all successful extractions"""
    generator = EvidenceScreenshotGenerator()
    screenshots = generator.generate_all_evidence_screenshots()
    
    print("\n" + "=" * 50)
    print("EVIDENCE SCREENSHOT GENERATION SUMMARY")
    print("=" * 50)
    print(f"Generated screenshots: {len(screenshots)}")
    
    if screenshots:
        print("\nGenerated evidence entries (paths under output/screenshots/):")
        for screenshot in screenshots:
            name = Path(screenshot["screenshot_path"]).name
            print(f"  {name}")
    
    print("\nScreenshots saved to: output/screenshots/")
    print("Metadata saved to: output/screenshots/evidence_metadata.json")

if __name__ == "__main__":
    main() 