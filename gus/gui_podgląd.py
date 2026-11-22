import sys
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path

import duckdb

# ─── Catppuccin Mocha ───
BG      = "#1e1e2e"
BG_DARK = "#181825"
BG_MED  = "#24273a"
FG      = "#cdd6f4"
FG_DIM  = "#a6adc8"
ACCENT  = "#89b4fa"
BORDER  = "#313244"
HOVER   = "#45475a"
SEL_FG  = "#11111b"

PAGE_SIZE = 500  # ile wierszy doładowywać przy scrollu


class DuckDBExplorer:
    def __init__(self, root: tk.Tk, db_path: str):
        self.root = root
        self.con = duckdb.connect(db_path, read_only=True)
        self.db_path = db_path

        self.current_table = None
        self.current_where = ""
        self.total_rows = 0
        self.loaded_rows = 0
        self.columns: list[str] = []
        self.loading = False
        self._table_names: list[str] = []

        root.title(f"DuckDB Explorer — {Path(db_path).name}")
        root.geometry("1280x820")
        root.configure(bg=BG)
        root.minsize(900, 500)

        self._setup_style()
        self._build_ui()
        self._load_tables()

    # ─── styl ───
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=BG, foreground=FG,
                        fieldbackground=BG_DARK, borderwidth=0)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Title.TLabel", background=BG, foreground=ACCENT,
                        font=("Segoe UI", 16, "bold"))
        style.configure("Info.TLabel", background=BG, foreground=FG_DIM,
                        font=("Segoe UI", 10))
        style.configure("TButton", background=HOVER, foreground=FG,
                        borderwidth=0, focuscolor=BG, padding=(12, 6),
                        font=("Segoe UI", 10))
        style.map("TButton",
                  background=[("active", ACCENT), ("pressed", ACCENT)],
                  foreground=[("active", SEL_FG), ("pressed", SEL_FG)])
        style.configure("TEntry", fieldbackground=BG_DARK, foreground=FG,
                        insertcolor=FG, bordercolor=BORDER, padding=(8, 6))
        style.map("TEntry",
                  bordercolor=[("focus", ACCENT)],
                  lightcolor=[("focus", ACCENT)],
                  darkcolor=[("focus", ACCENT)])

        style.configure("Treeview",
                        background=BG_DARK, foreground=FG,
                        fieldbackground=BG_DARK, rowheight=26,
                        borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading",
                        background=BG_MED, foreground=FG, borderwidth=0,
                        font=("Segoe UI", 10, "bold"), padding=(8, 6))
        style.map("Treeview.Heading", background=[("active", HOVER)])
        style.map("Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", SEL_FG)])

        for orient in ("Vertical", "Horizontal"):
            style.configure(f"{orient}.TScrollbar",
                            background=HOVER, troughcolor=BG_DARK,
                            borderwidth=0, arrowcolor=FG, width=12)
            style.map(f"{orient}.TScrollbar",
                      background=[("active", ACCENT)])

    # ─── layout ───
    def _build_ui(self):
        # Pasek statusu — pakujemy PIERWSZY, żeby zarezerwował miejsce na dole
        self.status_var = tk.StringVar(value=f"Połączono z {self.db_path}")
        tk.Label(self.root, textvariable=self.status_var,
                 bg=BG_DARK, fg=FG_DIM, anchor="w",
                 padx=12, pady=6,
                 font=("Segoe UI", 9)).pack(side="bottom", fill="x")

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        # ─── nagłówek ───
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))

        ttk.Label(header, text="🦆  DuckDB Explorer",
                  style="Title.TLabel").pack(side="left")

        ttk.Button(header, text="Otwórz...",
                   command=self._open_file).pack(side="right", padx=(6, 0))
        ttk.Button(header, text="Odśwież",
                   command=self._refresh).pack(side="right", padx=(6, 0))

        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(header, textvariable=self.search_var, width=40)
        search_entry.pack(side="right", padx=(6, 0))
        search_entry.bind("<Return>", lambda e: self._apply_filter())
        ttk.Label(header, text="Filtruj:",
                  style="Info.TLabel").pack(side="right")

        # ─── ciało ───
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)

        # lewy panel: lista tabel
        left = tk.Frame(body, bg=BG_DARK,
                        highlightthickness=1, highlightbackground=BORDER)
        left.pack(side="left", fill="y", padx=(0, 12))

        tk.Label(left, text="TABELE", bg=BG_DARK, fg=FG_DIM,
                 font=("Segoe UI", 9, "bold"),
                 anchor="w", padx=12, pady=8).pack(fill="x")

        list_frame = tk.Frame(left, bg=BG_DARK)
        list_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self.table_list = tk.Listbox(
            list_frame, bg=BG_DARK, fg=FG,
            selectbackground=ACCENT, selectforeground=SEL_FG,
            borderwidth=0, highlightthickness=0,
            activestyle="none", font=("Segoe UI", 11),
            width=24,
        )
        self.table_list.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(list_frame, orient="vertical",
                            command=self.table_list.yview)
        lsb.pack(side="right", fill="y")
        self.table_list.configure(yscrollcommand=lsb.set)
        self.table_list.bind("<<ListboxSelect>>", self._on_table_selected)

        # prawy panel: info + tabela
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        self.info_var = tk.StringVar(value="Wybierz tabelę z listy")
        ttk.Label(right, textvariable=self.info_var,
                  style="Info.TLabel").pack(fill="x", pady=(0, 6))

        tree_frame = tk.Frame(right, bg=BG_DARK,
                              highlightthickness=1, highlightbackground=BORDER)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, show="headings",
                                 selectmode="browse")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",
                            command=self.tree.xview)
        self.tree.configure(xscrollcommand=hsb.set,
                            yscrollcommand=self._on_yscroll)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.vsb = vsb

    # ─── akcje ───
    def _load_tables(self):
        self.table_list.delete(0, "end")
        try:
            rows = self.con.execute("SHOW TABLES").fetchall()
            self._table_names = sorted(r[0] for r in rows)
            for name in self._table_names:
                self.table_list.insert("end", f"  {name}")
            self.status_var.set(f"Znaleziono {len(rows)} tabel")
        except Exception as e:
            self.status_var.set(f"Błąd: {e}")

    def _on_table_selected(self, event=None):
        sel = self.table_list.curselection()
        if not sel:
            return
        self._load_table(self._table_names[sel[0]])

    def _load_table(self, name: str):
        try:
            self.current_table = name
            self.current_where = self._build_filter_where(name)
            where_sql = f" WHERE {self.current_where}" if self.current_where else ""

            info = self.con.execute(f'DESCRIBE "{name}"').fetchall()
            self.columns = [r[0] for r in info]

            self.total_rows = self.con.execute(
                f'SELECT COUNT(*) FROM "{name}"{where_sql}'
            ).fetchone()[0]
            self.loaded_rows = 0

            self.tree.delete(*self.tree.get_children())
            self.tree["columns"] = self.columns
            self.tree["show"] = "headings"
            for col in self.columns:
                self.tree.heading(col, text=col, anchor="w")
                self.tree.column(col, width=160, minwidth=80,
                                 anchor="w", stretch=False)

            self._load_more()

            rows_str = f"{self.total_rows:,}".replace(",", " ")
            filter_info = (f"  (filtr: „{self.search_var.get()}”)"
                           if self.current_where else "")
            self.info_var.set(
                f"Tabela: {name} — {rows_str} wierszy, "
                f"{len(self.columns)} kolumn{filter_info}"
            )
        except Exception as e:
            self.info_var.set(f"Błąd: {e}")

    def _build_filter_where(self, name: str) -> str:
        text = self.search_var.get().strip()
        if not text:
            return ""
        safe = text.replace("'", "''")
        cols = [r[0] for r in
                self.con.execute(f'DESCRIBE "{name}"').fetchall()]
        parts = [f'CAST("{c}" AS VARCHAR) ILIKE \'%{safe}%\'' for c in cols]
        return " OR ".join(parts)

    def _load_more(self):
        if self.loading or not self.current_table:
            return
        if self.loaded_rows >= self.total_rows:
            return
        self.loading = True
        try:
            where_sql = (f" WHERE {self.current_where}"
                         if self.current_where else "")
            q = (f'SELECT * FROM "{self.current_table}"{where_sql} '
                 f'LIMIT {PAGE_SIZE} OFFSET {self.loaded_rows}')
            rows = self.con.execute(q).fetchall()
            for row in rows:
                vals = ["" if v is None else str(v) for v in row]
                self.tree.insert("", "end", values=vals)
            self.loaded_rows += len(rows)
            self.status_var.set(
                f"Załadowano {self.loaded_rows:,} / {self.total_rows:,} "
                f"wierszy".replace(",", " ")
            )
        except Exception as e:
            self.status_var.set(f"Błąd: {e}")
        finally:
            self.loading = False

    def _on_yscroll(self, first, last):
        self.vsb.set(first, last)
        try:
            if (float(last) >= 0.95
                    and self.loaded_rows < self.total_rows):
                self._load_more()
        except (ValueError, TypeError):
            pass

    def _apply_filter(self):
        if self.current_table:
            self._load_table(self.current_table)

    def _refresh(self):
        current = self.current_table
        self._load_tables()
        if current and current in self._table_names:
            idx = self._table_names.index(current)
            self.table_list.selection_set(idx)
            self.table_list.see(idx)
            self._load_table(current)

    def _open_file(self):
        path = filedialog.askopenfilename(
            title="Wybierz bazę DuckDB",
            filetypes=[("DuckDB", "*.duckdb *.db *.ddb"),
                       ("Wszystkie pliki", "*.*")],
        )
        if not path:
            return
        try:
            self.con.close()
        except Exception:
            pass
        for child in self.root.winfo_children():
            child.destroy()
        self.__init__(self.root, path)


def _find_default_db() -> str | None:
    if len(sys.argv) > 1:
        return sys.argv[1]
    for pat in ("*.duckdb", "*.db", "*.ddb"):
        found = sorted(Path.cwd().glob(pat))
        if found:
            return str(found[0])
    return None


def main():
    root = tk.Tk()
    root.withdraw()
    db = _find_default_db()
    if not db:
        db = filedialog.askopenfilename(
            title="Wybierz bazę DuckDB",
            filetypes=[("DuckDB", "*.duckdb *.db *.ddb"),
                       ("Wszystkie pliki", "*.*")],
        )
    if not db:
        return
    root.deiconify()
    DuckDBExplorer(root, db)
    root.mainloop()


if __name__ == "__main__":
    main()