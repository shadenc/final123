#!/usr/bin/env python3
"""
Calculate Retained Earnings Flow (Quarterly Changes)
Flow = Retained Earnings (Current Q) - Retained Earnings (Previous Q)
"""

import json
import pandas as pd
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Repo root (avoid relying on process cwd when launched from the API or other tools)
_ROOT = Path(__file__).resolve().parent.parent.parent
FLOW_CSV_PATH = _ROOT / "data/results/retained_earnings_flow.csv"
FLOW_JSON_PATH = _ROOT / "data/results/retained_earnings_flow.json"
RETAINED_RESULTS_JSON = _ROOT / "data/results/retained_earnings_results.json"
OWNERSHIP_JSON = _ROOT / "data/ownership/foreign_ownership_data.json"
OWNERSHIP_CSV = _ROOT / "data/ownership/foreign_ownership_data.csv"
QUARTERLY_NET_PROFIT_JSON = _ROOT / "data/results/quarterly_net_profit.json"

_STMT_TYPE_ORDER = {"annual": 0, "q1": 1, "q2": 2, "q3": 3, "q4": 4}


def _fmt_sample_currency(value, decimals: int = 2) -> str:
    """Format for console sample lines; empty strings and NaN must not use float format specifiers."""
    if value is None:
        return "N/A"
    if isinstance(value, str) and value.strip() == "":
        return "N/A"
    try:
        if pd.isna(value):
            return "N/A"
    except TypeError:
        pass
    try:
        return f"{float(value):,.{decimals}f} SAR"
    except (TypeError, ValueError):
        return str(value)


def _lookup_quarterly_net_profit(
    qmap: Dict, quarter: str, year: int
) -> Optional[float]:
    """
    Match frontend App.js lookupQuarterlyNetProfitValue: try focus year and ±1,
    then newest calendar year for that quarter prefix.
    """
    if not qmap:
        return None

    def present(v) -> bool:
        if v is None:
            return False
        if isinstance(v, str) and v.strip() == "":
            return False
        try:
            if isinstance(v, float) and pd.isna(v):
                return False
        except Exception:
            pass
        return True

    for y in (year, year + 1, year - 1):
        key = f"{quarter} {y}"
        if key in qmap and present(qmap[key]):
            try:
                return float(qmap[key])
            except (TypeError, ValueError):
                continue

    prefix = f"{quarter} "
    best_year = -10**9
    best_val: Optional[float] = None
    for k, v in qmap.items():
        if not isinstance(k, str) or not k.startswith(prefix):
            continue
        rest = k[len(prefix) :].strip()
        if not rest.isdigit():
            continue
        y = int(rest)
        if y >= best_year and present(v):
            try:
                best_val = float(v)
                best_year = y
            except (TypeError, ValueError):
                continue
    return best_val


def _find_statement(statements: List[Dict], stype: str, year: int):
    return next((s for s in statements if s["type"] == stype and s["year"] == year), None)


def _append_flow(
    flows: List[Dict],
    quarter: str,
    year: int,
    current: Optional[Dict],
    previous: Optional[Dict],
    formula: str,
) -> None:
    if current is None or previous is None:
        return
    flows.append(
        {
            "quarter": quarter,
            "year": year,
            "current_value": current["value"],
            "previous_value": previous["value"],
            "flow": current["value"] - previous["value"],
            "flow_formula": formula,
        }
    )


def _quarterly_flows_for_year(statements: List[Dict], current_year: int) -> List[Dict]:
    flows: List[Dict] = []
    q1 = _find_statement(statements, "q1", current_year)
    annual_prev = _find_statement(statements, "annual", current_year - 1)
    _append_flow(
        flows,
        "Q1",
        current_year,
        q1,
        annual_prev,
        f"Q1 {current_year} - Annual {current_year - 1}",
    )
    q2 = _find_statement(statements, "q2", current_year)
    _append_flow(
        flows,
        "Q2",
        current_year,
        q2,
        q1,
        f"Q2 {current_year} - Q1 {current_year}",
    )
    q3 = _find_statement(statements, "q3", current_year)
    _append_flow(
        flows,
        "Q3",
        current_year,
        q3,
        q2,
        f"Q3 {current_year} - Q2 {current_year}",
    )
    q4 = _find_statement(statements, "q4", current_year)
    _append_flow(
        flows,
        "Q4",
        current_year,
        q4,
        q3,
        f"Q4 {current_year} - Q3 {current_year}",
    )
    return flows


def parse_statement_info(filename: str) -> Dict:
    """Parse PDF filename to extract company, statement type, and year"""
    # Example: 2222_q1_2025.pdf -> company: 2222, type: q1, year: 2025
    # Example: 2382_annual_2024.pdf -> company: 2382, type: annual, year: 2024
    
    parts = filename.replace('.pdf', '').split('_')
    if len(parts) >= 3:
        company = parts[0]
        statement_type = parts[1]
        year = int(parts[2])
        
        return {
            'company': company,
            'type': statement_type,
            'year': year
        }
    return None

