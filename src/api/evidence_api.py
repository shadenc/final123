#!/usr/bin/env python3
"""
Flask API for serving evidence screenshots and extraction metadata
"""

from flask import Flask, send_file, jsonify, request, make_response
from flask_cors import CORS
import json
import os
from pathlib import Path
import logging
import subprocess
import sys
from datetime import datetime
import re
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
import shutil
import threading

# Allow `python src/api/evidence_api.py` from repo root (package `src` must be importable).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Get environment variables for production
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*')

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from src.config.reporting_period import reporting_fiscal_year


def _append_glob_evidence_paths(screenshots_dir: Path, pattern: str, paths: list, seen: set) -> None:
    for p in screenshots_dir.glob(pattern):
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            paths.append(p)


def resolve_evidence_screenshot_paths(screenshots_dir: Path, company_symbol: str, quarter: str):
    """
    Find evidence PNGs for a company/quarter key used by the UI (e.g. Q1_2026, Annual_2025).
    Order: exact Q match, then for Q1 also prior-year annual, then any company evidence.
    """
    quarter = (quarter or "").strip()
    paths: list = []
    seen = set()

    m = re.match(r"^Q([1-4])_(\d{4})$", quarter, re.I)
    if m:
        qn, yr = m.group(1), m.group(2)
        _append_glob_evidence_paths(screenshots_dir, f"{company_symbol}_*_q{qn}_{yr}_evidence.png", paths, seen)
        if m.group(1) == "1":
            prev = str(int(yr) - 1)
            _append_glob_evidence_paths(
                screenshots_dir, f"{company_symbol}_*_annual_{prev}_evidence.png", paths, seen
            )
        if not paths:
            _append_glob_evidence_paths(screenshots_dir, f"{company_symbol}_*_evidence.png", paths, seen)
        return paths

    m = re.match(r"^Annual_(\d{4})$", quarter, re.I)
    if m:
        yr = m.group(1)
        _append_glob_evidence_paths(screenshots_dir, f"{company_symbol}_*_annual_{yr}_evidence.png", paths, seen)
        if not paths:
            _append_glob_evidence_paths(screenshots_dir, f"{company_symbol}_*_evidence.png", paths, seen)
        return paths

    _append_glob_evidence_paths(screenshots_dir, f"{company_symbol}_*_evidence.png", paths, seen)
    return paths


# --- Path fragments & messages (deduplicated; Sonar / maintainability) ---
SCREENSHOTS_RELPATH = "output/screenshots"
FLOW_CSV_RELPATH = "data/results/retained_earnings_flow.csv"
RESULTS_JSON_RELPATH = "data/results/retained_earnings_results.json"
REINVESTED_CSV_RELPATH = "data/results/reinvested_earnings_results.csv"
QUARTERLY_NET_PROFIT_RELPATH = "data/results/quarterly_net_profit.json"
RUNTIME_STOP_PDFS_FLAG = "data/runtime/stop_pdfs_pipeline.flag"
RUNTIME_PDFS_PROGRESS_JSON = "data/runtime/pdfs_progress.json"
RUNTIME_STOP_NET_FLAG = "data/runtime/stop_net_profit.flag"
RUNTIME_NET_PROGRESS_JSON = "data/runtime/net_profit_progress.json"
SCRIPT_CALCULATE_REINVESTED = "src/calculators/calculate_reinvested_earnings.py"
# One Playwright job at a time: parallel PDF + net-profit subprocesses can crash Chromium (e.g. headed on macOS).
_PLAYWRIGHT_SCRAPER_LOCK = threading.Lock()
SCRIPT_GENERATE_SCREENSHOTS = "src/utils/generate_evidence_screenshots.py"
MSG_INTERNAL_ERROR = "Internal server error"
MSG_FILE_NOT_FOUND = "File not found"
MSG_OWNERSHIP_UPDATED_OK = "Ownership data updated successfully"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Arabic "not available" for Excel export columns (single literal for Sonar duplicate-string rule)
DISPLAY_NONE_AR = "لايوجد"


