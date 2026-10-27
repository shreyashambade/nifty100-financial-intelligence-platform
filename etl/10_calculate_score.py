"""
07_calculate_scores.py

Computes a 0-100 financial health score for every company in the
warehouse, using 6 weighted dimensions (per PRD Section 7.1):

    Profitability   25%   -> OPM%, net_profit_margin_pct (percentile rank)
    Revenue Growth  20%   -> 3-year sales CAGR (computed from fact_profit_loss)
    Leverage/Debt   20%   -> debt_to_equity (latest year, lower = better)
    Cash Flow       15%   -> cash_conversion_ratio (avg, higher = better)
    Dividend        10%   -> dividend payout consistency over last 5 years
    Growth Trend    10%   -> linear regression slope on sales (last 5 years)

Banking/NBFC/Insurance companies are excluded from leverage and cashflow
dimensions since these metrics are structurally misleading for financial firms.

If a company is missing data for a dimension, that dimension is
skipped and the remaining dimensions are re-weighted proportionally
so the final score is still out of 100.

Output is written to fact_ml_scores with computed_at = run timestamp.
"""

import os
import numpy as np
import pandas as pd
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

# .env lives at the project root (one level above etl/)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME", "nifty100_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "Sradb@1"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
}

# Dimension weights (must sum to 100)
WEIGHTS = {
    "profitability": 25,
    "growth": 20,
    "leverage": 20,
    "cashflow": 15,
    "dividend": 10,
    "trend": 10,
}

# Sectors where leverage and cashflow metrics are structurally misleading
FINANCIAL_SECTORS = {"Banking", "NBFC", "Insurance"}


def load_data(conn):
    pl = pd.read_sql("SELECT * FROM fact_profit_loss WHERE year_id <> 9999;", conn)
    bs = pd.read_sql("SELECT * FROM fact_balance_sheet WHERE year_id <> 9999;", conn)
    cf = pd.read_sql("SELECT * FROM fact_cash_flow WHERE year_id <> 9999;", conn)
    sectors = pd.read_sql("SELECT symbol, sector FROM dim_company;", conn)
    return pl, bs, cf, sectors


def score_profitability(pl):
    """Percentile rank of avg OPM% and avg net_profit_margin_pct. Scale 0-25."""
    agg = pl.groupby("symbol").agg(
        avg_opm=("opm_pct", "mean"),
        avg_npm=("net_profit_margin_pct", "mean"),
    ).reset_index()
    agg["opm_rank"] = agg["avg_opm"].rank(pct=True)
    agg["npm_rank"] = agg["avg_npm"].rank(pct=True)
    agg["profitability_score"] = ((agg["opm_rank"] + agg["npm_rank"]) / 2) * WEIGHTS["profitability"]
    return agg[["symbol", "profitability_score"]]


def score_growth(pl):
    """3-year sales CAGR from fact_profit_loss. Scale 0-20."""
    rows = []
    for symbol, g in pl.groupby("symbol"):
        g = g.sort_values("year_id")
        if len(g) < 4:
            continue
        recent = g.iloc[-1]
        past = g.iloc[-4]
        if pd.isna(recent["sales"]) or pd.isna(past["sales"]) or past["sales"] <= 0:
            continue
        cagr = ((recent["sales"] / past["sales"]) ** (1 / 3) - 1) * 100
        rows.append({"symbol": symbol, "sales_cagr_3y": cagr})

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["symbol", "growth_score"])

    df["growth_rank"] = df["sales_cagr_3y"].rank(pct=True)
    df.loc[df["sales_cagr_3y"] < 5, "growth_rank"] *= 0.5
    df["growth_score"] = df["growth_rank"] * WEIGHTS["growth"]
    return df[["symbol", "growth_score"]]


