#!/usr/bin/env python3
"""
ads.txt / app-ads.txt Analyzer  (versión completa, 100% tkinter)
=================================================================
Proyecto AdOps: analiza un ads.txt o app-ads.txt y muestra

  * DIRECT vs RESELLER (cantidad y %)
  * duplicados exactos y conflictos (mismo ID declarado DIRECT y RESELLER)
  * agrupación por plataforma (SSP / exchange) con gráficos
  * líneas inválidas y variables (OWNERDOMAIN, MANAGERDOMAIN, CONTACT...)
  * observaciones automáticas (buenas prácticas)
  * verificación contra el sellers.json de cada plataforma
  * exportación a CSV y reporte TXT

Uso:
  python ads_txt_analyzer_completo.py                  -> abre la interfaz y pide el archivo
  python ads_txt_analyzer_completo.py --domain sitio.com [--app] [--csv out.csv]
  python ads_txt_analyzer_completo.py --file ads.txt
  python ads_txt_analyzer_completo.py --domain sitio.com --verify google.com

Requiere solo la librería estándar de Python 3.8+ (tkinter incluido).
En Linux, si falta tkinter:  sudo apt install python3-tk
"""

import argparse
import csv
import json
import sys
import threading
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field

UA = "Mozilla/5.0 (AdsTxtAnalyzer/2.0)"
VARIABLES = {"CONTACT", "SUBDOMAIN", "OWNERDOMAIN", "MANAGERDOMAIN", "INVENTORYPARTNERDOMAIN"}
RELATIONSHIPS = {"DIRECT", "RESELLER"}

# Algunas plataformas publican el sellers.json fuera de /sellers.json
SELLERS_URL_OVERRIDES = {
    "google.com": "https://storage.googleapis.com/adx-rtb-dashboard/sellers.json",
}

C_DIRECT = "#2e8b57"
C_RESELLER = "#3b6fb6"


# =============================================================================
# LÓGICA (independiente de la interfaz)
# =============================================================================
@dataclass
class Record:
    line_no: int
    domain: str
    account_id: str
    relationship: str
    cert_id: str
    raw: str


@dataclass
class Analysis:
    records: list = field(default_factory=list)
    variables: list = field(default_factory=list)   # (line_no, clave, valor)
    errors: list = field(default_factory=list)      # (line_no, motivo, raw)
    duplicates: list = field(default_factory=list)  # ((dom, id, rel), [lineas])
    conflicts: list = field(default_factory=list)   # (dom, id, [lineas])
    total_lines: int = 0

    @property
    def direct(self):
        return sum(1 for r in self.records if r.relationship == "DIRECT")

    @property
    def reseller(self):
        return sum(1 for r in self.records if r.relationship == "RESELLER")

    def by_platform(self):
        groups = defaultdict(lambda: {"direct": 0, "reseller": 0, "ids": set()})
        for r in self.records:
            g = groups[r.domain]
            g["direct" if r.relationship == "DIRECT" else "reseller"] += 1
            g["ids"].add(r.account_id)
        rows = [{"platform": d, "total": g["direct"] + g["reseller"], "direct": g["direct"],
                 "reseller": g["reseller"], "unique_ids": len(g["ids"])}
                for d, g in groups.items()]
        return sorted(rows, key=lambda x: (-x["total"], x["platform"]))

    def variable(self, key):
        return [v for _, k, v in self.variables if k == key]

    def observations(self):
        """Observaciones automáticas de buenas prácticas."""
        obs = []
        n = len(self.records)
        if n == 0:
            return ["No se encontraron registros válidos en el archivo."]
        if not self.variable("OWNERDOMAIN"):
            obs.append("No declara OWNERDOMAIN (recomendado por IAB Tech Lab en ads.txt 1.1).")
        if not self.variable("CONTACT"):
            obs.append("No declara CONTACT (dato de contacto recomendado).")
        if self.direct == 0:
            obs.append("No hay ninguna línea DIRECT: todo el inventario figura vía revendedores.")
        elif self.reseller / n > 0.8:
            obs.append(f"Más del 80% de las líneas son RESELLER ({self.reseller / n:.0%}): cadena de venta larga.")
        if self.duplicates:
            obs.append(f"Hay {len(self.duplicates)} línea(s) duplicada(s): conviene limpiarlas.")
        if self.conflicts:
            obs.append(f"Hay {len(self.conflicts)} ID(s) declarados a la vez como DIRECT y RESELLER: revisar.")
        if self.errors:
            obs.append(f"Hay {len(self.errors)} línea(s) inválida(s) que los crawlers van a ignorar.")
        no_cert = sum(1 for r in self.records if not r.cert_id)
        if no_cert:
            obs.append(f"{no_cert} línea(s) sin TAG-ID (campo opcional, pero ayuda a identificar la plataforma).")
        top = self.by_platform()[0]
        if top["total"] / n > 0.4 and len(self.by_platform()) > 1:
            obs.append(f"Alta concentración: {top['platform']} concentra {top['total'] / n:.0%} de las líneas.")
        if not obs:
            obs.append("Sin observaciones: el archivo se ve prolijo.")
        return obs