def _scheduler_export_quarter_labels(current_month: int, current_year: int):
    """Return (current_quarter, previous_quarter_header, current_quarter_header) for scheduler Excel."""
    if current_month in (1, 2, 3):
        cq, pq = "Q1", "Q4"
        y_for_prior = current_year - 1
    elif current_month in (4, 5, 6):
        cq, pq = "Q2", "Q1"
        y_for_prior = current_year
    elif current_month in (7, 8, 9):
        cq, pq = "Q3", "Q2"
        y_for_prior = current_year
    else:
        cq, pq = "Q4", "Q3"
        y_for_prior = current_year
    if cq == "Q1":
        previous_quarter_header = f"{y_for_prior}Q4"
    else:
        previous_quarter_header = f"{current_year}{pq}"
    current_quarter_header = f"{current_year}{cq}"
    return cq, previous_quarter_header, current_quarter_header


def _scheduler_build_flow_map_from_df(flow_data: pd.DataFrame) -> dict:
    flow_map = {}
    for _, row in flow_data.iterrows():
        symbol = str(row.get("company_symbol", "")).strip()
        quarter = str(row.get("quarter", "")).strip()
        if not symbol or not quarter:
            continue
        if symbol not in flow_map:
            flow_map[symbol] = {}
        flow_map[symbol][quarter] = {
            "previous_value": row.get("previous_value", ""),
            "current_value": row.get("current_value", ""),
            "flow": row.get("flow", ""),
            "flow_formula": row.get("flow_formula", ""),
            "year": row.get("year", ""),
            "reinvested_earnings_flow": row.get("reinvested_earnings_flow", ""),
            "net_profit_foreign_investor": row.get("net_profit_foreign_investor", ""),
            "distributed_profits_foreign_investor": row.get("distributed_profits_foreign_investor", ""),
        }
    return flow_map


def _scheduler_format_export_cell(value) -> str:
    if value == "" or value is None:
        return DISPLAY_NONE_AR
    if value == 0 or (isinstance(value, str) and value.strip() == "0"):
        return "0"
    return value


def _scheduler_net_profit_cell(
    net_profit_info: dict, current_quarter: str, current_year: int
) -> str:
    net_profit_value = DISPLAY_NONE_AR
    if net_profit_info and "quarterly_net_profit" in net_profit_info:
        quarter_key = f"{current_quarter} {current_year}"
        qmap = net_profit_info["quarterly_net_profit"]
        if quarter_key in qmap:
            net_profit_value = qmap[quarter_key]
    return net_profit_value


def _scheduler_merged_row_scheduler_export(
    ownership_row: dict,
    quarter_data: dict,
    net_profit_value: str,
    previous_quarter_header: str,
    current_quarter_header: str,
) -> dict:
    symbol = str(ownership_row.get("symbol", "")).strip()
    return {
        "رمز الشركة": symbol,
        "الشركة": ownership_row.get("company_name", ""),
        "ملكية جميع المستثمرين الأجانب": ownership_row.get("foreign_ownership", ""),
        "الملكية الحالية": ownership_row.get("max_allowed", ""),
        "ملكية المستثمر الاستراتيجي الأجنبي": ownership_row.get("investor_limit", ""),
        f"الأرباح المبقاة للربع السابق ({previous_quarter_header})": _scheduler_format_export_cell(
            quarter_data.get("previous_value", "")
        ),
        f"الأرباح المبقاة للربع الحالي ({current_quarter_header})": _scheduler_format_export_cell(
            quarter_data.get("current_value", "")
        ),
        "حجم الزيادة أو النقص في الأرباح المبقاة (التدفق)": _scheduler_format_export_cell(
            quarter_data.get("flow", "")
        ),
        "تدفق الأرباح المبقاة للمستثمر الأجنبي": _scheduler_format_export_cell(
            quarter_data.get("reinvested_earnings_flow", "")
        ),
        "صافي الربح": net_profit_value,
        "صافي الربح للمستثمر الأجنبي": _scheduler_format_export_cell(
            quarter_data.get("net_profit_foreign_investor", "")
        ),
        "الأرباح الموزعة للمستثمر الأجنبي": _scheduler_format_export_cell(
            quarter_data.get("distributed_profits_foreign_investor", "")
        ),
    }


