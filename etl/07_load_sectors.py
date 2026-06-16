import pandas as pd
import psycopg2
import os

from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
     "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT")
}

MAPPING_FILE = "../data/raw/sector_mapping.csv"


def main():
    print("🚀 Connecting to PostgreSQL Data Warehouse...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("✅ Connection Successful.\n")

        df = pd.read_csv(MAPPING_FILE)
        print(f"Loaded {len(df)} symbol-sector mappings.")

        updated = 0
        for _, row in df.iterrows():
            cursor.execute(
                "UPDATE dim_company SET sector = %s WHERE symbol = %s;",
                (row['sector'], row['symbol'])
            )
            updated += cursor.rowcount

        conn.commit()
        print(f"✅ Updated sector for {updated} rows in dim_company.")

        cursor.execute("SELECT COUNT(*) FROM dim_company WHERE sector IS NULL;")
        missing = cursor.fetchone()[0]
        print(f"ℹ️  Companies still missing a sector: {missing}")

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