import pandas as pd
import numpy as np
import os

RAW_DIR = "../data/raw"
CLEAN_DIR = "../data/clean"
os.makedirs(CLEAN_DIR, exist_ok=True)


def main():
    print("🚀 Cleaning companies.xlsx -> dim_company staging...")

    df = pd.read_excel(os.path.join(RAW_DIR, "companies.xlsx"), skiprows=1)

    df.rename(columns={
        'id': 'symbol',
        'nse_profile': 'nse_url',
        'bse_profile': 'bse_url',
    }, inplace=True)

    # Strip whitespace/newlines from text columns
    df = df.replace(['NULL', 'Null', 'null', ''], np.nan)
    for col in df.select_dtypes(include=['object']):
        df[col] = df[col].apply(lambda x: str(x).strip().replace('\r', '').replace('\n', '') if pd.notna(x) else x)

    # Keep only the columns dim_company actually has (drop chart_link, roce_percentage, roe_percentage for now)
    keep_cols = ['symbol', 'company_name', 'company_logo', 'website',
                 'nse_url', 'bse_url', 'face_value', 'book_value', 'about_company']
    df = df[keep_cols]

    df.dropna(subset=['symbol'], inplace=True)
    df.drop_duplicates(subset=['symbol'], keep='last', inplace=True)

    out_path = os.path.join(CLEAN_DIR, "clean_companies.csv")
    df.to_csv(out_path, index=False)
    print(f"✅ Saved {len(df)} rows to {out_path}")


if __name__ == "__main__":
    main()