def _scheduler_archive_quarterly_output(
    project_root: Path,
    output_path,
    csv_path: Path,
    current_year: int,
    current_quarter: str,
) -> None:
    archive_dir = project_root / f"output/archives/{current_year}_{current_quarter}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    archive_excel_name = f"financial_analysis_{current_year}_{current_quarter}.xlsx"
    archive_excel_path = archive_dir / archive_excel_name
    shutil.copy(output_path, archive_excel_path)

    archive_csv_name = f"retained_earnings_flow_{current_year}_{current_quarter}.csv"
    archive_csv_path = archive_dir / archive_csv_name
    shutil.copy(csv_path, archive_csv_path)

    screenshots_archive_dir = archive_dir / "evidence_screenshots"
    screenshots_archive_dir.mkdir(exist_ok=True)

    screenshots_dir = project_root / SCREENSHOTS_RELPATH
    if screenshots_dir.exists():
        quarter_pattern = f"*_{current_quarter.lower()}_{current_year}_evidence.png"
        for screenshot in screenshots_dir.glob(quarter_pattern):
            shutil.copy(screenshot, screenshots_archive_dir / screenshot.name)

    logger.info(f"[Scheduler] Archived results to {archive_dir}")
    logger.info(f"[Scheduler] Excel file: {archive_excel_name}")
    logger.info(f"[Scheduler] CSV file: {archive_csv_name}")
    logger.info("[Scheduler] Evidence screenshots copied")


