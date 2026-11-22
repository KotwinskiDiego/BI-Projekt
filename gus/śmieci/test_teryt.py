import os
import csv

FOLDER_PATH = '..'


# Oczekiwane prawidłowe długości kodów TERYT w standardzie GUS:
# 2 cyfry = Województwo (np. "02")
# 4 cyfry = Powiat (np. "0201")
# 7 cyfr  = Gmina (np. "0201011")
# 12 cyfr = Pełny ciąg BDL z dopełnieniem zerami (np. "020000000000")

def check_teryt_issues():
    print("==================================================================")
    print("    TEST INTEGRALNOŚCI GEOGRAFICZNEJ I ZGUBIONYCH ZER W TERYT    ")
    print("==================================================================\n")

    files = [f for f in sorted(os.listdir(FOLDER_PATH)) if f.endswith('.csv')]

    for filename in files:
        file_path = os.path.join(FOLDER_PATH, filename)

        with open(file_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            first_line = f.readline()
            sep = ';' if ';' in first_line else ','
            f.seek(0)

            reader = csv.reader(f, delimiter=sep)
            headers = next(reader, None)

            if not headers or 'Kod' not in headers and 'WOJ' not in headers:
                continue

            print(f"📄 BADANIE PLIKU: {filename}")

            short_codes_found = []
            total_rows = 0

            for row_idx, row in enumerate(reader, start=2):
                if not row or not row[0].strip():
                    continue
                total_rows += 1

                raw_code = row[0].strip()
                name = row[1].strip() if len(row) > 1 else "Brak nazwy"
                code_len = len(raw_code)

                # Wykrywanie przypadków zgubienia zera wiodącego:
                # - Długość 3 cyfr zamiast 4 (np. "201" zamiast "0201" dla woj. dolnośląskiego)
                # - Długość 1 cyfry zamiast 2 (np. "2" zamiast "02")
                # - Długość 6 cyfr zamiast 7 (np. "20101" zamiast "0201011")
                # - Długość 11 cyfr zamiast 12 (np. "20000000000" zamiast "020000000000")
                if code_len in (1, 3, 6, 11):
                    # Wyznaczamy prawidłowy odpowiednik przez zfill
                    if code_len == 1:
                        correct_code = raw_code.zfill(2)
                    elif code_len == 3:
                        correct_code = raw_code.zfill(4)
                    elif code_len == 6:
                        correct_code = raw_code.zfill(7)
                    else:
                        correct_code = raw_code.zfill(12)

                    short_codes_found.append((row_idx, raw_code, correct_code, name))

            if short_codes_found:
                print(f"   ⚠️ ZNALAZŁEM {len(short_codes_found)} WIERSZY ZE ZGUBIONYM ZEREM (na {total_rows} wierszy)!")
                print("   Przykłady konkretnych uszkodzeń:")
                for r_idx, bad, fixed, nzm in short_codes_found[:6]:
                    print(
                        f"     • Wiersz {r_idx:<5} | Surowy Kod w CSV: '{bad}' (dł. {len(bad)}) ❌  --> Powinno być: '{fixed}' ({nzm})")
            else:
                print("   ✅ Brak brakujących zer (wszystkie kody mają pełną długość).")

            print("-" * 66)


if __name__ == "__main__":
    check_teryt_issues()
