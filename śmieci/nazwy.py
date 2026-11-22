import os
import csv

FOLDER_PATH = 'data/bronze'

print(f"=== SPIS PLIKÓW I STRUKTURA DANYCH ({os.path.abspath(FOLDER_PATH)}) ===\n")

for filename in sorted(os.listdir(FOLDER_PATH)):
    file_path = os.path.join(FOLDER_PATH, filename)

    # Pomijamy ukryte pliki i foldery
    if os.path.isfile(file_path) and not filename.startswith('.'):
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        print(f"📄 Plik: {filename} ({size_mb:.2f} MB)")