def run_quarterly_refresh_and_archive(project_root: Path) -> None:
    """
    Quarterly refresh: recalc, screenshots, export, archive.
    Module-level to keep create_app() cognitive complexity low.
    """
    try:
        logger.info("[Scheduler] Running quarterly refresh and archive...")

        logger.info("[Scheduler] Step 1: Recalculating reinvested earnings...")
        subprocess.run(
            [sys.executable, SCRIPT_CALCULATE_REINVESTED],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        logger.info("[Scheduler] Reinvested earnings calculation completed")

        logger.info("[Scheduler] Step 2: Regenerating evidence screenshots...")
        subprocess.run(
            [sys.executable, SCRIPT_GENERATE_SCREENSHOTS],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        logger.info("[Scheduler] Evidence screenshots regeneration completed")

        logger.info("[Scheduler] Step 3: Exporting dashboard table for each quarter...")

        from src.utils.export_to_excel import ExcelExporter

        exporter = ExcelExporter()

        ownership_json_path = project_root / "data/ownership/foreign_ownership_data.json"
        if not ownership_json_path.exists():
            logger.error("[Scheduler] Ownership data file not found")
            return

        with open(ownership_json_path, "r", encoding="utf-8") as f:
            ownership_data = json.load(f)

        csv_path = project_root / FLOW_CSV_RELPATH
        if not csv_path.exists():
            logger.error("[Scheduler] Retained earnings flow data file not found")
            return

        flow_data = pd.read_csv(csv_path)

        net_profit_path = project_root / QUARTERLY_NET_PROFIT_RELPATH
        net_profit_data = {}
        if net_profit_path.exists():
            with open(net_profit_path, "r", encoding="utf-8") as f:
                net_profit_raw = json.load(f)
                for company in net_profit_raw:
                    symbol = company.get("company_symbol")
                    if symbol:
                        net_profit_data[symbol] = company

        now = datetime.now()
        current_month, current_year = now.month, now.year
        current_quarter, previous_quarter_header, current_quarter_header = _scheduler_export_quarter_labels(
            current_month, current_year
        )
        logger.info(f"[Scheduler] Current quarter: {current_quarter} {current_year}")
        logger.info(f"[Scheduler] Column headers: prev={previous_quarter_header} curr={current_quarter_header}")

        flow_map = _scheduler_build_flow_map_from_df(flow_data)

        logger.info(f"[Scheduler] Exporting data for {current_quarter} {current_year}...")

        merged_data = []
        for ownership_row in ownership_data:
            symbol = str(ownership_row.get("symbol", "")).strip()
            flow_info = flow_map.get(symbol, {})
            net_profit_info = net_profit_data.get(symbol, {})
            quarter_data = flow_info.get(current_quarter, {})
            net_profit_value = _scheduler_net_profit_cell(
                net_profit_info, current_quarter, current_year
            )
            merged_data.append(
                _scheduler_merged_row_scheduler_export(
                    ownership_row,
                    quarter_data,
                    net_profit_value,
                    previous_quarter_header,
                    current_quarter_header,
                )
            )

        data = pd.DataFrame(merged_data)

        output_path = exporter.export_dashboard_table(data)

        if output_path:
            _scheduler_archive_quarterly_output(
                project_root, output_path, csv_path, current_year, current_quarter
            )
        else:
            logger.error("[Scheduler] Failed to export Excel file")

    except Exception as e:
        logger.error(f"[Scheduler] Error in scheduled refresh: {e}")
        import traceback

        logger.error(f"[Scheduler] Traceback: {traceback.format_exc()}")


def run_daily_ownership_scraper_and_recalc(project_root: Path) -> None:
    """Daily job: ownership JSON + recalc flows."""
    try:
        logger.info("[Scheduler] Running daily ownership update and recalculation...")
        try:
            logger.info("[Scheduler] Step 1: Updating foreign ownership via Tadawul scraper...")
            from src.scrapers.ownership import TadawulOwnershipScraper

            scraper = TadawulOwnershipScraper(base_url="https://www.saudiexchange.sa")
            scraper.scrape_to_files(output_dir=str(project_root / "data/ownership"), debug=False)
            logger.info("[Scheduler] Ownership data updated")
        except Exception as e:
            logger.error(f"[Scheduler] Ownership update failed: {e}")

        try:
            logger.info("[Scheduler] Step 2: Recalculating reinvested earnings flows...")
            subprocess.run(
                [sys.executable, SCRIPT_CALCULATE_REINVESTED],
                check=True,
                capture_output=True,
                text=True,
                cwd=str(project_root),
            )
            logger.info("[Scheduler] Recalculation finished")
        except subprocess.CalledProcessError as e:
            logger.error(f"[Scheduler] Recalculation failed: {e.stderr}")
    except Exception as e:
        logger.error(f"[Scheduler] Unexpected error in daily ownership job: {e}")


def _run_pdfs_pipeline_task(project_root: Path, downloader: Path, extractor: Path) -> None:
    try:
        try:
            stop_flag_file = project_root / RUNTIME_STOP_PDFS_FLAG
            if stop_flag_file.exists():
                stop_flag_file.unlink()
        except Exception:
            pass
        try:
            progress_path = project_root / RUNTIME_PDFS_PROGRESS_JSON
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            with open(progress_path, 'w', encoding='utf-8') as f:
                json.dump({"status": "running", "processed": 0}, f)
        except Exception:
            pass
        logger.info("[Pipeline] Starting hybrid downloader...")
        env = os.environ.copy()
        env.setdefault('STOP_FLAG_FILE', str(project_root / RUNTIME_STOP_PDFS_FLAG))
        env.setdefault('PROGRESS_FILE', str(project_root / RUNTIME_PDFS_PROGRESS_JSON))
        env.setdefault('PLAYWRIGHT_HEADLESS', '1')
        env.setdefault('REPORTING_FISCAL_YEAR', str(reporting_fiscal_year()))
        if sys.platform == 'darwin':
            env.setdefault('PLAYWRIGHT_CHANNEL', 'chrome')
        with _PLAYWRIGHT_SCRAPER_LOCK:
            subprocess.run([sys.executable, str(downloader)], cwd=str(project_root), check=True, text=True, env=env)
    except subprocess.CalledProcessError as e:
        logger.error(f"[Pipeline] Downloader failed: {e}")
        return
    stop_flag_file = project_root / RUNTIME_STOP_PDFS_FLAG
    try:
        if stop_flag_file.exists():
            logger.info(
                "[Pipeline] Stop was requested during download; clearing flag and running "
                "extract → calculate → screenshots on PDFs already saved."
            )
            stop_flag_file.unlink()
    except Exception as e:
        logger.warning(f"[Pipeline] Could not clear stop flag before extract: {e}")
    try:
        logger.info("[Pipeline] Starting retained earnings extractor...")
        subprocess.run([sys.executable, str(extractor)], cwd=str(project_root), check=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"[Pipeline] Extractor failed: {e}")
        return
    try:
        logger.info("[Pipeline] Recalculating reinvested earnings...")
        calc = project_root / SCRIPT_CALCULATE_REINVESTED
        subprocess.run([sys.executable, str(calc)], cwd=str(project_root), check=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"[Pipeline] Calculation failed: {e}")
        return
    try:
        logger.info("[Pipeline] Regenerating evidence screenshots...")
        shots = project_root / SCRIPT_GENERATE_SCREENSHOTS
        subprocess.run([sys.executable, str(shots)], cwd=str(project_root), check=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.warning(f"[Pipeline] Screenshot regeneration failed: {e}")
    try:
        progress_path = project_root / RUNTIME_PDFS_PROGRESS_JSON
        with open(progress_path, 'w', encoding='utf-8') as f:
            json.dump({"status": "completed"}, f)
    except Exception:
        pass
    try:
        stop_flag_file = project_root / RUNTIME_STOP_PDFS_FLAG
        if stop_flag_file.exists():
            stop_flag_file.unlink()
    except Exception:
        pass
    logger.info("[Pipeline] Pipeline completed (download → extract → calculate → screenshots)")


def _write_combined_progress_both(project_root: Path, payload: dict) -> None:
    for rel in (RUNTIME_PDFS_PROGRESS_JSON, RUNTIME_NET_PROGRESS_JSON):
        try:
            p = project_root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception:
            pass


def _combined_try_unlink_stop_flags(project_root: Path) -> None:
    try:
        for rel in (RUNTIME_STOP_PDFS_FLAG, RUNTIME_STOP_NET_FLAG):
            p = project_root / rel
            if p.exists():
                p.unlink()
    except Exception:
        pass


def _combined_write_running_progress(project_root: Path) -> None:
    try:
        for rel in (RUNTIME_PDFS_PROGRESS_JSON, RUNTIME_NET_PROGRESS_JSON):
            p = project_root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"status": "running", "processed": 0, "mode": "combined"}, f)
    except Exception:
        pass


def _combined_playwright_env(project_root: Path) -> dict:
    env = os.environ.copy()
    env.setdefault("STOP_FLAG_FILE", str(project_root / RUNTIME_STOP_PDFS_FLAG))
    env.setdefault("STOP_FLAG_FILE_NET", str(project_root / RUNTIME_STOP_NET_FLAG))
    env.setdefault("PROGRESS_FILE", str(project_root / RUNTIME_PDFS_PROGRESS_JSON))
    env.setdefault("PROGRESS_FILE_NET", str(project_root / RUNTIME_NET_PROGRESS_JSON))
    env.setdefault("PLAYWRIGHT_HEADLESS", "1")
    env.setdefault("REPORTING_FISCAL_YEAR", str(reporting_fiscal_year()))
    if sys.platform == "darwin":
        env.setdefault("PLAYWRIGHT_CHANNEL", "chrome")
    return env


def _combined_clear_stops_before_extract(project_root: Path, pdf_stop: Path, net_stop: Path) -> None:
    for stop in (pdf_stop, net_stop):
        try:
            if stop.exists():
                logger.info("[Combined] Clearing stop flag before extract...")
                stop.unlink()
        except Exception as e:
            logger.warning(f"[Combined] Could not clear stop flag before extract: {e}")


def _combined_run_tadawul_pipeline(project_root: Path, combined_script: Path, env: dict) -> bool:
    try:
        logger.info("[Combined] Starting tadawul pipeline (one visit per company)...")
        with _PLAYWRIGHT_SCRAPER_LOCK:
            subprocess.run(
                [sys.executable, str(combined_script)],
                cwd=str(project_root),
                check=True,
                text=True,
                env=env,
            )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"[Combined] Tadawul pipeline failed: {e}")
        _write_combined_progress_both(
            project_root,
            {"status": "error", "mode": "combined", "message": "tadawul_pipeline_failed"},
        )
        return False


