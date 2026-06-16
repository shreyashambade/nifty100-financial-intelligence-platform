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

        print("Loading clean_companies.csv...")
        df = pd.read_csv(os.path.join(CLEAN_DIR, "clean_companies.csv"))

        for col in ['face_value', 'book_value']:
            df[col] = df[col].astype(float)
        df.replace({pd.NA: None, float('nan'): None}, inplace=True)

        # Safety check: dim_company.symbol FK references dim_companies(symbol)
        cursor.execute("SELECT symbol FROM dim_companies;")
        valid_symbols = {row[0] for row in cursor.fetchall()}
        missing = set(df['symbol']) - valid_symbols
        if missing:
            print(f"  ⚠️  Skipping {len(missing)} symbol(s) not in dim_companies: {missing}")
            df = df[df['symbol'].isin(valid_symbols)]

        print("📊 Loading dim_company...")
        pre = get_row_count(cursor, 'dim_company')
        bulk_upsert(cursor, df, 'dim_company', conflict_columns=['symbol'])
        post = get_row_count(cursor, 'dim_company')
        print(f"  -> dim_company | Before: {pre} | After: {post}")

        conn.commit()
        print("\n✅ Load complete. Transaction committed.")

    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


if __name__ == '__main__':
    main()