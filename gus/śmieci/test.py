import os
from collections import Counter
import pandas as pd

FOLDER = 'data/bronze'

for fname in sorted(os.listdir(FOLDER)):
    if fname.startswith(('~$', 'TERC')) or not fname.endswith(('.csv', '.xlsx')):
        continue

    path = os.path.join(FOLDER, fname)
    if fname.endswith('.csv'):
        df = pd.read_csv(path, sep=';', dtype=str, encoding='utf-8-sig')
    else:
        raw = pd.read_excel(path, header=None, dtype=str)
        hdr_idx = next((i for i, r in raw.iterrows() if r.astype(str).str.contains('Kod', na=False).any()), 0)
        df = raw.iloc[hdr_idx + 1:].copy()
        df.columns = raw.iloc[hdr_idx].astype(str).str.strip()

    nazwa_cols = [c for c in df.columns if str(c).strip().lower() == 'nazwa']
    if not nazwa_cols:
        continue

    unikalne_nazwy = df[nazwa_cols[0]].dropna().str.strip().unique()
    przedrostki = Counter()
    jednowyrazowe = []

    for nazwa in unikalne_nazwy:
        slowa = nazwa.split()
        if len(slowa) > 1:
            przedrostki[slowa[0]] += 1
        elif len(slowa) == 1:
            jednowyrazowe.append(nazwa)

    print(f"PLIK: {fname}")
    print(f"  Przedrostki (top 15 z {len(przedrostki)}): {dict(przedrostki.most_common(15))}")
    print(f"  Jednowyrazowe ({len(jednowyrazowe)} szt.): {jednowyrazowe[:15]}{'...' if len(jednowyrazowe) > 15 else ''}\n")