def parse_ads_txt(text: str) -> Analysis:
    res = Analysis()
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    res.total_lines = len(lines)

    for i, raw in enumerate(lines, start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        if "=" in line and "," not in line.split("=", 1)[0]:
            key, value = line.split("=", 1)
            key = key.strip().upper()
            if key in VARIABLES:
                res.variables.append((i, key, value.strip()))
            else:
                res.errors.append((i, f"Variable desconocida: {key}", raw))
            continue

        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            res.errors.append((i, "Menos de 3 campos", raw))
            continue
        domain, account_id, rel = parts[0].lower(), parts[1], parts[2].upper()
        cert = parts[3] if len(parts) > 3 else ""
        if rel not in RELATIONSHIPS:
            res.errors.append((i, f"Relación inválida: '{parts[2]}'", raw))
            continue
        if not domain or not account_id:
            res.errors.append((i, "Dominio o ID vacío", raw))
            continue
        res.records.append(Record(i, domain, account_id, rel, cert, raw.strip()))

    seen = defaultdict(list)
    for r in res.records:
        seen[(r.domain, r.account_id, r.relationship)].append(r.line_no)
    res.duplicates = [(k, v) for k, v in seen.items() if len(v) > 1]

    rels = defaultdict(lambda: {"DIRECT": [], "RESELLER": []})
    for r in res.records:
        rels[(r.domain, r.account_id)][r.relationship].append(r.line_no)
    res.conflicts = [(d, a, v["DIRECT"] + v["RESELLER"])
                     for (d, a), v in rels.items() if v["DIRECT"] and v["RESELLER"]]
    return res


def _http_get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace"), resp.geturl()


def clean_domain(domain: str) -> str:
    d = domain.strip().lower()
    for p in ("https://", "http://"):
        if d.startswith(p):
            d = d[len(p):]
    return d.split("/")[0]


def fetch_ads_txt(domain: str, app: bool = False, timeout: int = 20):
    """Descarga /ads.txt o /app-ads.txt. Devuelve (texto, url_final)."""
    domain = clean_domain(domain)
    filename = "app-ads.txt" if app else "ads.txt"
    last = None
    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}/{filename}"
        try:
            text, final = _http_get(url, timeout)
            head = text.lstrip()[:200].lower()
            if head.startswith("<!doctype") or head.startswith("<html"):
                last = "la respuesta es una página HTML (¿404 o redirección?)"
                continue
            return text, final
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"No se pudo descargar {filename} de {domain}: {last}")


def fetch_sellers_json(platform: str, custom_url: str = "", timeout: int = 90):
    """Descarga el sellers.json de una plataforma. Devuelve (dict, url)."""
    platform = clean_domain(platform)
    urls = [custom_url.strip()] if custom_url.strip() else []
    if platform in SELLERS_URL_OVERRIDES:
        urls.append(SELLERS_URL_OVERRIDES[platform])
    urls += [f"https://{platform}/sellers.json", f"https://www.{platform}/sellers.json"]
    last = None
    for url in urls:
        try:
            text, final = _http_get(url, timeout)
            return json.loads(text), final
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"No se pudo obtener el sellers.json de {platform}: {last}")