def score_leverage(bs, sectors):
    """Latest year D/E. D/E < 0.1 = full marks, D/E > 2 = 0.
    Financial sector companies (Banking/NBFC/Insurance) are excluded
    since high leverage is structurally normal for them."""
    financial_symbols = set(
        sectors[sectors["sector"].isin(FINANCIAL_SECTORS)]["symbol"]
    )
    rows = []
    for symbol, g in bs.groupby("symbol"):
        if symbol in financial_symbols:
            # Skip leverage dimension for financial companies
            continue
        g = g.sort_values("year_id")
        latest = g.iloc[-1]
        de = latest["debt_to_equity"]
        if pd.isna(de):
            continue
        de = max(de, 0)
        if de <= 0.1:
            score = WEIGHTS["leverage"]
        elif de >= 2:
            score = 0.0
        else:
            score = WEIGHTS["leverage"] * (1 - (de - 0.1) / (2 - 0.1))
        rows.append({"symbol": symbol, "leverage_score": score})
    return pd.DataFrame(rows)


def score_cashflow(cf, pl, sectors):
    """Average cash_conversion_ratio. Ratio > 1.2 = full marks.
    Financial sector companies are excluded."""
    financial_symbols = set(
        sectors[sectors["sector"].isin(FINANCIAL_SECTORS)]["symbol"]
    )
    merged = pd.merge(
        cf[["symbol", "year_id", "operating_activity"]],
        pl[["symbol", "year_id", "net_profit"]],
        on=["symbol", "year_id"], how="inner"
    )
    merged = merged[merged["net_profit"] != 0]
    merged = merged[~merged["symbol"].isin(financial_symbols)]
    merged["ccr"] = merged["operating_activity"] / merged["net_profit"]

    agg = merged.groupby("symbol")["ccr"].mean().reset_index()
    agg.rename(columns={"ccr": "avg_ccr"}, inplace=True)

    def scale(ccr):
        if pd.isna(ccr):
            return np.nan
        ccr = max(ccr, 0)
        if ccr >= 1.2:
            return WEIGHTS["cashflow"]
        return WEIGHTS["cashflow"] * (ccr / 1.2)

    agg["cashflow_score"] = agg["avg_ccr"].apply(scale)
    return agg[["symbol", "cashflow_score"]].dropna()


def score_dividend(pl):
    """Dividend payout consistency over last 5 years. Scale 0-10."""
    rows = []
    for symbol, g in pl.groupby("symbol"):
        g = g.sort_values("year_id").tail(5)
        if g.empty:
            continue
        payouts = g["dividend_payout_pct"].dropna()
        if payouts.empty:
            continue
        years_with_div = (payouts > 0).sum()
        total_years = len(g)
        avg_payout = payouts.mean()
        consistency = years_with_div / total_years
        strength = min(avg_payout / 30, 1.0) if avg_payout > 0 else 0
        score = WEIGHTS["dividend"] * consistency * strength
        rows.append({"symbol": symbol, "dividend_score": score})
    return pd.DataFrame(rows)


def score_trend(pl):
    """Linear regression slope on sales, last 5 years. Scale 0-10."""
    rows = []
    for symbol, g in pl.groupby("symbol"):
        g = g.sort_values("year_id").tail(5)
        g = g.dropna(subset=["sales"])
        if len(g) < 3:
            continue
        x = np.arange(len(g))
        y = g["sales"].values
        slope, _ = np.polyfit(x, y, 1)
        avg_sales = y.mean()
        if avg_sales == 0:
            continue
        norm_slope = slope / avg_sales
        rows.append({"symbol": symbol, "norm_slope": norm_slope})

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["symbol", "trend_score"])

    df["trend_rank"] = df["norm_slope"].rank(pct=True)
    df.loc[df["norm_slope"] < 0, "trend_rank"] *= 0.3
    df["trend_score"] = df["trend_rank"] * WEIGHTS["trend"]
    return df[["symbol", "trend_score"]]


