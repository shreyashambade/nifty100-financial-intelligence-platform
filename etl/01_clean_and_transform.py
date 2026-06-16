import pandas as pd
import numpy as np
import os
import re

RAW_DIR = "../data/raw"
CLEAN_DIR = "../data/clean"
os.makedirs(CLEAN_DIR, exist_ok=True)

def clean_strings_and_nulls(df):
    df = df.replace(['NULL', 'Null', 'null', ''], np.nan)
    for col in df.select_dtypes(include=['object']):
        df[col] = df[col].apply(lambda x: str(x).strip().replace('\r', '').replace('\n', '') if pd.notna(x) else x)
    return df

def standardize_year(year_val):
    if pd.isna(year_val): return pd.Series([np.nan, np.nan, np.nan])
    year_str = str(year_val).strip()
    if year_str == 'TTM': return pd.Series(['TTM', 9999, 9999])
    match = re.search(r'([A-Za-z]+)[-\s]*(20\d{2}|\d{2})', year_str)
    if match:
        month = match.group(1)[:3].capitalize()
        yy = match.group(2)
        full_year = int(yy) if len(yy) == 4 else 2000 + int(yy)
        return pd.Series([f"{month} {full_year}", full_year, full_year])
    return pd.Series([year_str, np.nan, np.nan])

def main():
    print("🚀 Starting PRD-Compliant Data Cleaning Pipeline...")

    df_pl = pd.read_excel(os.path.join(RAW_DIR, "profitandloss.xlsx"), skiprows=1)
    df_bs = pd.read_excel(os.path.join(RAW_DIR, "balancesheet.xlsx"), skiprows=1)
    df_cf = pd.read_excel(os.path.join(RAW_DIR, "cashflow.xlsx"), skiprows=1)

    # 1. PRD Naming Standard: 'company_id' (ticker symbol) -> 'symbol'
    #    Drop the leftover 'id' row-serial column (not needed in the warehouse).
    for df in [df_pl, df_bs, df_cf]:
        if 'company_id' in df.columns:
            df.rename(columns={'company_id': 'symbol'}, inplace=True)
        if 'id' in df.columns:
            df.drop(columns=['id'], inplace=True)

    # Clean strings
    df_pl = clean_strings_and_nulls(df_pl)
    df_bs = clean_strings_and_nulls(df_bs)
    df_cf = clean_strings_and_nulls(df_cf)

    # PRD Naming Standard: Rename existing columns to match schema perfectly
    df_pl.rename(columns={'opm_percentage': 'opm_pct', 'tax_percentage': 'tax_pct', 'dividend_payout': 'dividend_payout_pct'}, inplace=True)
    if 'other_asset' in df_bs.columns: df_bs.rename(columns={'other_asset': 'other_assets'}, inplace=True)

    print("Standardizing years to year_id...")
    for df in [df_pl, df_bs, df_cf]:
        if 'year' in df.columns:
            df[['year_label', 'year_id', 'sort_order']] = df['year'].apply(standardize_year)
            df['year_id'] = pd.to_numeric(df['year_id'], errors='coerce')
            df['sort_order'] = pd.to_numeric(df['sort_order'], errors='coerce')

    print("Enforcing Primary Key integrity...")
    for df in [df_pl, df_bs, df_cf]:
        df.dropna(subset=['symbol', 'year_id'], inplace=True)
        df.drop_duplicates(subset=['symbol', 'year_id'], keep='last', inplace=True)

    print("Calculating PRD Section 3.2 Computed Columns...")
    # --- P&L Metrics ---
    df_pl['net_profit_margin_pct'] = (df_pl['net_profit'] / df_pl['sales']) * 100
    df_pl['expense_ratio_pct'] = (df_pl['expenses'] / df_pl['sales']) * 100
    df_pl['interest_coverage'] = df_pl['operating_profit'] / (df_pl['interest'] + 1e-6)

    # --- Cash Flow Metrics ---
    df_cf['free_cash_flow'] = df_cf['operating_activity'] + df_cf['investing_activity']

    # --- Cross-Table Merges & Metrics ---
    # Bring BS columns onto df_pl's index (left join keeps df_pl's row count/order)
    bs_for_pl = pd.merge(df_pl[['symbol', 'year_id']], df_bs[['symbol', 'year_id', 'total_assets']], on=['symbol', 'year_id'], how='left')
    df_pl['asset_turnover'] = df_pl['sales'] / (bs_for_pl['total_assets'] + 1e-6)
    df_pl['return_on_assets'] = (df_pl['net_profit'] / (bs_for_pl['total_assets'] + 1e-6)) * 100

    # Bring PL's net_profit onto df_cf's index for cash conversion ratio
    pl_for_cf = pd.merge(df_cf[['symbol', 'year_id']], df_pl[['symbol', 'year_id', 'net_profit']], on=['symbol', 'year_id'], how='left')
    df_cf['cash_conversion_ratio'] = df_cf['operating_activity'] / (pl_for_cf['net_profit'] + 1e-6)

    # --- Balance Sheet Metrics ---
    df_bs['debt_to_equity'] = df_bs['borrowings'] / (df_bs['equity_capital'] + df_bs['reserves'] + 1e-6)
    df_bs['equity_ratio'] = (df_bs['equity_capital'] + df_bs['reserves']) / (df_bs['total_assets'] + 1e-6)

    # Book Value Per Share = (Equity+Reserves) / Shares. Shares = Net Profit / EPS
    pl_for_bs = pd.merge(df_bs[['symbol', 'year_id']], df_pl[['symbol', 'year_id', 'eps', 'net_profit']], on=['symbol', 'year_id'], how='left')
    shares_outstanding = np.where(pl_for_bs['eps'] != 0, pl_for_bs['net_profit'] / pl_for_bs['eps'], np.nan)
    df_bs['book_value_per_share'] = (df_bs['equity_capital'] + df_bs['reserves']) / shares_outstanding

    print("Dropping legacy columns to match warehouse schema...")
    for df in [df_pl, df_bs, df_cf]:
        cols_to_drop = [col for col in ['year'] if col in df.columns]
        if cols_to_drop: df.drop(columns=cols_to_drop, inplace=True)

    print("Saving to warehouse staging...")
    df_pl.to_csv(os.path.join(CLEAN_DIR, "clean_profit_loss.csv"), index=False)
    df_bs.to_csv(os.path.join(CLEAN_DIR, "clean_balance_sheet.csv"), index=False)
    df_cf.to_csv(os.path.join(CLEAN_DIR, "clean_cash_flow.csv"), index=False)
    print("✅ PRD Transformation Complete.")

if __name__ == "__main__":
    main()