def _combined_run_extractor_subprocess(project_root: Path, extractor: Path) -> bool:
    try:
        logger.info("[Combined] Starting retained earnings extractor...")
        subprocess.run([sys.executable, str(extractor)], cwd=str(project_root), check=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"[Combined] Extractor failed: {e}")
        _write_combined_progress_both(
            project_root,
            {"status": "error", "mode": "combined", "message": "extract_failed"},
        )
        return False


def _combined_run_calc_subprocess(project_root: Path) -> bool:
    try:
        logger.info("[Combined] Recalculating reinvested earnings...")
        calc = project_root / SCRIPT_CALCULATE_REINVESTED
        subprocess.run([sys.executable, str(calc)], cwd=str(project_root), check=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"[Combined] Calculation failed: {e}")
        _write_combined_progress_both(
            project_root,
            {"status": "error", "mode": "combined", "message": "calculation_failed"},
        )
        return False


def _combined_run_screenshots_subprocess(project_root: Path) -> None:
    try:
        logger.info("[Combined] Regenerating evidence screenshots...")
        shots = project_root / SCRIPT_GENERATE_SCREENSHOTS
        subprocess.run([sys.executable, str(shots)], cwd=str(project_root), check=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.warning(f"[Combined] Screenshot regeneration failed: {e}")


def _combined_unlink_stops_quiet(pdf_stop: Path, net_stop: Path) -> None:
    try:
        for stop in (pdf_stop, net_stop):
            if stop.exists():
                stop.unlink()
    except Exception:
        pass


def _run_combined_pipeline_task(project_root: Path) -> None:
    combined_script = project_root / "src/scrapers/combined_tadawul_pipeline.py"
    extractor = project_root / "src/extractors/extract_retained_earnings_all_pdfs.py"
    if not combined_script.exists() or not extractor.exists():
        logger.error("[Combined] Required script missing")
        return

    _combined_try_unlink_stop_flags(project_root)
    _combined_write_running_progress(project_root)
    env = _combined_playwright_env(project_root)

    if not _combined_run_tadawul_pipeline(project_root, combined_script, env):
        return

    _write_combined_progress_both(project_root, {"status": "finalizing", "mode": "combined"})

    pdf_stop = project_root / RUNTIME_STOP_PDFS_FLAG
    net_stop = project_root / RUNTIME_STOP_NET_FLAG
    _combined_clear_stops_before_extract(project_root, pdf_stop, net_stop)

    if not _combined_run_extractor_subprocess(project_root, extractor):
        return
    if not _combined_run_calc_subprocess(project_root):
        return

    _combined_run_screenshots_subprocess(project_root)

    _write_combined_progress_both(project_root, {"status": "completed", "mode": "combined"})
    _combined_unlink_stops_quiet(pdf_stop, net_stop)
    logger.info("[Combined] Done (tadawul → extract → calculate → screenshots)")


def _run_net_profit_background_task(project_root: Path, scraper: Path) -> None:
    try:
        logger.info("[NetProfit] Starting scraper...")
        try:
            net_stop_flag = project_root / RUNTIME_STOP_NET_FLAG
            if net_stop_flag.exists():
                net_stop_flag.unlink()
        except Exception:
            pass
        try:
            net_progress = project_root / RUNTIME_NET_PROGRESS_JSON
            net_progress.parent.mkdir(parents=True, exist_ok=True)
            with open(net_progress, 'w', encoding='utf-8') as f:
                json.dump({"status": "running", "processed": 0}, f)
        except Exception:
            pass
        env = os.environ.copy()
        env.setdefault('STOP_FLAG_FILE', str(project_root / RUNTIME_STOP_NET_FLAG))
        env.setdefault('PROGRESS_FILE', str(project_root / RUNTIME_NET_PROGRESS_JSON))
        env.setdefault('PLAYWRIGHT_HEADLESS', '1')
        if sys.platform == 'darwin':
            env.setdefault('PLAYWRIGHT_CHANNEL', 'chrome')
        with _PLAYWRIGHT_SCRAPER_LOCK:
            subprocess.run([sys.executable, str(scraper)], cwd=str(project_root), check=True, text=True, env=env)
    except subprocess.CalledProcessError as e:
        logger.error(f"[NetProfit] Scraper failed: {e}")
        return
    try:
        logger.info("[NetProfit] Recalculating flows after net profit update...")
        calc = project_root / SCRIPT_CALCULATE_REINVESTED
        subprocess.run([sys.executable, str(calc)], cwd=str(project_root), check=True, text=True)
        logger.info("[NetProfit] Completed")
    except subprocess.CalledProcessError as e:
        logger.error(f"[NetProfit] Recalculation failed: {e}")
    finally:
        try:
            net_stop_flag = project_root / RUNTIME_STOP_NET_FLAG
            if net_stop_flag.exists():
                net_stop_flag.unlink()
        except Exception:
            pass


def _attach_quarterly_scheduler(project_root: Path) -> None:
    """Cron jobs (module-level to keep create_app cognitive complexity low)."""
    if os.environ.get("WERKZEUG_RUN_MAIN", "true") != "true":
        return
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        run_quarterly_refresh_and_archive,
        "cron",
        month="3,6,9,12",
        day="last",
        hour=23,
        minute=59,
        args=[project_root],
        id="quarterly_refresh_and_archive",
        replace_existing=True,
    )

    scheduler.add_job(
        run_quarterly_refresh_and_archive,
        "cron",
        hour=2,
        minute=0,
        args=[project_root],
        id="daily_test_archive",
        replace_existing=True,
    )

    scheduler.add_job(
        run_daily_ownership_scraper_and_recalc,
        "cron",
        hour=3,
        minute=0,
        args=[project_root],
        id="daily_ownership_and_recalc",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("[Scheduler] Quarterly scheduler started successfully")
    logger.info("[Scheduler] Will run at end of each quarter (Mar 31, Jun 30, Sep 30, Dec 31)")
    logger.info("[Scheduler] Daily test run at 2 AM for development")
    logger.info("[Scheduler] Daily ownership update scheduled at 03:00")


def create_app():
    from src.api.evidence_routes import EvidenceRouteContext, register_evidence_api_routes

    # CSRF / Flask-WTF: Vanilla Flask does not register CSRF middleware—nothing is "disabled".
    # This API is not cookie-session authenticated (SPA fetch is typically cross-origin without
    # credentials), so classic browser CSRF against this app's cookies does not apply. CORS only
    # affects which origins may read responses; protect sensitive POST /api/* with network access
    # control or API auth in production.
    app = Flask(__name__)
    # Allow CORS from React frontend - supports both localhost and production
    allowed_list = (
        [o.strip() for o in ALLOWED_ORIGINS.split(",")]
        if ALLOWED_ORIGINS != "*"
        else "*"
    )
    CORS(app, origins=allowed_list)

    project_root = Path(__file__).parent.parent.parent.resolve()
    ctx = EvidenceRouteContext(project_root)
    register_evidence_api_routes(app, ctx)
    _attach_quarterly_scheduler(project_root)
    return app



# Create app instance for Gunicorn
app = create_app()

# Ensure directories exist for production
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
(PROJECT_ROOT / SCREENSHOTS_RELPATH).mkdir(parents=True, exist_ok=True)
(PROJECT_ROOT / "data/results").mkdir(parents=True, exist_ok=True)
(PROJECT_ROOT / "data/pdfs").mkdir(parents=True, exist_ok=True)

if __name__ == '__main__':
    print("Starting Evidence API server...")
    print(f"Screenshots directory: {PROJECT_ROOT / SCREENSHOTS_RELPATH}")
    print("API will be available at: http://localhost:5003")
    
    app.run(debug=True, host='0.0.0.0', port=5003) 