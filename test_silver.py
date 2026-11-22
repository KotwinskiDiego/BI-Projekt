from pathlib import Path
import duckdb

DB_PATH = str(Path(__file__).resolve().parent / 'hurtownia.duckdb')


def run_tests():
    con = duckdb.connect(DB_PATH, read_only=True)
    failures = []

    def check(name: str, query: str, expected=0):
        actual = con.execute(query).fetchone()[0]
        if actual == expected:
            print(f"[PASS] {name}")
        else:
            print(f"[FAIL] {name} -> oczekiwano: {expected}, jest: {actual}")
            failures.append(name)

    print("=== 1. KOMPLETNOŚĆ TABEL I SŁOWNIKÓW TERC ===")
    check("Województwa = 16", "SELECT COUNT(*) FROM silver_terc_woj", expected=16)
    check("Powiaty = 380", "SELECT COUNT(*) FROM silver_terc_pow", expected=380)
    check("Gminy TERC > 3000", "SELECT CASE WHEN COUNT(*) > 3000 THEN 0 ELSE 1 END FROM silver_terc_gmi")

    fact_tables = [
        'silver_ceny_mieszkan',
        'silver_wynagrodzenia',
        'silver_zasoby_mieszkan',
        'silver_ceny_uslug',
        'silver_aktywnosc_ekonomiczna',
        'silver_demografia_gminy',
        'ceny_m2_reszta',
    ]
    for tbl in fact_tables:
        check(f"Tabela niepusta: {tbl}", f"SELECT CASE WHEN COUNT(*) > 0 THEN 0 ELSE 1 END FROM {tbl}")

    print("\n=== 2. BRAKI DANYCH (NULL / PUSTE STRINGI) W KLUCZACH I NAZWACH ===")
    null_checks = [
        ("silver_ceny_mieszkan", ["teryt_powiat", "nazwa_powiatu", "rok", "kwartal", "rynek", "powierzchnia", "typ_miary", "cena_m2_zl"]),
        ("silver_wynagrodzenia", ["teryt_woj", "nazwa_wojewodztwa", "rok", "wynagrodzenie_brutto_zl"]),
        ("silver_zasoby_mieszkan", ["teryt_gmina", "nazwa_gminy", "teryt_powiat", "nazwa_powiatu", "rok", "kategoria", "liczba_mieszkan"]),
        ("silver_ceny_uslug", ["teryt_woj", "nazwa_wojewodztwa", "rok", "kategoria_uslugi", "cena_uslugi_zl"]),
        ("silver_aktywnosc_ekonomiczna", ["teryt_woj", "nazwa_wojewodztwa", "rok", "kwartal", "wskaznik", "wartosc"]),
        ("silver_demografia_gminy", ["teryt_gmina", "nazwa_gminy", "teryt_powiat", "nazwa_powiatu", "rok", "zmiana_ludnosci_na_1000"]),
    ]
    for tbl, cols in null_checks:
        for col in cols:
            check(
                f"Brak NULL/pustych w {tbl}.{col}",
                f"SELECT COUNT(*) FROM {tbl} WHERE {col} IS NULL OR TRIM(CAST({col} AS VARCHAR)) = ''"
            )

    print("\n=== 3. POPRAWNOŚĆ CZASU (ROK, KWARTAŁ) I ZAKRESÓW LICZBOWYCH ===")
    for tbl in fact_tables:
        check(
            f"Rok w zakresie 2000-2026 w {tbl}",
            f"SELECT COUNT(*) FROM {tbl} WHERE rok IS NULL OR rok < 2000 OR rok > 2026"
        )
        check(
            f"Kwartał w {{1,2,3,4}} (jeśli podany) w {tbl}",
            f"SELECT COUNT(*) FROM {tbl} WHERE kwartal IS NOT NULL AND kwartal NOT IN (1, 2, 3, 4)"
        )

    check("Ceny m2 > 0", "SELECT COUNT(*) FROM silver_ceny_mieszkan WHERE cena_m2_zl <= 0")
    check("Wynagrodzenia > 0", "SELECT COUNT(*) FROM silver_wynagrodzenia WHERE wynagrodzenie_brutto_zl <= 0")
    check("Liczba mieszkań >= 0", "SELECT COUNT(*) FROM silver_zasoby_mieszkan WHERE liczba_mieszkan < 0")
    check("Ceny usług > 0", "SELECT COUNT(*) FROM silver_ceny_uslug WHERE cena_uslugi_zl <= 0")
    check(
        "Demografia w realistycznym zakresie (-200..200)",
        "SELECT COUNT(*) FROM silver_demografia_gminy WHERE zmiana_ludnosci_na_1000 < -200 OR zmiana_ludnosci_na_1000 > 200"
    )

    print("\n=== 4. INTEGRALNOŚĆ REFERENCYJNA (KLUCZE OBCE DO TERC) ===")
    fk_checks = [
        ("silver_ceny_mieszkan.teryt_powiat -> TERC", "silver_ceny_mieszkan", "teryt_powiat", "silver_terc_pow", "teryt_powiat"),
        ("silver_wynagrodzenia.teryt_woj -> TERC", "silver_wynagrodzenia", "teryt_woj", "silver_terc_woj", "teryt_woj"),
        ("silver_ceny_uslug.teryt_woj -> TERC", "silver_ceny_uslug", "teryt_woj", "silver_terc_woj", "teryt_woj"),
        ("silver_aktywnosc_ekonomiczna.teryt_woj -> TERC", "silver_aktywnosc_ekonomiczna", "teryt_woj", "silver_terc_woj", "teryt_woj"),
        ("silver_zasoby_mieszkan.teryt_gmina -> TERC", "silver_zasoby_mieszkan", "teryt_gmina", "silver_terc_gmi", "teryt_gmina"),
        ("silver_demografia_gminy.teryt_gmina -> TERC", "silver_demografia_gminy", "teryt_gmina", "silver_terc_gmi", "teryt_gmina"),
    ]
    for label, src_tbl, src_col, ref_tbl, ref_col in fk_checks:
        check(
            f"FK {label}",
            f"""
            SELECT COUNT(*) FROM {src_tbl} s
            LEFT JOIN {ref_tbl} r ON s.{src_col} = r.{ref_col}
            WHERE r.{ref_col} IS NULL
            """
        )

    print("\n=== 5. UNIKALNOŚĆ ZIARNA (BRAK DUPLIKATÓW) ===")
    grain_checks = [
        ("silver_terc_woj (teryt_woj)", "silver_terc_woj", "teryt_woj"),
        ("silver_terc_pow (teryt_powiat)", "silver_terc_pow", "teryt_powiat"),
        ("silver_terc_gmi (teryt_gmina)", "silver_terc_gmi", "teryt_gmina"),
        ("silver_demografia_gminy (gmina, rok)", "silver_demografia_gminy", "teryt_gmina, rok"),
        ("silver_zasoby_mieszkan (gmina, rok, kategoria)", "silver_zasoby_mieszkan", "teryt_gmina, rok, kategoria"),
        ("silver_ceny_mieszkan (powiat, rok, kw, rynek, pow, miara)", "silver_ceny_mieszkan", "teryt_powiat, rok, kwartal, rynek, powierzchnia, typ_miary"),
        ("silver_wynagrodzenia (woj, rok, kw)", "silver_wynagrodzenia", "teryt_woj, rok, COALESCE(kwartal, 0)"),
        ("silver_ceny_uslug (woj, rok, kw, usluga)", "silver_ceny_uslug", "teryt_woj, rok, COALESCE(kwartal, 0), kategoria_uslugi"),
        ("silver_aktywnosc_ekonomiczna (woj, rok, kw, wskaznik)", "silver_aktywnosc_ekonomiczna", "teryt_woj, rok, kwartal, wskaznik"),
    ]
    for label, tbl, cols in grain_checks:
        check(
            f"Unikalność: {label}",
            f"""
            SELECT COUNT(*) FROM (
                SELECT {cols}, COUNT(*) AS cnt
                FROM {tbl}
                GROUP BY {cols}
                HAVING COUNT(*) > 1
            )
            """
        )

    con.close()
    print("\n=========================================")
    if failures:
        print(f"WYNIK: NIEZALICZONE ({len(failures)} błędów)")
        raise SystemExit(1)
    else:
        print("WYNIK: WSZYSTKIE TESTY WARSTWY SILVER ZALICZONE (100% PASS)")


if __name__ == '__main__':
    run_tests()