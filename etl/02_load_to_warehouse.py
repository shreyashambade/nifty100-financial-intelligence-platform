import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import os

from dotenv import load_dotenv

load_dotenv()

CLEAN_DIR = "../data/clean"
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT")
}

def bulk_upsert(cursor, df, table_name, conflict_columns):
    if df.empty:
        return 0
    columns = list(df.columns)
    values = [tuple(row) for row in df.to_numpy().tolist()]
    col_str = ",".join(columns)
    excluded_str = ",".join([f"{col}=EXCLUDED.{col}" for col in columns if col not in conflict_columns])
    conflict_str = ",".join(conflict_columns)

    if not excluded_str:
        query = f"INSERT INTO {table_name} ({col_str}) VALUES %s ON CONFLICT ({conflict_str}) DO NOTHING"
    else:
        query = f"INSERT INTO {table_name} ({col_str}) VALUES %s ON CONFLICT ({conflict_str}) DO UPDATE SET {excluded_str}"

    execute_values(cursor, query, values)
    return len(values)

def get_row_count(cursor, table_name):
    cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
    return cursor.fetchone()[0]

def main():
    print("🚀 Connecting to PostgreSQL Data Warehouse...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("✅ Connection Successful.\n")

        print("Loading clean datasets into memory...")
        df_pl = pd.read_csv(os.path.join(CLEAN_DIR, "clean_profit_loss.csv"))
        df_bs = pd.read_csv(os.path.join(CLEAN_DIR, "clean_balance_sheet.csv"))
        df_cf = pd.read_csv(os.path.join(CLEAN_DIR, "clean_cash_flow.csv"))

        for df in [df_pl, df_bs, df_cf]:
            # Drop any leftover legacy id/company_id columns not present in warehouse schema
            for legacy_col in ['id', 'company_id']:
                if legacy_col in df.columns:
                    df.drop(columns=[legacy_col], inplace=True)

            for col in df.columns:
                if col in ['year_id', 'sort_order']:
                    df[col] = df[col].apply(lambda x: int(x) if pd.notna(x) else None)
                elif pd.api.types.is_numeric_dtype(df[col]):
                    df[col] = df[col].astype(float)
            df.replace({pd.NA: None, float('nan'): None}, inplace=True)

        print("📊 PHASE 1: Loading Dimension Tables...")
        all_symbols = pd.concat([df_pl[['symbol']], df_bs[['symbol']], df_cf[['symbol']]]).drop_duplicates()
        pre = get_row_count(cursor, 'dim_companies')
        bulk_upsert(cursor, all_symbols, 'dim_companies', conflict_columns=['symbol'])
        post = get_row_count(cursor, 'dim_companies')
        print(f"  -> dim_companies | Before: {pre} | After: {post}")

        all_years = pd.concat([
            df_pl[['year_id', 'year_label', 'sort_order']],
            df_bs[['year_id', 'year_label', 'sort_order']],
            df_cf[['year_id', 'year_label', 'sort_order']]
        ]).dropna().drop_duplicates(subset=['year_id'])
        pre = get_row_count(cursor, 'dim_years')
        bulk_upsert(cursor, all_years, 'dim_years', conflict_columns=['year_id'])
        post = get_row_count(cursor, 'dim_years')
        print(f"  -> dim_years     | Before: {pre} | After: {post}\n")

        print("📈 PHASE 2: Loading Fact Tables...")
        pre = get_row_count(cursor, 'fact_profit_loss')
        bulk_upsert(cursor, df_pl, 'fact_profit_loss', conflict_columns=['symbol', 'year_id'])
        post = get_row_count(cursor, 'fact_profit_loss')
        print(f"  -> fact_profit_loss | Before: {pre} | After: {post}")

        pre = get_row_count(cursor, 'fact_balance_sheet')
        bulk_upsert(cursor, df_bs, 'fact_balance_sheet', conflict_columns=['symbol', 'year_id'])
        post = get_row_count(cursor, 'fact_balance_sheet')
        print(f"  -> fact_balance_sheet | Before: {pre} | After: {post}")

        pre = get_row_count(cursor, 'fact_cash_flow')
        bulk_upsert(cursor, df_cf, 'fact_cash_flow', conflict_columns=['symbol', 'year_id'])
        post = get_row_count(cursor, 'fact_cash_flow')
        print(f"  -> fact_cash_flow | Before: {pre} | After: {post}\n")

        print("🔎 PHASE 3: Running Post-Load Data Quality Checks...")
        cursor.execute("SELECT COUNT(*) FROM fact_profit_loss WHERE symbol NOT IN (SELECT symbol FROM dim_companies);")
        orphans = cursor.fetchone()[0]
        if orphans > 0:
            print(f"  ❌ DQ FAILED: Found {orphans} rows in P&L with no matching company.")
        else:
            print("  ✅ DQ PASSED: Foreign Key Integrity (No orphaned records).")

        conn.commit()
        print("\n✅ PRD Load complete. All transactions safely committed to PostgreSQL.")

    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        if 'conn' in locals(): conn.rollback()
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()

if __name__ == '__main__':
    main()