import pandas as pd
import os
import re

# Szukamy plików
TERC_PATH = next((f for f in os.listdir('../data/') if 'TERC' in f), None)
GUS_PATH = next((f for f in os.listdir('../data/') if 'Mediana' in f), None)

# 1. Przygotowanie słownika TERC
terc_raw = pd.read_csv(f'data/{TERC_PATH}', sep=';', dtype=str, encoding='utf-8-sig')
# Wyciągamy tylko powiaty (POW jest pełne, GMI jest puste)
terc_pow = terc_raw[(terc_raw['POW'].notna()) & (terc_raw['GMI'].isna())].copy()
terc_pow['teryt_woj'] = terc_pow['WOJ'].str.zfill(2)
terc_pow['nazwa_norm'] = terc_pow['NAZWA'].astype(str).str.lower().str.strip()

# 2. Przygotowanie danych GUS
df = pd.read_csv(f'data/{GUS_PATH}', sep=';', dtype=str, encoding='utf-8-sig')
# Bierzemy TYLKO wiersze zaczynające się od "Powiat "
powiaty_df = df[df['Nazwa'].astype(str).str.strip().str.startswith('Powiat ')].copy()

# Funkcja wyciągająca województwo (potrzebne, bo np. "powiat bielski" jest w dwóch województwach)
def get_woj(c):
    c = re.sub(r'\D', '', str(c)).replace('.0', '').zfill(12)
    if c[7:12] == '00000': return c[:2] # TERYT
    return c[2:4]                       # KTS

powiaty_df['teryt_woj'] = powiaty_df['Kod'].apply(get_woj)
# Wycinamy słowo Powiat i czyścimy skróty miast na prawach powiatu
powiaty_df['nazwa_norm'] = (
    powiaty_df['Nazwa'].astype(str).str.strip()
    .str.replace(r'^Powiat\s+(m\.\s*(st\.\s*)?)?', '', regex=True)
    .str.lower()
    .str.strip()
)

# 3. Łączymy
merged = powiaty_df.merge(terc_pow[['teryt_woj', 'nazwa_norm', 'POW', 'NAZWA']], on=['teryt_woj', 'nazwa_norm'], how='left')
bledy = merged[merged['POW'].isna()]

print(f"Wszystkich powiatów w pliku GUS: {len(powiaty_df)}")
print(f"Rozpoznanych pomyślnie po nazwie: {len(merged[merged['POW'].notna()])}")
print(f"Nierozpoznanych (błędy mapowania): {len(bledy)}")

if not bledy.empty:
    print("\nOto nazwy, które nie chcą się złączyć z TERC:")
    # Pokazujemy unikalne błędy
    print(bledy[['Kod', 'Nazwa', 'teryt_woj', 'nazwa_norm']].drop_duplicates().head(25).to_string())