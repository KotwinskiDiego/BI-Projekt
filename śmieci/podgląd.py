import os
import duckdb
import pandas as pd

DB_PATH = '../hurtownia.duckdb'

# Ustawienia czytelnego wyświetlania tabel w terminalu (bez ucinania kolumn)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)
pd.set_option('display.max_colwidth', 45)


def inspect_duckdb():
    if not os.path.exists(DB_PATH):
        print(f"Nie znaleziono pliku bazy '{DB_PATH}'. Uruchom najpierw skrypt ETL.")
        return

    con = duckdb.connect(DB_PATH, read_only=True)
    tables = [row[0] for row in con.execute("SHOW TABLES").fetchall()]

    if not tables:
        print("Baza jest pusta — brak tabel do wyświetlenia.")
        con.close()
        return

    print("=" * 95)
    print(f"PODGLĄD DANYCH W BAZIE: {DB_PATH} (Liczba tabel: {len(tables)})")
    print("=" * 95 + "\n")

    for tbl in tables:
        row_count = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        schema_df = con.execute(f"DESCRIBE {tbl}").df()[['column_name', 'column_type']]
        cols_info = ", ".join(f"{r['column_name']} ({r['column_type']})" for _, r in schema_df.iterrows())

        print(f"TABELA: {tbl} | Liczba wierszy: {row_count:,}")
        print(f"Schemat: {cols_info}")
        print("-" * 95)

        sample_df = con.execute(f"SELECT * FROM {tbl} LIMIT 5").df()
        if sample_df.empty:
            print("  (Brak rekordów w tabeli)")
        else:
            print(sample_df.to_string(index=False))
        print("\n" + "=" * 95 + "\n")

    # Tryb interaktywny do szybkiego sprawdzania konkretnych tabel lub zapytań
    while True:
        cmd = input("Wpisz nazwę tabeli (pokaże 20 wierszy), zapytanie SQL lub wciśnij Enter aby wyjść: ").strip()
        if not cmd:
            break
        try:
            if cmd.upper().startswith(("SELECT", "DESCRIBE", "SHOW", "WITH", "SUMMARIZE")):
                query = cmd
            else:
                query = f"SELECT * FROM {cmd} LIMIT 20"
            res_df = con.execute(query).df()
            print("\n" + res_df.to_string(index=False) + "\n")
        except Exception as e:
            print(f"Błąd zapytania: {e}\n")

    con.close()


if __name__ == '__main__':
    inspect_duckdb()