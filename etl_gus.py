import os
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import pandas as pd
import duckdb

BASE_DIR = Path(__file__).resolve().parent
SEARCH_DIRS = [str(BASE_DIR / 'data' ), str(BASE_DIR)]
DB_PATH = str(BASE_DIR / 'hurtownia.duckdb')

NULL_TOKENS = ['', ' ', '-', '.', 'n/a', 'N/A', 'null', 'NULL', 'nan', 'NaN']
ROMAN_TO_INT = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, '1': 1, '2': 2, '3': 3, '4': 4}
NUTS_PREFIXES = ('MAKROREGION ', 'REGION ', 'PODREGION ')
VALID_WOJ_CODES = {'02', '04', '06', '08', '10', '12', '14', '16', '18',
                   '20', '22', '24', '26', '28', '30', '32'}
VALID_MAKROREGIONS = {'01', '02', '03', '04', '05', '06', '07'}


def find_file(keyword: str) -> str:
    for folder in SEARCH_DIRS:
        if not os.path.exists(folder):
            continue
        for fname in os.listdir(folder):
            if keyword.lower() in fname.lower() and not fname.startswith('~$'):
                return os.path.join(folder, fname)
    raise FileNotFoundError(f"Nie znaleziono pliku: '{keyword}'")


def clean_numeric(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    s = s.replace(NULL_TOKENS, pd.NA)
    s = s.str.replace(',', '.', regex=False)
    s = s.str.replace(r'[\s\u00a0\u202f\u2009\u2007\u2008\u200b]+', '', regex=True)
    s = s.str.replace('\u2013', '-', regex=False).str.replace('\u2212', '-', regex=False)
    s = s.str.replace(r'[^\d.\-]', '', regex=True)
    s = s.str.replace(r'\.(?=.*\.)', '', regex=True)
    s = s.mask(s == '', pd.NA)
    return pd.to_numeric(s, errors='coerce')


def extract_quarter(series: pd.Series) -> pd.Series:
    extracted = series.str.extract(r'\b(IV|III|II|I|[1-4])\s*kwarta[łl]\b', flags=re.IGNORECASE)[0]
    mapped = extracted.str.upper().map(ROMAN_TO_INT)
    return pd.to_numeric(mapped, errors='coerce').astype('Int64')


def _find_col(columns, patterns, exclude=(), required=False):
    for col in columns:
        if col in exclude:
            continue
        for pat in patterns:
            if re.search(pat, str(col), re.IGNORECASE):
                return col
    if required:
        available = [c for c in columns if c not in exclude]
        raise KeyError(f"Nie znaleziono kolumny pasującej do {patterns}. Dostępne: {available}")
    return None


def _find_value_col(columns, exclude=(), required=False):
    for col in columns:
        if col in exclude:
            continue
        if 'warto' in str(col).lower():
            return col
    if required:
        available = [c for c in columns if c not in exclude]
        raise KeyError(f"Nie znaleziono kolumny wartości. Dostępne: {available}")
    return None


def _find_year_col(columns, exclude=()):
    cols = [c for c in columns if c not in exclude]
    for col in cols:
        if 'rok' in str(col).lower() or 'year' in str(col).lower():
            return col
    for col in cols:
        if re.search(r'\b(19|20)\d{2}\b', str(col)):
            return col
    return None


def read_xlsx_safe(file_path: str) -> pd.DataFrame:
    try:
        return pd.read_excel(file_path, header=None, dtype=str)
    except ImportError:
        rows = []
        with zipfile.ZipFile(file_path, 'r') as z:
            shared = []
            if 'xl/sharedStrings.xml' in z.namelist():
                tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
                for elem in tree.iter():
                    if elem.tag.endswith('t') and elem.text:
                        shared.append(elem.text)
            sheets = [f for f in z.namelist() if f.startswith('xl/worksheets/sheet')]
            tree = ET.fromstring(z.read(sheets[0]))
            sheet_data = tree.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheetData')
            if sheet_data is not None:
                for row_elem in sheet_data:
                    row = []
                    for cell in row_elem:
                        t = cell.get('t')
                        v = cell.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
                        val = v.text if v is not None else ''
                        if t == 's' and val.isdigit():
                            idx = int(val)
                            val = shared[idx] if idx < len(shared) else val
                        row.append(val)
                    rows.append(row)
        return pd.DataFrame(rows)


def build_silver_terc(terc_path: str) -> dict:
    df = pd.read_csv(terc_path, sep=';', dtype=str, encoding='utf-8-sig')
    df.columns = [c.strip() for c in df.columns]

    df['WOJ'] = df['WOJ'].fillna('').str.strip().str.zfill(2)
    df['POW'] = df['POW'].fillna('').str.strip()
    df['GMI'] = df['GMI'].fillna('').str.strip()
    df['RODZ'] = df['RODZ'].fillna('').str.strip()
    df['NAZWA'] = df['NAZWA'].fillna('').str.strip()

    woj_df = df[(df['WOJ'] != '00') & (df['POW'] == '')][['WOJ', 'NAZWA']].drop_duplicates()
    woj_df.columns = ['teryt_woj', 'nazwa_wojewodztwa']

    pow_df = df[(df['POW'] != '') & (df['GMI'] == '')].copy()
    pow_df['teryt_powiat'] = pow_df['WOJ'] + pow_df['POW'].str.zfill(2)
    pow_df['teryt_woj'] = pow_df['WOJ']
    pow_df = pow_df[['teryt_powiat', 'teryt_woj', 'NAZWA']].drop_duplicates()
    pow_df.columns = ['teryt_powiat', 'teryt_woj', 'nazwa_powiatu']

    gmi_raw = df[(df['GMI'] != '') & (df['RODZ'] != '')].copy()
    gmi_raw['teryt_gmina'] = gmi_raw['WOJ'] + gmi_raw['POW'].str.zfill(2) + gmi_raw['GMI'].str.zfill(2) + gmi_raw['RODZ']
    gmi_raw['teryt_powiat'] = gmi_raw['WOJ'] + gmi_raw['POW'].str.zfill(2)
    gmi_raw['teryt_woj'] = gmi_raw['WOJ']
    gmi_df = gmi_raw[['teryt_gmina', 'teryt_powiat', 'NAZWA']].drop_duplicates()
    gmi_df.columns = ['teryt_gmina', 'teryt_powiat', 'nazwa_gminy']

    woj_name_to_code = dict(zip(woj_df['nazwa_wojewodztwa'].str.upper(), woj_df['teryt_woj']))
    woj_code_to_name = dict(zip(woj_df['teryt_woj'], woj_df['nazwa_wojewodztwa']))
    pow_code_to_name = dict(zip(pow_df['teryt_powiat'], pow_df['nazwa_powiatu']))
    gmi_code_to_name = dict(zip(gmi_df['teryt_gmina'], gmi_df['nazwa_gminy']))

    pow_lookup = pow_df.copy()
    pow_lookup['nazwa_norm'] = pow_lookup['nazwa_powiatu'].str.lower().str.replace(r'\s+', ' ', regex=True).str.strip()

    gmi_lookup = gmi_raw[['teryt_gmina', 'teryt_powiat', 'teryt_woj', 'NAZWA', 'RODZ']].drop_duplicates().copy()
    gmi_lookup['nazwa_norm'] = gmi_lookup['NAZWA'].str.lower().str.replace(r'\s+', ' ', regex=True).str.strip()

    return {
        'woj_df': woj_df, 'pow_df': pow_df, 'gmi_df': gmi_df,
        'woj_name_to_code': woj_name_to_code,
        'woj_code_to_name': woj_code_to_name,
        'pow_code_to_name': pow_code_to_name,
        'gmi_code_to_name': gmi_code_to_name,
        'pow_lookup': pow_lookup, 'gmi_lookup': gmi_lookup,
    }


def decode_kts_or_teryt(raw_code, *args) -> tuple[str, str, str]:
    """
    Kolejność rozpoznawania:
      1. Czysty 7-cyfrowy TERYT
      2. <7 cyfr – dopełnienie z lewej zerami
      3. 12-cyfrowy stary KTS z prefiksem '10' ('100201011000' -> '0201011')
      4. 12-cyfrowy nowy BDL: MM WW PP ... -> powiat z c[2:6]
      5. 12-cyfrowy TERYT dopełniony zerami ('146501100000' -> '1465011')
      6. 11-cyfrowy (Excel uciął wiodące zero z makroregionu):
         pad do 12 i to samo co w pkt 4 – to naprawia ~50 tys. NULL w ceny_mieszkan
    """
    if raw_code is None:
        return '', '', ''
    s = str(raw_code).strip()
    if not s or s.lower() in ('nan', 'none', ''):
        return '', '', ''

    if 'e' in s.lower() or 'E' in s:
        try:
            s = f"{float(s):.0f}"
        except (ValueError, OverflowError):
            pass

    c = ''.join(filter(str.isdigit, s))
    if not c or set(c) == {'0'}:
        return '', '', ''

    # 1) 7-cyfrowy TERYT
    if len(c) == 7:
        return c[:2], c[:4], c

    # 2) <7 cyfr – dopełnienie
    if len(c) < 7:
        c = c.zfill(7)
        return c[:2], c[:4], c

    # 3–5) 12-cyfrowy kod
    if len(c) == 12:
        # stary KTS z prefiksem '10'
        if c[:2] == '10':
            teryt = c[2:9]
            if teryt[:2] in VALID_WOJ_CODES and teryt[6] != '0':
                return teryt[:2], teryt[:4], teryt
        # nowy BDL: MM WW PP ...
        if c[:2] in VALID_MAKROREGIONS and c[2:4] in VALID_WOJ_CODES:
            teryt_pow = c[2:6]
            if teryt_pow[2:4] != '00':
                # gmina celowo pusta – nie próbujemy zgadywać GG R z nowego BDL;
                # fallback po nazwie w map_gmina_by_name zadziała dużo lepiej
                return c[2:4], teryt_pow, ''
        # TERYT dopełniony zerami z prawej
        if c[7:12] == '00000' and c[:2] in VALID_WOJ_CODES:
            return c[:2], c[:4], c[:7]

    # 6) 11-cyfrowy – Excel uciął wiodące zero makroregionu
    if len(c) == 11 and c[0] in '1234567':
        c12 = '0' + c
        if c12[:2] in VALID_MAKROREGIONS and c12[2:4] in VALID_WOJ_CODES:
            teryt_pow = c12[2:6]
            if teryt_pow[2:4] != '00':
                return c12[2:4], teryt_pow, ''

    # Ostatnia deska ratunku: przesuwne okno 7-cyfrowe
    for start in range(0, min(6, len(c) - 6)):
        cand = c[start:start + 7]
        if cand[:2] in VALID_WOJ_CODES and cand[6] != '0':
            return cand[:2], cand[:4], cand

    return '', '', ''


def map_powiat_by_name(df: pd.DataFrame, terc: dict) -> pd.DataFrame:
    df = df.copy()
    df['nazwa_clean'] = df['Nazwa'].astype(str).str.strip()
    df['is_powiat'] = df['nazwa_clean'].str.startswith('Powiat ')

    decoded = df['Kod'].apply(decode_kts_or_teryt)
    df['teryt_woj'] = decoded.apply(lambda t: t[0] if t else '')

    df['nazwa_norm'] = (
        df['nazwa_clean']
        .str.replace(r'^Powiat\s+(m\.\s*(st\.\s*)?)?', '', regex=True)
        .str.replace(r'\s+od\s+20\d{2}$', '', regex=True)
        .str.lower()
        .str.replace(r'\s+', ' ', regex=True)
        .str.strip()
    )

    return df.merge(
        terc['pow_lookup'][['teryt_woj', 'nazwa_norm', 'teryt_powiat', 'nazwa_powiatu']],
        on=['teryt_woj', 'nazwa_norm'], how='left'
    )


def map_gmina_by_name(df: pd.DataFrame, terc: dict, debug: bool = True) -> pd.DataFrame:
    df = df.copy()
    df['nazwa_clean'] = df['Nazwa'].astype(str).str.strip()

    is_higher_level = (
            (df['nazwa_clean'] == 'POLSKA') |
            (df['nazwa_clean'].str.startswith(NUTS_PREFIXES)) |
            (df['nazwa_clean'].str.startswith('Powiat ')) |
            (df['nazwa_clean'].str.upper().isin(terc['woj_name_to_code'].keys()))
    )
    df = df[~is_higher_level].copy()

    decoded = df['Kod'].apply(decode_kts_or_teryt)
    df['teryt_woj'] = decoded.apply(lambda t: str(t[0]) if t else '')
    df['teryt_gmina'] = decoded.apply(lambda t: str(t[2]) if t else '')

    if debug:
        sample_codes = df['Kod'].astype(str).head(10).tolist()
        sample_decoded = decoded.head(10).tolist()
        empty_teryt = int((df['teryt_gmina'].fillna('') == '').sum())
        print(f"[map_gmina_by_name] Wejście po filtrze: {len(df)} wierszy, "
              f"pustych teryt_gmina: {empty_teryt}/{len(df)}")
        print(f"  Próbka Kod→(woj,pow,gmi): {list(zip(sample_codes, sample_decoded))}")

    gmi_ref = terc['gmi_lookup'].drop_duplicates(subset=['teryt_gmina']).copy()
    gmi_ref['teryt_gmina'] = gmi_ref['teryt_gmina'].astype(str)

    merged = df.merge(
        gmi_ref[['teryt_gmina', 'teryt_powiat', 'NAZWA']],
        on='teryt_gmina', how='left'
    )

    if debug:
        print(f"  Po merge z TERC: {int(merged['teryt_powiat'].notna().sum())}/{len(merged)} dopasowanych")

    unmatched_mask = merged['teryt_powiat'].isna()
    if unmatched_mask.any():
        fb = merged.loc[unmatched_mask].drop(
            columns=['teryt_powiat', 'teryt_gmina', 'NAZWA'], errors='ignore'
        ).copy()

        fb['rodz_from_name'] = fb['nazwa_clean'].str.extract(r'\((\d)\)\s*$')[0]
        fb['nazwa_norm'] = fb['nazwa_clean'].str.replace(r'\s*\(\d\)\s*$', '', regex=True).str.lower().str.strip()
        fb['_rid'] = range(len(fb))

        ref = terc['gmi_lookup'][['teryt_woj', 'nazwa_norm', 'RODZ', 'teryt_gmina', 'teryt_powiat', 'NAZWA']].drop_duplicates().copy()
        ref['RODZ'] = ref['RODZ'].astype(str)

        fb_with_rodz = fb[fb['rodz_from_name'].notna()].copy()
        fb_with_rodz['rodz_key'] = fb_with_rodz['rodz_from_name'].astype(str)
        p1 = fb_with_rodz.merge(
            ref,
            left_on=['teryt_woj', 'nazwa_norm', 'rodz_key'],
            right_on=['teryt_woj', 'nazwa_norm', 'RODZ'],
            how='inner',
        )
        matched_rids = set(p1['_rid'].unique())

        fb_remaining = fb[~fb['_rid'].isin(matched_rids)].copy()
        ref_no_rodz = terc['gmi_lookup'][['teryt_woj', 'nazwa_norm', 'teryt_gmina', 'teryt_powiat', 'NAZWA']].drop_duplicates().copy()
        p2 = fb_remaining.merge(
            ref_no_rodz,
            on=['teryt_woj', 'nazwa_norm'],
            how='inner',
        )
        p2 = p2.drop_duplicates(subset=['_rid'], keep='first')

        fb_matched = pd.concat([p1, p2], ignore_index=True)
        fb_matched = fb_matched.drop(
            columns=['_rid', 'rodz_key', 'rodz_from_name', 'RODZ'], errors='ignore'
        )

        merged = pd.concat([merged[~unmatched_mask], fb_matched], ignore_index=True)

        if debug:
            print(f"  Po fallback po nazwie: {len(merged)} wierszy")

    return merged[merged['teryt_powiat'].notna()].drop_duplicates().copy()


def extract_surface_category(attr_str: str) -> str:
    parts = [p.strip() for p in str(attr_str).split(';')]
    for p in parts:
        p_low = p.lower()
        if any(k in p_low for k in ['ogółem', 'm2', 'm²', 'powierzchni', 'od ', 'do ', 'powyżej']):
            if 'rynek' not in p_low and 'kwarta' not in p_low and not p.startswith('['):
                return p_low
    return 'ogółem'


def process_ceny_pivot(file_path: str, typ_miary: str, terc: dict):
    df = pd.read_csv(file_path, sep=';', dtype=str, encoding='utf-8-sig')
    df = df.loc[:, ~df.columns.str.startswith('Unnamed')]
    df = map_powiat_by_name(df, terc)

    id_cols = ['Kod', 'Nazwa', 'nazwa_clean', 'is_powiat', 'teryt_powiat', 'nazwa_powiatu']
    value_cols = [c for c in df.columns if c not in id_cols and c not in ('teryt_woj', 'nazwa_norm')]

    long_df = df.melt(id_vars=id_cols, value_vars=value_cols, var_name='atrybuty', value_name='wartosc_raw')
    long_df['cena_m2_zl'] = clean_numeric(long_df['wartosc_raw'])

    # BUGFIX #2: zero w GUS oznacza "brak transakcji" – traktujemy jak NULL
    long_df['cena_m2_zl'] = long_df['cena_m2_zl'].replace(0, pd.NA)
    long_df = long_df.dropna(subset=['cena_m2_zl']).copy()

    long_df['rynek'] = long_df['atrybuty'].str.extract(r'(rynek pierwotny|rynek wtórny)', flags=re.IGNORECASE)[0].str.lower()
    long_df['powierzchnia'] = long_df['atrybuty'].apply(extract_surface_category)
    long_df['rok'] = pd.to_numeric(long_df['atrybuty'].str.extract(r'\b(20\d{2})\b')[0], errors='coerce').astype('Int64')
    long_df['kwartal'] = extract_quarter(long_df['atrybuty'])
    long_df['typ_miary'] = typ_miary

    # BUGFIX #3: filtrujemy wiersze, gdzie is_powiat=True, ale teryt_powiat nie został dopasowany
    mask_valid = long_df['is_powiat'] & long_df['teryt_powiat'].notna()
    clean_df = long_df[mask_valid][
        ['teryt_powiat', 'nazwa_powiatu', 'rok', 'kwartal', 'rynek', 'powierzchnia', 'typ_miary', 'cena_m2_zl']
    ].copy()
    clean_df = clean_df.drop_duplicates(
        subset=['teryt_powiat', 'rok', 'kwartal', 'rynek', 'powierzchnia', 'typ_miary'],
        keep='first'
    )

    reszta_df = long_df[~long_df['is_powiat']][
        ['Kod', 'Nazwa', 'rok', 'kwartal', 'rynek', 'powierzchnia', 'typ_miary', 'cena_m2_zl']
    ].rename(columns={'Kod': 'kod_raw'}).drop_duplicates().copy()

    return clean_df, reszta_df


def process_long_woj(file_path: str, terc: dict) -> pd.DataFrame:
    df = pd.read_csv(file_path, sep=';', dtype=str, encoding='utf-8-sig')
    df = df.loc[:, ~df.columns.str.startswith('Unnamed')]
    df['teryt_woj'] = df['Nazwa'].astype(str).str.strip().str.upper().map(terc['woj_name_to_code'])
    df = df[df['teryt_woj'].notna()].copy()
    df['nazwa_wojewodztwa'] = df['teryt_woj'].map(terc['woj_code_to_name'])

    val_col = _find_value_col(df.columns, exclude={'teryt_woj', 'nazwa_wojewodztwa'}, required=True)
    year_col = _find_year_col(df.columns, exclude={val_col})
    if year_col is None:
        raise KeyError(f"Brak kolumny z rokiem. Dostępne: {list(df.columns)}")

    df['wartosc'] = clean_numeric(df[val_col])
    df = df.dropna(subset=['wartosc']).copy()
    df['rok'] = pd.to_numeric(df[year_col].astype(str).str.strip(), errors='coerce').astype('Int64')

    kw_cols = [c for c in df.columns if 'Kwartał' in c or 'Kwartal' in c or 'Okres' in c]
    if kw_cols:
        df['kwartal'] = extract_quarter(df[kw_cols[0]].astype(str))
    else:
        df['kwartal'] = pd.Series([pd.NA] * len(df), dtype='Int64')
    return df


def process_long_gmi(file_path: str, terc: dict) -> pd.DataFrame:
    df = pd.read_csv(file_path, sep=';', dtype=str, encoding='utf-8-sig')
    df = df.loc[:, ~df.columns.str.startswith('Unnamed')]
    print(f"[process_long_gmi] {file_path}: {len(df)} wierszy wejściowych")

    df = map_gmina_by_name(df, terc)
    print(f"  Po map_gmina_by_name: {len(df)} wierszy")
    if df.empty:
        return pd.DataFrame(columns=[
            'teryt_gmina', 'teryt_powiat', 'teryt_woj', 'NAZWA',
            'nazwa_gminy', 'nazwa_powiatu', 'wartosc', 'rok', 'kwartal',
        ])

    df['nazwa_gminy'] = df['teryt_gmina'].map(terc['gmi_code_to_name'])
    df['nazwa_powiatu'] = df['teryt_powiat'].map(terc['pow_code_to_name'])

    val_col = _find_value_col(df.columns, exclude={'teryt_woj', 'teryt_gmina', 'teryt_powiat'}, required=True)
    year_col = _find_year_col(df.columns, exclude={val_col})
    if year_col is None:
        raise KeyError(f"Brak kolumny z rokiem. Dostępne: {list(df.columns)}")
    print(f"  Kolumna wartości: '{val_col}' | kolumna roku: '{year_col}'")

    df['wartosc'] = clean_numeric(df[val_col])
    print(f"  NaN po clean_numeric: {int(df['wartosc'].isna().sum())}/{len(df)}")
    df = df.dropna(subset=['wartosc']).copy()
    print(f"  Po dropna(wartosc): {len(df)} wierszy")

    df['rok'] = pd.to_numeric(df[year_col].astype(str).str.strip(), errors='coerce').astype('Int64')
    df['kwartal'] = pd.Series([pd.NA] * len(df), dtype='Int64')
    return df


def extract_indicator_name(raw_header: str) -> str:
    parts = [p.strip() for p in str(raw_header).split(';') if p.strip()]
    clean_parts = []
    for p in parts:
        if re.fullmatch(r'20\d{2}', p) or re.fullmatch(r'(IV|III|II|I|[1-4])\s*kwarta[łl]', p, flags=re.IGNORECASE):
            continue
        if p.startswith('[') and p.endswith(']'):
            continue
        clean_parts.append(p)
    return " - ".join(clean_parts) if clean_parts else raw_header


def process_aktywnosc_pivot(file_path: str, terc: dict) -> pd.DataFrame:
    df = pd.read_csv(file_path, sep=';', dtype=str, encoding='utf-8-sig')
    df = df.loc[:, ~df.columns.str.startswith('Unnamed')]
    df['teryt_woj'] = df['Nazwa'].astype(str).str.strip().str.upper().map(terc['woj_name_to_code'])
    df = df[df['teryt_woj'].notna()].copy()
    df['nazwa_wojewodztwa'] = df['teryt_woj'].map(terc['woj_code_to_name'])

    id_cols = ['Kod', 'Nazwa', 'teryt_woj', 'nazwa_wojewodztwa']
    value_cols = [c for c in df.columns if c not in id_cols]

    long_df = df.melt(id_vars=id_cols, value_vars=value_cols, var_name='wskaznik_raw', value_name='wartosc_raw')
    long_df['wartosc'] = clean_numeric(long_df['wartosc_raw'])
    long_df = long_df.dropna(subset=['wartosc']).copy()
    long_df['rok'] = pd.to_numeric(long_df['wskaznik_raw'].str.extract(r'\b(20\d{2})\b')[0], errors='coerce').astype('Int64')
    long_df['kwartal'] = extract_quarter(long_df['wskaznik_raw'])
    long_df['wskaznik'] = long_df['wskaznik_raw'].apply(extract_indicator_name)
    return long_df[['teryt_woj', 'nazwa_wojewodztwa', 'rok', 'kwartal', 'wskaznik', 'wartosc']].copy()


def process_demografia_xlsx(file_path: str, terc: dict) -> pd.DataFrame:
    raw = read_xlsx_safe(file_path)
    print(f"[process_demografia_xlsx] {file_path}: {len(raw)} wierszy surowych")

    kod_row_idx = None
    for i, r in raw.head(30).iterrows():
        row_str = r.astype(str).str.lower()
        if row_str.str.contains('kod|teryt', regex=True, na=False).any():
            kod_row_idx = i
            break

    if kod_row_idx is None:
        raise ValueError(
            f"Nie znaleziono wiersza nagłówka (Kod/TERYT) w {file_path}. "
            f"Pierwsze 10 wierszy:\n{raw.head(10).to_string()}"
        )

    kod_row = raw.iloc[kod_row_idx]
    has_year_in_kod_row = any(re.search(r'\b(19|20)\d{2}\b', str(c)) for c in kod_row)

    if has_year_in_kod_row:
        df_start = kod_row_idx + 1
        headers = [str(c).strip() for c in kod_row]
    else:
        next_idx = kod_row_idx + 1
        next_row = raw.iloc[next_idx]
        if not any(re.search(r'\b(19|20)\d{2}\b', str(c)) for c in next_row):
            raise ValueError(f"Nie znaleziono kolumn z latami w {file_path}. Kolumny: {[str(c) for c in kod_row]}")
        headers = []
        for i, c in enumerate(kod_row):
            prev = str(c).strip()
            nxt = str(next_row.iloc[i]).strip() if i < len(next_row) else ''
            prev_ok = prev and prev.lower() != 'nan'
            nxt_ok = nxt and nxt.lower() != 'nan'
            if prev_ok and nxt_ok and prev != nxt:
                headers.append(f"{prev} {nxt}")
            elif prev_ok:
                headers.append(prev)
            elif nxt_ok:
                headers.append(nxt)
            else:
                headers.append(f"col_{i}")
        df_start = next_idx + 1

    seen = {}
    deduped = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            deduped.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            deduped.append(h)
    headers = deduped

    print(f"  Wiersz nagłówka: {kod_row_idx} (start danych: {df_start})")
    df = raw.iloc[df_start:].copy().reset_index(drop=True)
    df.columns = headers

    kod_col = _find_col(df.columns, [r'^kod', r'teryt', r'kod'], required=True)
    nazwa_col = _find_col(df.columns, [r'nazw'], exclude={kod_col}, required=True)
    df = df[df[kod_col].notna() & (df[kod_col].astype(str).str.strip() != '')].copy()
    print(f"  Po filtrze pustych kodów: {len(df)} wierszy")

    year_cols = [c for c in df.columns
                 if c not in (kod_col, nazwa_col)
                 and re.search(r'\b(19|20)\d{2}\b', str(c))]
    if not year_cols:
        raise ValueError(f"Brak kolumn z latami w {file_path}. Kolumny: {list(df.columns)}")

    df = df.rename(columns={kod_col: 'Kod', nazwa_col: 'Nazwa'})
    df = map_gmina_by_name(df, terc)
    print(f"  Po map_gmina_by_name: {len(df)} wierszy")
    if df.empty:
        return pd.DataFrame(columns=[
            'teryt_gmina', 'nazwa_gminy', 'teryt_powiat', 'nazwa_powiatu',
            'rok', 'kwartal', 'zmiana_ludnosci_na_1000',
        ])

    df['nazwa_gminy'] = df['teryt_gmina'].map(terc['gmi_code_to_name'])
    df['nazwa_powiatu'] = df['teryt_powiat'].map(terc['pow_code_to_name'])

    long_df = df.melt(
        id_vars=['teryt_gmina', 'nazwa_gminy', 'teryt_powiat', 'nazwa_powiatu'],
        value_vars=year_cols, var_name='rok_raw', value_name='wartosc_raw',
    )
    print(f"  Po melt: {len(long_df)} wierszy")

    long_df['zmiana_ludnosci_na_1000'] = clean_numeric(long_df['wartosc_raw'])
    print(f"  NaN po clean_numeric: {int(long_df['zmiana_ludnosci_na_1000'].isna().sum())}/{len(long_df)}")
    long_df = long_df.dropna(subset=['zmiana_ludnosci_na_1000']).copy()
    print(f"  Po dropna: {len(long_df)} wierszy")

    # BUGFIX #1: pełny 4-cyfrowy rok w grupie przechwytującej
    long_df['rok'] = pd.to_numeric(
        long_df['rok_raw'].astype(str).str.extract(r'\b((?:19|20)\d{2})\b')[0], errors='coerce',
    ).astype('Int64')
    long_df['kwartal'] = pd.Series([pd.NA] * len(long_df), dtype='Int64')

    # BUGFIX #3: dedup ziarna (teryt_gmina, rok) – 12 kolizji po zmianie statusu gminy
    long_df = long_df.drop_duplicates(subset=['teryt_gmina', 'rok'], keep='first')

    return long_df[[
        'teryt_gmina', 'nazwa_gminy', 'teryt_powiat', 'nazwa_powiatu',
        'rok', 'kwartal', 'zmiana_ludnosci_na_1000',
    ]].copy()


def _safe_write(con, df: pd.DataFrame, table_name: str) -> int:
    if df is None:
        df = pd.DataFrame()

    seen = {}
    new_cols = []
    for c in df.columns:
        if c in seen:
            seen[c] += 1
            new_cols.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            new_cols.append(c)
    if list(df.columns) != new_cols:
        print(f"  ⚠️  {table_name}: deduplikacja kolumn {list(df.columns)} -> {new_cols}")
        df = df.copy()
        df.columns = new_cols

    tmp_name = f"_tmp_write_{table_name}"
    con.register(tmp_name, df)
    try:
        con.execute(f"DROP TABLE IF EXISTS {table_name}").fetchall()
        con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM {tmp_name}").fetchall()
    finally:
        try:
            con.unregister(tmp_name)
        except Exception:
            pass

    cnt = int(con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchall()[0][0])
    flag = "✓" if cnt == len(df) else "⚠️"
    print(f"  {flag} {table_name:<32} | źródło: {len(df):>8,} | w bazie: {cnt:>8,}")
    return cnt


def run_pr5_pipeline():
    print(f"Rozpoczynanie potoku GUS (Bronze -> Silver) w bazie: {DB_PATH}")
    terc = build_silver_terc(find_file('TERC_Urzedowy'))

    mediana_df, r1 = process_ceny_pivot(find_file('Mediana_zam2'), 'mediana', terc)
    srednia_df, r2 = process_ceny_pivot(find_file('Srednia_zam2'), 'srednia', terc)
    silver_ceny_mieszkan = pd.concat([mediana_df, srednia_df], ignore_index=True)
    ceny_m2_reszta = pd.concat([r1, r2], ignore_index=True).drop_duplicates()

    print(f"  Ceny mieszkań: {len(silver_ceny_mieszkan):,} wierszy "
          f"(NULL teryt_powiat: {int(silver_ceny_mieszkan['teryt_powiat'].isna().sum())}, "
          f"cena=0: {int((silver_ceny_mieszkan['cena_m2_zl'] == 0).sum())})")

    wyn_raw = process_long_woj(find_file('Średnie_wynagrodzenie'), terc)
    silver_wynagrodzenia = wyn_raw[['teryt_woj', 'nazwa_wojewodztwa', 'rok', 'kwartal', 'wartosc']].rename(
        columns={'wartosc': 'wynagrodzenie_brutto_zl'}
    )

    zasoby_raw = process_long_gmi(find_file('Ilość_mieszkań_w_gminie'), terc)
    excluded_cols = {
        'Kod', 'Nazwa', 'Rok', 'Wartosc', 'Wartość', 'Jednostka miary', 'Atrybut',
        'Kwartał', 'Kwartal', 'Okres', 'nazwa_clean', 'nazwa_norm', 'RODZ',
        'teryt_woj', 'teryt_gmina', 'teryt_powiat', 'NAZWA', 'nazwa_gminy', 'nazwa_powiatu',
        'wartosc', 'rok', 'kwartal'
    }
    kat_candidates = [c for c in zasoby_raw.columns if c not in excluded_cols]

    base_cols = ['teryt_gmina', 'nazwa_gminy', 'teryt_powiat', 'nazwa_powiatu', 'rok', 'kwartal']
    if kat_candidates:
        kat_col = kat_candidates[0]
        silver_zasoby_mieszkan = zasoby_raw[base_cols + [kat_col, 'wartosc']].rename(
            columns={kat_col: 'kategoria', 'wartosc': 'liczba_mieszkan'}
        )
    else:
        silver_zasoby_mieszkan = zasoby_raw[base_cols + ['wartosc']].assign(kategoria='ogółem').rename(
            columns={'wartosc': 'liczba_mieszkan'}
        )
    print(f"  Zasoby mieszkaniowe przed DuckDB: {len(silver_zasoby_mieszkan)} wierszy")

    uslugi_raw = process_long_woj(find_file('47KATEGORIEUSLUG'), terc)
    usl_kat_col = [c for c in uslugi_raw.columns if 'Rodzaje' in c or 'Towary' in c][-1]
    silver_ceny_uslug = uslugi_raw[['teryt_woj', 'nazwa_wojewodztwa', 'rok', 'kwartal', usl_kat_col, 'wartosc']].rename(
        columns={usl_kat_col: 'kategoria_uslugi', 'wartosc': 'cena_uslugi_zl'}
    ).copy()

    # BUGFIX #2 (c.d.): 0 zł za usługę = brak danych
    n_zeros_uslug = int((silver_ceny_uslug['cena_uslugi_zl'] == 0).sum())
    silver_ceny_uslug['cena_uslugi_zl'] = silver_ceny_uslug['cena_uslugi_zl'].replace(0, pd.NA)
    silver_ceny_uslug = silver_ceny_uslug.dropna(subset=['cena_uslugi_zl']).copy()
    print(f"  Ceny usług: wyfiltrowano {n_zeros_uslug} zer, pozostało {len(silver_ceny_uslug):,} wierszy")

    silver_aktywnosc = process_aktywnosc_pivot(find_file('Aktywność_Ekonomiczna'), terc)
    silver_demografia = process_demografia_xlsx(find_file('Zmiana_Ludności'), terc)

    print(f"\nZapis do DuckDB: {DB_PATH}")
    con = duckdb.connect(DB_PATH)

    _safe_write(con, terc['woj_df'], 'silver_terc_woj')
    _safe_write(con, terc['pow_df'], 'silver_terc_pow')
    _safe_write(con, terc['gmi_df'], 'silver_terc_gmi')
    _safe_write(con, silver_ceny_mieszkan, 'silver_ceny_mieszkan')
    _safe_write(con, silver_wynagrodzenia, 'silver_wynagrodzenia')
    _safe_write(con, silver_zasoby_mieszkan, 'silver_zasoby_mieszkan')
    _safe_write(con, silver_ceny_uslug, 'silver_ceny_uslug')
    _safe_write(con, silver_aktywnosc, 'silver_aktywnosc_ekonomiczna')
    _safe_write(con, silver_demografia, 'silver_demografia_gminy')
    _safe_write(con, ceny_m2_reszta, 'ceny_m2_reszta')

    print("\nStan bazy:")
    for (tbl_name,) in con.execute("SHOW TABLES").fetchall():
        cnt = int(con.execute(f"SELECT COUNT(*) FROM {tbl_name}").fetchall()[0][0])
        flag = "   ⚠️  PUSTA" if cnt == 0 else ""
        print(f"  - {tbl_name:<32} | Wierszy: {cnt:,}{flag}")
    con.close()


if __name__ == '__main__':
    run_pr5_pipeline()