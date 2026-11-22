# Pull Finwire API to DuckDB

## Overview

Nowy skrypt `pull_finwire_to_duckdb.py` zaciąga dane z API Finwire i przechowuje je w DuckDB na dłużej (trwale). Dane są organizowane w specjalną strukturę z rozdzieleniem warstwy danych od metadanych.

## Ilość zaciąganych danych

**Wszystkie dostępne dane z każdego endpointa:**

| Dataset | Endpoint | Rekordy |
|---------|----------|---------|
| **snapshot** | `/v1/snapshot` | 39 (rozpakowane z sekcji: interestRates, inflation, currencies, creditStress, barometry) |
| **interest-rates** | `/v1/series/interest-rate` | 5 (5 stóp procentowych NBP) |
| **cpi-monthly** | `/v1/series/cpi?type=monthly&limit=600` | 538 (452 miesiące CPI) |
| **cpi-yearly** | `/v1/series/cpi?type=yearly` | 44 (44 lata CPI) |
| **fx-table** | `/v1/series/fx` | 31 (pary walut) |
| **public-debt** | `/v1/public-debt` | 44 (rekordy długu publicznego) |

**Razem: 701 rekorów zamiast 6!**

## Architektura

### Warstwy danych

1. **Warstwa danych (flattened columns)**
   - Każdy rekord z API jest rozpakowywany rekurencyjnie
   - Zagnieżdżone struktury są spłaszczane z prefiksem `f_`
   - Przykład: `record.inflation.entries` → `f_inflation_entries`
   - Arrays są spłaszczane z indeksami (np. `f_sparkline_0`, `f_sparkline_1`, ...) aż do głębokości 3
   - Głębokie struktury (> 3 poziomy) przechowywane jako JSON strings

2. **Warstwa metadanych (base columns)**
   - `run_id` - ID przebiegu (UUID)
   - `pulled_at` - Timestamp zaciągnięcia danych
   - `dataset_id` - ID datasetu z konfiguracji
   - `endpoint` - Endpoint API
   - `record_index` - Numer rekordu w odpowiedzi (dla trackowania porządku)
   - `record_id`, `record_code`, `record_date`, `record_period` - Metadane z oryginalnego rekordu (jeśli dostępne)
   - `record` - Pełny JSON oryginalnego rekordu (dla audytu i dostępu do raw danych)
   - `response_info` - Metadane odpowiedzi API (summary, meta z timestamp i source info)

3. **Specjalne pola**
   - `_section` - Dla snapshot: nazwa sekcji (interestRates, inflation, currencies, itp.)
   - `_index` - Dla snapshot: indeks w sekcji

### Tabele systemowe

- **finwire_dataset_catalog** - Katalog datasetów z informacją o tabelach i ostatnim zaciągnięciu
- **finwire_pull_errors** - Logi błędów zaciągnięcia

## Użycie

### Instalacja

```bash
pip install -r requirements.txt
```

### Zaciągnięcie wszystkich datasetów

```bash
python pull_finwire_to_duckdb.py
```

Opcje:
- `--db-path` - Ścieżka do pliku DuckDB (domyślnie: `finwire_data.duckdb`)
- `--api-base-url` - URL API (domyślnie: `https://public-api.finwire.pl`)
- `--limit N` - Zaciągnięcie tylko N datasetów (do testowania)
- `--delay-seconds 1.0` - Opóźnienie między żądaniami
- `--timeout-seconds 90` - Timeout żądań HTTP
- `--max-retries 5` - Liczba powtórzeń przy błędach

### Przykłady

```bash
# Zaciągnąć tylko 1 dataset (snapshot) do testowania
python pull_finwire_to_duckdb.py --limit 1

# Zaciągnąć z powolniejszym tempem (mniej obciążenia na API)
python pull_finwire_to_duckdb.py --delay-seconds 2.0

# Zaciągnąć do innego pliku bazy
python pull_finwire_to_duckdb.py --db-path /tmp/finwire_backup.duckdb
```

## Struktura bazy

### Datasets

- `finwire_ds_snapshot` - Zbiorcze dane rynkowe (39 rekordów)
- `finwire_ds_interest_rates` - Stopy procentowe NBP (5 rekordów)
- `finwire_ds_cpi_monthly` - CPI (miesięczne) - 538 rekordów
- `finwire_ds_cpi_yearly` - CPI (roczne) - 44 rekordy
- `finwire_ds_fx_table` - Kursy walut (31 rekordów)
- `finwire_ds_public_debt` - Dług publiczny (44 rekordy)

## Zapytania

### Sprawdzenie dostępnych datasetów

```python
import duckdb

conn = duckdb.connect('finwire_data.duckdb')

# Wyświetlić katalog
result = conn.execute("""
    SELECT dataset_id, table_name, record_count, last_pulled_at
    FROM finwire_dataset_catalog
    ORDER BY last_pulled_at DESC
""").fetchall()

for row in result:
    print(f"{row[0]:20} -> {row[1]:25} ({row[2]} records, pulled at {row[3]})")
```

### Eksploracja struktury tabeli

```python
# Wyświetlić wszystkie kolumny
result = conn.execute("PRAGMA table_info(finwire_ds_interest_rates)").fetchall()
for col in result:
    print(f"  {col[1]}: {col[2]}")
```

### Pobieranie danych z raw JSON

```python
# Dostęp do oryginalnego JSON (kolumna 'record')
result = conn.execute("""
    SELECT record FROM finwire_ds_snapshot LIMIT 1
""").fetchall()

import json
raw_data = json.loads(result[0][0]) if result else {}
```

### Zapytania na danych

```python
# Ostatnie CPI YoY
result = conn.execute("""
    SELECT record_period, f_yoy
    FROM finwire_ds_cpi_monthly
    ORDER BY record_period DESC
    LIMIT 5
""").fetchall()

# Wszystkie stopy procentowe
result = conn.execute("""
    SELECT f_shortname, f_value, f_change, f_date
    FROM finwire_ds_interest_rates
    ORDER BY f_date DESC, f_shortname
""").fetchall()

# Kursy walut
result = conn.execute("""
    SELECT f_code, f_mid, f_bid, f_ask
    FROM finwire_ds_fx_table
    ORDER BY f_code
""").fetchall()
```

## Helper - explore_duckdb.py

Narzędzie do eksploracji bazy:

```bash
# Pokaż katalog datasetów
python explore_duckdb.py

# Pokaż schemat tabeli
python explore_duckdb.py --table finwire_ds_cpi_monthly

# Pokaż 3 przykładowe wiersze
python explore_duckdb.py --table finwire_ds_interest_rates --sample 3

# Uruchom custom query
python explore_duckdb.py --query "SELECT COUNT(*) as cnt FROM finwire_ds_cpi_monthly WHERE f_yoy > 3"
```

## Notatki

- DuckDB przechowuje dane w pliku `finwire_data.duckdb` - trwałe i niezależne od PostgreSQL
- Każde zaciągnięcie dodaje nowe rekordy (nie nadpisuje - `ON CONFLICT` aktualizuje tylko metadane w katalogu)
- Kolumna `record` zawiera pełny JSON dla audytu i odzyskiwania danych
- Flattened columns (`f_*`) ułatwiają SQL queries bez konieczności przetwarzania JSON
- Indeksy automatycznie tworzone na `dataset_id` i `pulled_at` dla szybkiego dostępu
- Dużo więcej danych niż przy naiwnym podejściu (701 rekorów vs 6!)

