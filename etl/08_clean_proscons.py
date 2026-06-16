import pandas as pd
import numpy as np
import os
from datetime import datetime

RAW_DIR = "../data/raw"
CLEAN_DIR = "../data/clean"
os.makedirs(CLEAN_DIR, exist_ok=True)


def main():
    print("🚀 Cleaning prosandcons.xlsx -> fact_pros_cons staging...")

    df = pd.read_excel(os.path.join(RAW_DIR, "prosandcons.xlsx"), skiprows=1)
    df.rename(columns={'company_id': 'symbol'}, inplace=True)
    df = df.replace(['NULL', 'Null', 'null', ''], np.nan)

    now = datetime.now()
    rows = []

    for _, r in df.iterrows():
        symbol = r['symbol']
        if pd.notna(r.get('pros')):
            rows.append({
                'symbol': symbol,
                'is_pro': True,
                'category': None,
                'text': str(r['pros']).strip(),
                'source': 'MANUAL',
                'confidence': None,
                'generated_at': now
            })
        if pd.notna(r.get('cons')):
            rows.append({
                'symbol': symbol,
                'is_pro': False,
                'category': None,
                'text': str(r['cons']).strip(),
                'source': 'MANUAL',
                'confidence': None,
                'generated_at': now
            })

    out = pd.DataFrame(rows)
    out.dropna(subset=['symbol', 'text'], inplace=True)

    out_path = os.path.join(CLEAN_DIR, "clean_pros_cons.csv")
    out.to_csv(out_path, index=False)
    print(f"✅ Saved {len(out)} rows to {out_path}")
    print(f"   Pros: {(out['is_pro'] == True).sum()} | Cons: {(out['is_pro'] == False).sum()}")


if __name__ == "__main__":
    main()