def calculate_retained_earnings_flow(retained_data: List[Dict]) -> List[Dict]:
    """Calculate quarterly flow of retained earnings"""
    
    # Group by company
    companies = {}
    for item in retained_data:
        if not item.get('success'):
            continue
            
        company = item['company_symbol']
        if company not in companies:
            companies[company] = []
        
        # Parse statement info
        info = parse_statement_info(item['pdf_filename'])
        if not info:
            continue
            
        companies[company].append({
            'type': info['type'],
            'year': info['year'],
            'value': item['numeric_value'],
            'pdf_filename': item['pdf_filename']
        })
    
    flow_results = []
    
    for company, statements in companies.items():
        statements.sort(key=lambda x: (x["year"], _STMT_TYPE_ORDER.get(x["type"], 999)))
        if not statements:
            continue
        current_year = max(s["year"] for s in statements)
        for flow in _quarterly_flows_for_year(statements, current_year):
            flow_results.append(
                {
                    "company_symbol": company,
                    "quarter": flow["quarter"],
                    "year": flow["year"],
                    "current_value": flow["current_value"],
                    "previous_value": flow["previous_value"],
                    "flow": flow["flow"],
                    "flow_formula": flow["flow_formula"],
                }
            )
    
    return flow_results


def main():
    """Main function to calculate retained earnings flow"""
    print("🔄 Calculating Retained Earnings Flow (Quarterly Changes)")
    print("=" * 60)
    
    # Load retained earnings data
    try:
        with open(RETAINED_RESULTS_JSON, 'r', encoding='utf-8') as f:
            retained_data = json.load(f)
        print(f"✅ Loaded {len(retained_data)} retained earnings records")
    except FileNotFoundError:
        print("❌ Error: retained_earnings_results.json not found")
        print("Please run the main extraction script first")
        return
    except Exception as e:
        print(f"❌ Error loading data: {e}")
        return
    
    # Calculate flows
    print("🔄 Calculating quarterly flows...")
    flow_results = calculate_retained_earnings_flow(retained_data)
    
    if not flow_results:
        print("❌ No flows could be calculated")
        return
    
    # Convert to DataFrame for easier manipulation
    flow_df = pd.DataFrame(flow_results)
    
    # Load ownership data for additional context (prefer JSON, fallback to CSV)
    try:
        try:
            with open(OWNERSHIP_JSON, 'r', encoding='utf-8') as f:
                ownership_json = json.load(f)
            ownership_df = pd.DataFrame(ownership_json)
            print(f"✅ Loaded ownership data (JSON) for {len(ownership_df)} companies")
        except FileNotFoundError:
            ownership_df = pd.read_csv(OWNERSHIP_CSV)
            print(f"✅ Loaded ownership data (CSV) for {len(ownership_df)} companies")
        
        # Normalize columns
        if 'symbol' not in ownership_df.columns and 'company_symbol' in ownership_df.columns:
            ownership_df = ownership_df.rename(columns={'company_symbol': 'symbol'})
        
        # Merge with ownership data
        flow_df['company_symbol'] = flow_df['company_symbol'].astype(str)
        ownership_df['symbol'] = ownership_df['symbol'].astype(str)
        
        merged = pd.merge(
            flow_df, 
            ownership_df[['symbol', 'company_name', 'foreign_ownership', 'max_allowed', 'investor_limit']], 
            left_on='company_symbol', 
            right_on='symbol', 
            how='left'
        )
        
        # Calculate reinvested earnings flow (foreign investor portion)
        merged['reinvested_earnings_flow'] = merged.apply(
            lambda row: (
                row['flow'] * (float(str(row['investor_limit']).replace('%', '')) / 100)
                if (pd.notna(row['flow']) and 
                    pd.notna(row['investor_limit']) and 
                    str(row['investor_limit']).replace('%', '').replace('.', '').isdigit() and
                    float(str(row['investor_limit']).replace('%', '')) > 0)
                else 0
            ), 
            axis=1
        )
        
        # Load net profit data for additional calculations (optional file)
        if not QUARTERLY_NET_PROFIT_JSON.exists():
            print(
                "⚠️ Warning: quarterly_net_profit.json not found, skipping net profit calculations"
            )
            merged['net_profit_foreign_investor'] = 0
            merged['distributed_profits_foreign_investor'] = 0
        else:
            try:
                with open(QUARTERLY_NET_PROFIT_JSON, 'r', encoding='utf-8') as f:
                    net_profit_data = json.load(f)
                print(f"✅ Loaded net profit data for {len(net_profit_data)} companies")

                # Convert net profit data to lookup format
                net_profit_lookup = {}
                for company in net_profit_data:
                    symbol = company.get('company_symbol')
                    if symbol:
                        net_profit_lookup[symbol] = company

                # Prepare raw net profit per quarter (None if missing); align keys with frontend lookup
                def get_raw_net_profit(symbol: str, quarter: str, year: int):
                    company = net_profit_lookup.get(str(symbol), {})
                    qmap = company.get("quarterly_net_profit", {}) if company else {}
                    return _lookup_quarterly_net_profit(qmap, quarter, int(year))

                # Investor limit fraction
                def investor_fraction(val):
                    if pd.isna(val):
                        return 0.0
                    s = str(val).replace('%', '')
                    if not s.replace('.', '').isdigit():
                        return 0.0
                    try:
                        return float(s) / 100.0
                    except Exception:
                        return 0.0

                # Compute raw net profit and calc/display values
                merged['__raw_net_profit'] = merged.apply(
                    lambda row: get_raw_net_profit(row['company_symbol'], row['quarter'], row['year']), axis=1
                )
                merged['__inv_frac'] = merged['investor_limit'].apply(investor_fraction)
                # Numeric for calculations: use 0 when missing
                merged['__net_profit_foreign_investor_calc'] = merged.apply(
                    lambda row: (
                        (row['__raw_net_profit'] if row['__raw_net_profit'] is not None else 0) * row['__inv_frac']
                    ), axis=1
                )
                # Display column: empty string when raw net profit is missing
                merged['net_profit_foreign_investor'] = merged.apply(
                    lambda row: (
                        row['__net_profit_foreign_investor_calc'] if row['__raw_net_profit'] is not None else ''
                    ), axis=1
                )
                # Calculate distributed using calc numeric regardless of display
                merged['distributed_profits_foreign_investor'] = merged.apply(
                    lambda row: (
                        row['__net_profit_foreign_investor_calc'] - row['reinvested_earnings_flow']
                        if pd.notna(row['reinvested_earnings_flow']) else 0
                    ), axis=1
                )

                print("✅ Added net profit calculations for foreign investors")

            except Exception as e:
                print(f"⚠️ Warning: Error processing net profit data: {e}")
                merged['net_profit_foreign_investor'] = 0
                merged['distributed_profits_foreign_investor'] = 0
        
        # Clean up the merged data
        final_results = merged[['company_symbol', 'company_name', 'quarter', 'year', 'current_value', 'previous_value', 'flow', 'flow_formula', 'foreign_ownership', 'max_allowed', 'investor_limit', 'reinvested_earnings_flow', 'net_profit_foreign_investor', 'distributed_profits_foreign_investor']].copy()
        
        print(f"✅ Calculated flows for {len(final_results)} company-quarters")
        print(f"✅ Added foreign investor flow calculations")
        
        # Save to CSV
        final_results.to_csv(FLOW_CSV_PATH, index=False, encoding="utf-8")
        print(f"✅ Saved flow data to {FLOW_CSV_PATH}")
        
        # Save to JSON for debugging
        final_results.to_json(FLOW_JSON_PATH, orient="records", force_ascii=False, indent=2)
        print(f"✅ Saved flow data to {FLOW_JSON_PATH}")

        # New: Save compact per-quarter foreign investor metrics
        compact = final_results[['company_symbol','company_name','quarter','year','reinvested_earnings_flow','net_profit_foreign_investor','distributed_profits_foreign_investor']].copy()
        compact_json_path = _ROOT / "data/results/foreign_investor_results.json"
        compact.to_json(compact_json_path, orient='records', force_ascii=False, indent=2)
        print(f"✅ Saved foreign investor metrics to {compact_json_path}")
        
        # Display sample results
        print("\n📊 Sample Flow Results:")
        print("=" * 80)
        for _, row in final_results.head(10).iterrows():
            print(f"Company: {row['company_name']} ({row['company_symbol']})")
            print(f"Quarter: {row['quarter']} {row['year']}")
            print(f"Flow: {row['flow']:,.0f} SAR ({row['flow_formula']})")
            print(f"Foreign Investor Flow: {_fmt_sample_currency(row['reinvested_earnings_flow'], 2)}")
            print(f"Net Profit for Foreign Investor: {_fmt_sample_currency(row['net_profit_foreign_investor'], 2)}")
            print(
                f"Distributed Profits for Foreign Investor: {_fmt_sample_currency(row['distributed_profits_foreign_investor'], 2)}"
            )
            print("-" * 40)
        
    except FileNotFoundError:
        print("⚠️ Warning: ownership data not found, saving basic flow data only")
        # Save basic flow data without ownership calculations
        flow_df.to_csv(FLOW_CSV_PATH, index=False, encoding="utf-8")
        print(f"✅ Saved basic flow data to {FLOW_CSV_PATH}")
        
        flow_df.to_json(FLOW_JSON_PATH, orient="records", force_ascii=False, indent=2)
        print(f"✅ Saved basic flow data to {FLOW_JSON_PATH}")
        
    except Exception as e:
        print(f"❌ Error processing ownership data: {e}")
        # Save basic flow data as fallback
        flow_df.to_csv(FLOW_CSV_PATH, index=False, encoding="utf-8")
        print(f"✅ Saved basic flow data to {FLOW_CSV_PATH}")
    
    print("\n🎉 Flow calculation completed successfully!") 

if __name__ == "__main__":
    main() 