def verify_against_sellers(records, sellers_data):
    """Cruza los registros de UNA plataforma con su sellers.json."""
    sellers = sellers_data.get("sellers", []) if isinstance(sellers_data, dict) else []
    index = {}
    for s in sellers:
        sid = str(s.get("seller_id", "")).strip().lower()
        if sid:
            index[sid] = s
    ok_types = {"DIRECT": {"PUBLISHER", "BOTH"}, "RESELLER": {"INTERMEDIARY", "BOTH"}}

    rows = []
    for r in records:
        s = index.get(r.account_id.lower())
        if s is None:
            rows.append({"line": r.line_no, "id": r.account_id, "rel": r.relationship,
                         "type": "", "name": "", "domain": "", "status": "No encontrado",
                         "note": "El ID no figura en el sellers.json de la plataforma"})
            continue
        stype = str(s.get("seller_type", "")).upper()
        conf = s.get("is_confidential") in (1, "1", True)
        name = "(confidencial)" if conf else str(s.get("name", ""))
        domain = "" if conf else str(s.get("domain", ""))
        notes = []
        status = "OK"
        if stype and stype not in ok_types[r.relationship]:
            status = "Revisar"
            notes.append(f"Declarado {r.relationship} pero seller_type={stype}")
        elif conf:
            status = "Confidencial"
            notes.append("El seller es confidencial")
        if s.get("is_passthrough") in (1, "1", True):
            notes.append("is_passthrough=1")
        rows.append({"line": r.line_no, "id": r.account_id, "rel": r.relationship,
                     "type": stype, "name": name, "domain": domain,
                     "status": status, "note": "; ".join(notes)})
    return rows


def summary_text(a: Analysis, source: str = "") -> str:
    n = len(a.records)
    pd_ = (a.direct / n * 100) if n else 0
    pr_ = (a.reseller / n * 100) if n else 0
    out = []
    if source:
        out.append(f"Fuente: {source}")
    out += [
        f"Líneas totales en el archivo : {a.total_lines}",
        f"Registros válidos            : {n}",
        f"  DIRECT                     : {a.direct} ({pd_:.1f}%)",
        f"  RESELLER                   : {a.reseller} ({pr_:.1f}%)",
        f"Plataformas distintas        : {len(a.by_platform())}",
        f"Duplicados exactos           : {len(a.duplicates)}",
        f"Conflictos DIRECT/RESELLER   : {len(a.conflicts)}",
        f"Líneas inválidas             : {len(a.errors)}",
    ]
    return "\n".join(out)


def full_report(a: Analysis, source: str = "") -> str:
    out = [summary_text(a, source), "", "VARIABLES"]
    out += [f"  {k} = {v}" for _, k, v in a.variables] or ["  (ninguna)"]
    out += ["", "OBSERVACIONES"] + [f"  - {o}" for o in a.observations()]
    out += ["", "PLATAFORMAS (total / DIRECT / RESELLER / IDs únicos)"]
    for r in a.by_platform():
        out.append(f"  {r['platform']:<30} {r['total']:>4} / {r['direct']:>4} / {r['reseller']:>4} / {r['unique_ids']:>4}")
    if a.duplicates:
        out += ["", "DUPLICADOS"]
        out += [f"  {d}, {i}, {rel} -> líneas {l}" for (d, i, rel), l in a.duplicates]
    if a.conflicts:
        out += ["", "CONFLICTOS DIRECT/RESELLER"]
        out += [f"  {d}, {i} -> líneas {l}" for d, i, l in a.conflicts]
    if a.errors:
        out += ["", "LÍNEAS INVÁLIDAS"]
        out += [f"  L{ln}: {why} -> {raw.strip()[:80]}" for ln, why, raw in a.errors]
    return "\n".join(out) + "\n"


def export_platforms_csv(a, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["platform", "total", "direct", "reseller", "unique_ids"])
        for r in a.by_platform():
            w.writerow([r["platform"], r["total"], r["direct"], r["reseller"], r["unique_ids"]])


def export_records_csv(a, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["line", "platform", "account_id", "relationship", "cert_id"])
        for r in a.records:
            w.writerow([r.line_no, r.domain, r.account_id, r.relationship, r.cert_id])


