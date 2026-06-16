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


def get_row_count(cursor, table_name):
    cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
    return cursor.fetchone()[0]


def main():
    print("🚀 Connecting to PostgreSQL Data Warehouse...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("✅ Connection Successful.\n")

        df = pd.read_csv(os.path.join(CLEAN_DIR, "clean_pros_cons.csv"))
        df.replace({pd.NA: None, float('nan'): None}, inplace=True)

        # Safety check against dim_companies FK
        cursor.execute("SELECT symbol FROM dim_companies;")
        valid_symbols = {row[0] for row in cursor.fetchall()}
        missing = set(df['symbol']) - valid_symbols
        if missing:
            print(f"  ⚠️  Skipping {len(missing)} symbol(s) not in dim_companies: {missing}")
            df = df[df['symbol'].isin(valid_symbols)]

        print("📈 Loading fact_pros_cons...")
        pre = get_row_count(cursor, 'fact_pros_cons')

        # No natural unique key for a pros/cons text row, so this table
        # is loaded as plain inserts. To keep the script re-runnable
        # without creating duplicates, we clear existing MANUAL rows
        # for the symbols being loaded before inserting.
        symbols_in_batch = tuple(df['symbol'].unique())
        if symbols_in_batch:
            cursor.execute(
                "DELETE FROM fact_pros_cons WHERE source = 'MANUAL' AND symbol IN %s;",
                (symbols_in_batch,)
            )

        columns = list(df.columns)
        values = [tuple(row) for row in df.to_numpy().tolist()]
        col_str = ",".join(columns)
        query = f"INSERT INTO fact_pros_cons ({col_str}) VALUES %s"
        execute_values(cursor, query, values)

        post = get_row_count(cursor, 'fact_pros_cons')
        print(f"  -> fact_pros_cons | Before: {pre} | After: {post}")

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