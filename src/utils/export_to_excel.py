#!/usr/bin/env python3
"""
Excel Export Utility for Financial Analysis Results
Exports retained earnings and reinvested earnings data to formatted Excel files
"""

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from pathlib import Path
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Arabic placeholder for empty / missing cells (must match dashboard expectations)
EXCEL_EMPTY_PLACEHOLDER_AR = "لايوجد"


def _is_effectively_empty_cell(value) -> bool:
    if not value:
        return True
    s = str(value).strip()
    if not s:
        return True
    low = s.lower()
    return low in ("", "null", "undefined", "nan", EXCEL_EMPTY_PLACEHOLDER_AR)


def _format_dashboard_cell(value) -> str:
    """Format one cell for export; mirrors previous inline logic."""
    if _is_effectively_empty_cell(value):
        return EXCEL_EMPTY_PLACEHOLDER_AR
    try:
        num_value = float(str(value).replace(",", ""))
        if num_value != 0:
            return f"{num_value:,.0f}"
        return "0"
    except (ValueError, TypeError):
        return str(value)


def _rtl_row_values(row, headers: list) -> list:
    """One data row, values ordered to match reversed headers (RTL)."""
    return [_format_dashboard_cell(row.get(header, "")) for header in headers]


def _autosize_worksheet_columns(ws) -> None:
    for column in ws.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if cell.value and len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except (TypeError, ValueError, AttributeError):
                pass
        ws.column_dimensions[column_letter].width = min(max_length + 2, 50)


class ExcelExporter:
    def __init__(self, output_dir: str = "output/excel"):
        # Use absolute path for output directory
        project_root = Path(__file__).parent.parent.parent
        self.output_dir = project_root / output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Define styles
        self.header_font = Font(bold=True, color="FFFFFF", size=12)
        self.header_fill = PatternFill(start_color="1e6641", end_color="1e6641", fill_type="solid")
        self.data_font = Font(size=10)
        self.border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        self.center_alignment = Alignment(horizontal='center', vertical='center')
        self.right_alignment = Alignment(horizontal='right', vertical='center')

    def export_dashboard_table(self, data):
        """
        Export only the dashboard table data in a simple format
        """
        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Financial Data"

            headers = list(data.columns)[::-1]

            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col, value=header)
                cell.font = self.header_font
                cell.fill = self.header_fill
                cell.alignment = self.right_alignment
                cell.border = self.border

            for row_idx, (_, row) in enumerate(data.iterrows(), 2):
                row_data = _rtl_row_values(row, headers)
                for col, value in enumerate(row_data, 1):
                    cell = ws.cell(row=row_idx, column=col, value=value)
                    cell.font = self.data_font
                    cell.border = self.border
                    cell.alignment = self.right_alignment

            _autosize_worksheet_columns(ws)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"financial_analysis_{timestamp}.xlsx"
            output_path = self.output_dir / filename

            try:
                wb.save(str(output_path))
                logger.info(f"Dashboard table exported: {output_path}")
                return str(output_path)
            except Exception as save_error:
                logger.error(f"Error saving Excel file: {save_error}")
                return None

        except Exception as e:
            logger.error(f"Error exporting dashboard table: {e}")
            return None

def main():
    """Export current data to Excel"""
    try:
        # Load data
        csv_path = Path("data/results/reinvested_earnings_results.csv")
        if not csv_path.exists():
            print("Error: CSV file not found")
            return
        
        data = pd.read_csv(csv_path)
        print(f"Loaded {len(data)} records")
        
        # Export
        exporter = ExcelExporter()
        output_path = exporter.export_dashboard_table(data)
        
        if output_path:
            print(f"✅ Excel file created: {output_path}")
        else:
            print("❌ Failed to create Excel file")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main() 