# =============================================================================
# INTERFAZ TKINTER
# =============================================================================
def make_sortable(tree):
    """Permite ordenar un Treeview haciendo clic en los encabezados."""
    def sort_by(col, reverse):
        items = [(tree.set(k, col), k) for k in tree.get_children("")]

        def keyf(t):
            try:
                return (0, float(t[0]), "")
            except ValueError:
                return (1, 0.0, t[0].lower())
        items.sort(key=keyf, reverse=reverse)
        for i, (_, k) in enumerate(items):
            tree.move(k, "", i)
        tree.heading(col, command=lambda: sort_by(col, not reverse))

    for c in tree["columns"]:
        tree.heading(c, command=lambda c=c: sort_by(c, False))


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("ads.txt Analyzer")
    root.geometry("1020x700")
    root.minsize(860, 560)

    S = {"a": None, "source": "", "site": "", "verify_rows": []}

    # ---------- barra superior ----------
    top = ttk.Frame(root, padding=(10, 10, 10, 4))
    top.pack(fill="x")
    open_btn = ttk.Button(top, text="Ingresar archivo ads.txt…", padding=(16, 6))
    open_btn.pack(side="left")
    file_var = tk.StringVar(value="Ningún archivo cargado")
    ttk.Label(top, textvariable=file_var, foreground="#555").pack(side="left", padx=14)

    # ---------- barra de estado ----------
    bottom = ttk.Frame(root, padding=(10, 2, 10, 8))
    bottom.pack(side="bottom", fill="x")
    status_var = tk.StringVar(value="Listo. Ingresá un dominio o abrí un archivo.")
    ttk.Label(bottom, textvariable=status_var, anchor="w").pack(side="left", fill="x", expand=True)
    progress = ttk.Progressbar(bottom, mode="indeterminate", length=120)

    def busy(on, msg=None):
        if msg:
            status_var.set(msg)
        if on:
            progress.pack(side="right")
            progress.start(12)
        else:
            progress.stop()
            progress.pack_forget()

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=10, pady=(0, 4))

    # =================== TAB RESUMEN ===================
    tab_sum = ttk.Frame(nb, padding=8)
    nb.add(tab_sum, text="Resumen")

    cards = ttk.Frame(tab_sum)
    cards.pack(fill="x")
    card_vars = {}

    def make_card(key, title, col):
        f = ttk.LabelFrame(cards, text=title, padding=(14, 4))
        f.grid(row=0, column=col, padx=4, sticky="nsew")
        cards.columnconfigure(col, weight=1)
        v = tk.StringVar(value="–")
        ttk.Label(f, textvariable=v, font=("TkDefaultFont", 18, "bold")).pack()
        card_vars[key] = v

    for i, (k, t) in enumerate([("valid", "Registros"), ("direct", "DIRECT"), ("reseller", "RESELLER"),
                                ("plat", "Plataformas"), ("dup", "Duplicados"),
                                ("conf", "Conflictos"), ("err", "Inválidas")]):
        make_card(k, t, i)

    canvas = tk.Canvas(tab_sum, height=290, bg="white", highlightthickness=1, highlightbackground="#bbb")
    canvas.pack(fill="x", pady=8)

    obs_frame = ttk.LabelFrame(tab_sum, text="Observaciones y variables", padding=4)
    obs_frame.pack(fill="both", expand=True)
    obs_box = tk.Text(obs_frame, height=8, wrap="word", font=("TkDefaultFont", 10))
    obs_sb = ttk.Scrollbar(obs_frame, orient="vertical", command=obs_box.yview)
    obs_box.configure(yscrollcommand=obs_sb.set)
    obs_box.pack(side="left", fill="both", expand=True)
    obs_sb.pack(side="right", fill="y")

    def draw_charts(_evt=None):
        canvas.delete("all")
        a = S["a"]
        w = max(canvas.winfo_width(), 600)
        if not a or not a.records:
            canvas.create_text(w // 2, 120, text="Apretá \"Ingresar archivo\" para empezar", fill="#888", font=("TkDefaultFont", 12))
            return
        n = len(a.records)
        left, right = 190, 70
        bar_w = w - left - right

        # barra global DIRECT vs RESELLER
        canvas.create_text(10, 22, anchor="w", text="Total DIRECT vs RESELLER", font=("TkDefaultFont", 10, "bold"), fill="#222")
        dw = bar_w * a.direct / n
        canvas.create_rectangle(left, 10, left + dw, 34, fill=C_DIRECT, outline="")
        canvas.create_rectangle(left + dw, 10, left + bar_w, 34, fill=C_RESELLER, outline="")
        if dw > 60:
            canvas.create_text(left + dw / 2, 22, text=f"DIRECT {a.direct}", fill="white")
        if bar_w - dw > 80:
            canvas.create_text(left + dw + (bar_w - dw) / 2, 22, text=f"RESELLER {a.reseller}", fill="white")

        # top plataformas (barras apiladas)
        canvas.create_text(10, 58, anchor="w", text="Top 8 plataformas", font=("TkDefaultFont", 10, "bold"), fill="#222")
        rows = a.by_platform()[:8]
        mx = max(r["total"] for r in rows)
        y = 76
        for r in rows:
            canvas.create_text(left - 8, y + 11, anchor="e", text=r["platform"][:28], fill="#222")
            dpx = bar_w * r["direct"] / mx
            rpx = bar_w * r["reseller"] / mx
            canvas.create_rectangle(left, y, left + dpx, y + 22, fill=C_DIRECT, outline="")
            canvas.create_rectangle(left + dpx, y, left + dpx + rpx, y + 22, fill=C_RESELLER, outline="")
            canvas.create_text(left + dpx + rpx + 6, y + 11, anchor="w", text=str(r["total"]), fill="#222")
            y += 26
        # leyenda
        canvas.create_rectangle(w - 190, 58, w - 178, 70, fill=C_DIRECT, outline="")
        canvas.create_text(w - 174, 64, anchor="w", text="DIRECT", fill="#222")
        canvas.create_rectangle(w - 110, 58, w - 98, 70, fill=C_RESELLER, outline="")
        canvas.create_text(w - 94, 64, anchor="w", text="RESELLER", fill="#222")

    canvas.bind("<Configure>", draw_charts)

    # =================== TAB PLATAFORMAS ===================
    tab_plat = ttk.Frame(nb, padding=6)
    nb.add(tab_plat, text="Plataformas")
    bar = ttk.Frame(tab_plat)
    bar.pack(fill="x", pady=(0, 4))
    ttk.Label(bar, text="Filtrar:").pack(side="left")
    plat_filter = tk.StringVar()
    ttk.Entry(bar, textvariable=plat_filter, width=26).pack(side="left", padx=6)
    verify_sel_btn = ttk.Button(bar, text="Verificar sellers.json de la seleccionada →")
    verify_sel_btn.pack(side="right")
    pf = ttk.Frame(tab_plat)
    pf.pack(fill="both", expand=True)

    def make_tree(parent, cols, widths, height=None):
        t = ttk.Treeview(parent, columns=cols, show="headings", selectmode="browse")
        for c, wd in zip(cols, widths):
            t.heading(c, text=c)
            t.column(c, width=wd, anchor="w")
        sb = ttk.Scrollbar(parent, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        make_sortable(t)
        return t

    plat_tree = make_tree(pf, ("plataforma", "total", "DIRECT", "RESELLER", "IDs únicos"), (300, 80, 80, 90, 90))

    # =================== TAB REGISTROS ===================
    tab_rec = ttk.Frame(nb, padding=6)
    nb.add(tab_rec, text="Registros")
    bar2 = ttk.Frame(tab_rec)
    bar2.pack(fill="x", pady=(0, 4))
    ttk.Label(bar2, text="Buscar:").pack(side="left")
    rec_filter = tk.StringVar()
    ttk.Entry(bar2, textvariable=rec_filter, width=26).pack(side="left", padx=6)
    ttk.Label(bar2, text="Relación:").pack(side="left", padx=(10, 0))
    rel_filter = tk.StringVar(value="Todas")
    ttk.Combobox(bar2, textvariable=rel_filter, values=["Todas", "DIRECT", "RESELLER"],
                 state="readonly", width=10).pack(side="left", padx=6)
    rec_count = tk.StringVar()
    ttk.Label(bar2, textvariable=rec_count).pack(side="right")
    rf = ttk.Frame(tab_rec)
    rf.pack(fill="both", expand=True)
    rec_tree = make_tree(rf, ("línea", "plataforma", "ID", "relación", "TAG-ID"), (60, 260, 220, 100, 170))

    # =================== TAB PROBLEMAS ===================
    tab_iss = ttk.Frame(nb, padding=6)
    nb.add(tab_iss, text="Duplicados / Errores")
    iss_tree = make_tree(tab_iss, ("tipo", "detalle", "líneas"), (120, 560, 200))

    # =================== TAB SELLERS.JSON ===================
    tab_sel = ttk.Frame(nb, padding=6)
    nb.add(tab_sel, text="Verificación sellers.json")
    sbar = ttk.Frame(tab_sel)
    sbar.pack(fill="x", pady=(0, 4))
    ttk.Label(sbar, text="Plataforma:").pack(side="left")
    sel_plat = tk.StringVar()
    plat_combo = ttk.Combobox(sbar, textvariable=sel_plat, width=28)
    plat_combo.pack(side="left", padx=6)
    ttk.Label(sbar, text="URL propia (opcional):").pack(side="left", padx=(8, 0))
    sel_url = tk.StringVar()
    ttk.Entry(sbar, textvariable=sel_url, width=34).pack(side="left", padx=6)
    verify_btn = ttk.Button(sbar, text="Verificar")
    verify_btn.pack(side="left")
    sel_info = tk.StringVar(value="Elegí una plataforma y apretá Verificar. Algunos sellers.json pesan decenas de MB.")
    ttk.Label(tab_sel, textvariable=sel_info, wraplength=950, justify="left").pack(fill="x", pady=4)
    sf = ttk.Frame(tab_sel)
    sf.pack(fill="both", expand=True)
    sel_tree = make_tree(sf, ("línea", "ID", "relación", "seller_type", "nombre", "dominio", "estado", "nota"),
                         (50, 150, 80, 100, 170, 140, 100, 260))
    sel_tree.tag_configure("bad", foreground="#b00020")
    sel_tree.tag_configure("warn", foreground="#b26a00")
    sel_tree.tag_configure("good", foreground="#1b7a3a")

    # ---------- renderizado ----------
    def fill_plat():
        plat_tree.delete(*plat_tree.get_children())
        a = S["a"]
        if not a:
            return
        q = plat_filter.get().strip().lower()
        for r in a.by_platform():
            if q in r["platform"]:
                plat_tree.insert("", "end", values=(r["platform"], r["total"], r["direct"],
                                                    r["reseller"], r["unique_ids"]))

    def fill_rec():
        rec_tree.delete(*rec_tree.get_children())
        a = S["a"]
        if not a:
            return
        q = rec_filter.get().strip().lower()
        rel = rel_filter.get()
        shown = 0
        for r in a.records:
            if rel != "Todas" and r.relationship != rel:
                continue
            if q and q not in f"{r.domain} {r.account_id} {r.cert_id}".lower():
                continue
            rec_tree.insert("", "end", values=(r.line_no, r.domain, r.account_id, r.relationship, r.cert_id))
            shown += 1
        rec_count.set(f"{shown} de {len(a.records)} registros")

    plat_filter.trace_add("write", lambda *_: fill_plat())
    rec_filter.trace_add("write", lambda *_: fill_rec())
    rel_filter.trace_add("write", lambda *_: fill_rec())

    def render(a: Analysis):
        S["a"] = a
        n = len(a.records)
        card_vars["valid"].set(str(n))
        card_vars["direct"].set(f"{a.direct}  ({a.direct / n:.0%})" if n else "0")
        card_vars["reseller"].set(f"{a.reseller}  ({a.reseller / n:.0%})" if n else "0")
        card_vars["plat"].set(str(len(a.by_platform())))
        card_vars["dup"].set(str(len(a.duplicates)))
        card_vars["conf"].set(str(len(a.conflicts)))
        card_vars["err"].set(str(len(a.errors)))

        obs_box.config(state="normal")
        obs_box.delete("1.0", "end")
        obs_box.insert("end", "Observaciones\n", "h")
        for o in a.observations():
            obs_box.insert("end", f"  • {o}\n")
        obs_box.insert("end", "\nVariables\n", "h")
        if a.variables:
            for _, k, v in a.variables:
                obs_box.insert("end", f"  {k} = {v}\n")
        else:
            obs_box.insert("end", "  (ninguna)\n")
        obs_box.tag_configure("h", font=("TkDefaultFont", 10, "bold"))
        obs_box.config(state="disabled")

        draw_charts()
        fill_plat()
        fill_rec()

        iss_tree.delete(*iss_tree.get_children())
        for (d, acc, rel), lns in a.duplicates:
            iss_tree.insert("", "end", values=("Duplicado", f"{d}, {acc}, {rel}", ", ".join(map(str, lns))))
        for d, acc, lns in a.conflicts:
            iss_tree.insert("", "end", values=("Conflicto", f"{d}, {acc} (DIRECT y RESELLER)", ", ".join(map(str, lns))))
        for ln, why, raw in a.errors:
            iss_tree.insert("", "end", values=("Inválida", f"{why}: {raw.strip()[:90]}", ln))

        plat_combo["values"] = [r["platform"] for r in a.by_platform()]
        sel_tree.delete(*sel_tree.get_children())
        S["verify_rows"] = []

    def analyze_text(text, source, site=""):
        S["source"], S["site"] = source, site
        file_var.set("Archivo: " + source)
        render(parse_ads_txt(text))
        a = S["a"]
        status_var.set(f"Analizado: {source}  —  {len(a.records)} registros válidos")

    # ---------- acciones ----------
    def on_fetch(d, app):
        busy(True, f"Descargando {'app-ads.txt' if app else 'ads.txt'} de {d}…")

        def worker():
            try:
                text, url = fetch_ads_txt(d, app=app)
                root.after(0, lambda: analyze_text(text, url, clean_domain(d)))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                root.after(0, lambda: (status_var.set("Error al descargar."),
                                       messagebox.showerror("Error", msg)))
            finally:
                root.after(0, lambda: busy(False))
        threading.Thread(target=worker, daemon=True).start()

    def on_fetch_dialog():
        from tkinter import simpledialog
        d = simpledialog.askstring("Descargar de un dominio", "Dominio (ej: nytimes.com):", parent=root)
        if not d or not d.strip():
            return
        app = messagebox.askyesno("Tipo de archivo",
                                  "¿Es un app-ads.txt?\n\nSí = app-ads.txt   |   No = ads.txt", parent=root)
        on_fetch(d.strip(), app)

    def on_open():
        path = filedialog.askopenfilename(
            title="Elegí tu archivo ads.txt o app-ads.txt",
            filetypes=[("ads.txt / app-ads.txt", "*.txt"), ("Todos los archivos", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            messagebox.showerror("Error", f"No se pudo leer el archivo:\n{e}")
            return
        if not text.strip():
            messagebox.showwarning("Archivo vacío", "El archivo no tiene contenido.")
            return
        analyze_text(text, path)

    def on_verify(platform=None):
        a = S["a"]
        if not a:
            messagebox.showinfo("Sin datos", "Primero analizá un ads.txt.")
            return
        platform = clean_domain(platform or sel_plat.get())
        recs = [r for r in a.records if r.domain == platform]
        if not recs:
            messagebox.showwarning("Plataforma", f"'{platform}' no figura en el archivo analizado.")
            return
        verify_btn.config(state="disabled")
        busy(True, f"Descargando sellers.json de {platform}… (puede tardar)")
        sel_info.set(f"Verificando {len(recs)} línea(s) de {platform}…")
        custom = sel_url.get()

        def worker():
            try:
                data, url = fetch_sellers_json(platform, custom)
                rows = verify_against_sellers(recs, data)
                meta = (f"{platform}: {len(data.get('sellers', []))} sellers en {url}"
                        f"  |  contacto: {data.get('contact_email', 'n/d')}")
                root.after(0, lambda: show_verify(rows, meta))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                root.after(0, lambda: (sel_info.set("Error: " + msg), status_var.set("Error en sellers.json.")))
            finally:
                root.after(0, lambda: (busy(False), verify_btn.config(state="normal")))
        threading.Thread(target=worker, daemon=True).start()

    def show_verify(rows, meta):
        S["verify_rows"] = rows
        sel_tree.delete(*sel_tree.get_children())
        counts = defaultdict(int)
        for r in rows:
            counts[r["status"]] += 1
            tag = {"OK": "good", "No encontrado": "bad", "Revisar": "warn"}.get(r["status"], "warn")
            sel_tree.insert("", "end", tags=(tag,), values=(r["line"], r["id"], r["rel"], r["type"],
                                                            r["name"], r["domain"], r["status"], r["note"]))
        res = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        sel_info.set(f"{meta}\nResultado → {res}")
        status_var.set("Verificación terminada.")

    def on_verify_selected():
        sel = plat_tree.selection()
        if not sel:
            messagebox.showinfo("Seleccioná", "Elegí una plataforma de la lista.")
            return
        platform = plat_tree.item(sel[0], "values")[0]
        sel_plat.set(platform)
        nb.select(tab_sel)
        on_verify(platform)

    open_btn.config(command=on_open)
    verify_btn.config(command=on_verify)
    verify_sel_btn.config(command=on_verify_selected)
    plat_tree.bind("<Double-1>", lambda _e: on_verify_selected())

    # ---------- exportaciones ----------
    def need_data():
        if not S["a"]:
            messagebox.showinfo("Sin datos", "Primero analizá un archivo.")
            return False
        return True

    def ex_plat():
        if need_data():
            p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="plataformas.csv",
                                             filetypes=[("CSV", "*.csv")])
            if p:
                export_platforms_csv(S["a"], p)
                status_var.set(f"Exportado: {p}")

    def ex_rec():
        if need_data():
            p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="registros.csv",
                                             filetypes=[("CSV", "*.csv")])
            if p:
                export_records_csv(S["a"], p)
                status_var.set(f"Exportado: {p}")

    def ex_report():
        if need_data():
            p = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="reporte_ads_txt.txt",
                                             filetypes=[("Texto", "*.txt")])
            if p:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(full_report(S["a"], S["source"]))
                status_var.set(f"Reporte guardado: {p}")

    def ex_verify():
        if not S["verify_rows"]:
            messagebox.showinfo("Sin datos", "Primero hacé una verificación de sellers.json.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="verificacion_sellers.csv",
                                         filetypes=[("CSV", "*.csv")])
        if p:
            with open(p, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(S["verify_rows"][0].keys()))
                w.writeheader()
                w.writerows(S["verify_rows"])
            status_var.set(f"Exportado: {p}")

    menubar = tk.Menu(root)
    m_file = tk.Menu(menubar, tearoff=0)
    m_file.add_command(label="Ingresar archivo…", command=on_open)
    m_file.add_command(label="Descargar de un dominio…", command=on_fetch_dialog)
    m_file.add_separator()
    m_file.add_command(label="Exportar plataformas (CSV)", command=ex_plat)
    m_file.add_command(label="Exportar registros (CSV)", command=ex_rec)
    m_file.add_command(label="Exportar verificación sellers.json (CSV)", command=ex_verify)
    m_file.add_command(label="Guardar reporte completo (TXT)", command=ex_report)
    m_file.add_separator()
    m_file.add_command(label="Salir", command=root.destroy)
    menubar.add_cascade(label="Archivo", menu=m_file)
    root.config(menu=menubar)

    draw_charts()
    root.after(300, on_open)  # al abrir el programa, pide el archivo enseguida
    root.mainloop()


# =============================================================================
# CLI
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Analizador de ads.txt / app-ads.txt")
    ap.add_argument("--domain", help="dominio a descargar, ej: nytimes.com")
    ap.add_argument("--app", action="store_true", help="usar app-ads.txt")
    ap.add_argument("--file", help="analizar un archivo local")
    ap.add_argument("--csv", help="exportar agrupación por plataforma a CSV")
    ap.add_argument("--report", help="guardar reporte completo en TXT")
    ap.add_argument("--verify", help="plataforma a verificar contra su sellers.json, ej: google.com")
    args = ap.parse_args()

    if not args.domain and not args.file:
        run_gui()
        return 0

    if args.file:
        with open(args.file, encoding="utf-8", errors="replace") as f:
            text, source = f.read(), args.file
    else:
        text, source = fetch_ads_txt(args.domain, app=args.app)

    a = parse_ads_txt(text)
    report = full_report(a, source)
    print(report)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report)
    if args.csv:
        export_platforms_csv(a, args.csv)
        print(f"CSV exportado a {args.csv}")
    if args.verify:
        plat = clean_domain(args.verify)
        recs = [r for r in a.records if r.domain == plat]
        data, url = fetch_sellers_json(plat)
        print(f"\nVerificación contra {url}")
        for r in verify_against_sellers(recs, data):
            print(f"  L{r['line']:<4} {r['id']:<22} {r['rel']:<9} {r['status']:<14} {r['name']} {r['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
