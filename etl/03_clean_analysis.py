import pandas as pd
import re
import os

RAW_DIR = "../data/raw"
CLEAN_DIR = "../data/clean"
os.makedirs(CLEAN_DIR, exist_ok=True)


def parse_metric(val):
    """Parse strings like '10 Years:   21%', 'TTM:  43%', 'Last Year:  17%'
    into (period_label, value_pct). Period labels are normalized to
    10Y / 5Y / 3Y / TTM to match the PRD's fact_analysis.period_label."""
    if pd.isna(val):
        return (None, None)

    s = str(val).strip()

    pct_match = re.search(r'(-?\d+(\.\d+)?)\s*%', s)
    pct = float(pct_match.group(1)) if pct_match else None

    year_match = re.search(r'(\d+)\s*Year', s, re.IGNORECASE)
    if 'TTM' in s.upper():
        period = 'TTM'
    elif 'Last Year' in s:
        period = 'TTM'
    elif year_match:
        period = f"{year_match.group(1)}Y"
    else:
        period = None

    return (period, pct)


def main():
    print("🚀 Cleaning analysis.xlsx -> fact_analysis staging...")

    df = pd.read_excel(os.path.join(RAW_DIR, "analysis.xlsx"), skiprows=1)
    df.rename(columns={'company_id': 'symbol'}, inplace=True)

    rows = []
    for _, r in df.iterrows():
        sales_p, sales_v = parse_metric(r.get('compounded_sales_growth'))
        profit_p, profit_v = parse_metric(r.get('compounded_profit_growth'))
        cagr_p, cagr_v = parse_metric(r.get('stock_price_cagr'))
        roe_p, roe_v = parse_metric(r.get('roe'))

        # The 4 metrics in a row may carry slightly different but
        # equivalent period tags (e.g. cagr says "1 Year" while the
        # others say "TTM"). Pick the first non-null as this row's
        # canonical period_label.
        period = sales_p or profit_p or roe_p or cagr_p

        rows.append({
            'symbol': r['symbol'],
            'period_label': period,
            'compounded_sales_growth_pct': sales_v,
            'compounded_profit_growth_pct': profit_v,
            'stock_price_cagr_pct': cagr_v,
            'roe_pct': roe_v,
        })

    out = pd.DataFrame(rows)
    out.dropna(subset=['symbol', 'period_label'], inplace=True)
    out.drop_duplicates(subset=['symbol', 'period_label'], keep='last', inplace=True)

    out_path = os.path.join(CLEAN_DIR, "clean_analysis.csv")
    out.to_csv(out_path, index=False)
    print(f"✅ Saved {len(out)} rows to {out_path}")


if __name__ == "__main__":
    main()