def combine_scores(all_dims):
    """Merge all dimension scores, re-weighting for missing dimensions."""
    merged = all_dims[0]
    for d in all_dims[1:]:
        merged = pd.merge(merged, d, on="symbol", how="outer")

    score_cols = {
        "profitability_score": "profitability",
        "growth_score": "growth",
        "leverage_score": "leverage",
        "cashflow_score": "cashflow",
        "dividend_score": "dividend",
        "trend_score": "trend",
    }

    def compute_row(row):
        earned = 0
        available_weight = 0
        for col, dim in score_cols.items():
            val = row.get(col)
            if pd.notna(val):
                earned += val
                available_weight += WEIGHTS[dim]
        if available_weight == 0:
            return np.nan
        return round((earned / available_weight) * 100, 2)

    merged["overall_score"] = merged.apply(compute_row, axis=1)
    return merged


def assign_health_label(score):
    if pd.isna(score):
        return None
    if score >= 85:
        return "EXCELLENT"
    elif score >= 70:
        return "GOOD"
    elif score >= 50:
        return "AVERAGE"
    elif score >= 35:
        return "WEAK"
    else:
        return "POOR"


def main():
    print("🚀 Connecting to PostgreSQL Data Warehouse...")
    conn = psycopg2.connect(**DB_CONFIG)
    print("✅ Connection Successful.\n")

    print("Loading source data...")
    pl, bs, cf, sectors = load_data(conn)
    financial_count = sectors[sectors["sector"].isin(FINANCIAL_SECTORS)]["symbol"].nunique()
    print(f"  fact_profit_loss: {len(pl)} rows | fact_balance_sheet: {len(bs)} rows | fact_cash_flow: {len(cf)} rows")
    print(f"  Financial sector companies (excluded from leverage/cashflow): {financial_count}\n")

    print("Computing dimension scores...")
    prof  = score_profitability(pl)
    growth = score_growth(pl)
    lev   = score_leverage(bs, sectors)
    cash  = score_cashflow(cf, pl, sectors)
    div   = score_dividend(pl)
    trend = score_trend(pl)

    for name, df in [("Profitability", prof), ("Growth", growth), ("Leverage", lev),
                      ("Cash Flow", cash), ("Dividend", div), ("Trend", trend)]:
        print(f"  {name}: {len(df)} companies scored")

    print("\nCombining into overall score...")
    final = combine_scores([prof, growth, lev, cash, div, trend])
    final["health_label"] = final["overall_score"].apply(assign_health_label)
    final["computed_at"] = datetime.now()
    final = final.dropna(subset=["overall_score"])

    # Round all score columns
    score_cols = ["overall_score", "profitability_score", "growth_score",
                  "leverage_score", "cashflow_score", "dividend_score", "trend_score"]
    for col in score_cols:
        if col in final.columns:
            final[col] = final[col].round(2)

    print(f"\n✅ Final scores computed for {len(final)} companies.")
    print("\nTop 10 by overall score:")
    print(final[["symbol", "overall_score", "health_label"]]
          .sort_values("overall_score", ascending=False)
          .head(10).to_string(index=False))

    print("\nSanity check (5 key companies):")
    check = final[final["symbol"].isin(["TCS", "HDFCBANK", "WIPRO", "ADANIPOWER", "APOLLOHOSP"])]
    print(check[["symbol", "overall_score", "leverage_score", "cashflow_score", "health_label"]]
          .sort_values("overall_score", ascending=False).to_string(index=False))

    print("\n📈 Writing to fact_ml_scores...")
    cursor = conn.cursor()
    cursor.execute("TRUNCATE fact_ml_scores;")

    insert_cols = ["symbol", "computed_at", "overall_score", "profitability_score",
                   "growth_score", "leverage_score", "cashflow_score", "dividend_score",
                   "trend_score", "health_label"]
    for col in insert_cols:
        if col not in final.columns:
            final[col] = None

    final = final.replace({np.nan: None})
    values = [tuple(row[c] for c in insert_cols) for _, row in final.iterrows()]
    col_str = ",".join(insert_cols)
    placeholders = "(" + ",".join(["%s"] * len(insert_cols)) + ")"
    cursor.executemany(f"INSERT INTO fact_ml_scores ({col_str}) VALUES {placeholders}", values)
    conn.commit()

    print(f"✅ Inserted {len(values)} score rows into fact_ml_scores.")
    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()