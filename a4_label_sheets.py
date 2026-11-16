#!/usr/bin/env python3
"""Etykiety A4 / A4 Label Sheets — print shipping labels (e.g. 4x6") onto sticker sheets with 4 labels per A4.

Load PDF labels -> the app places them in the four A4 quarters (2x2), shows a live preview,
lets you adjust scale / offset / rotation and prints to a CUPS printer.
(PL: wczytujesz PDF-y, program układa je w ćwiartkach A4, pokazuje podgląd i drukuje.)

Requires: python3-gi + GTK 3, pycairo, Pillow, poppler-utils (pdftoppm, pdfinfo), CUPS client (lp, lpstat).
The user interface is currently in Polish.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import cairo
from PIL import Image, ImageChops, ImageDraw, ImageOps

# ----------------------------------------------------------------------------
# Stałe i geometria (wszystko w milimetrach, początek układu = lewy górny róg A4)
# ----------------------------------------------------------------------------
A4_W, A4_H = 210.0, 297.0
QW, QH = A4_W / 2, A4_H / 2          # ćwiartka = 105 x 148,5 mm
PREVIEW_DPI = 200                    # rozdzielczość podglądu / pomiaru zawartości
PRINT_DPI = 600                      # typowa natywna rozdzielczość drukarek laserowych
PAD_MM = 1.0                         # luz między etykietą a krawędzią naklejki
SLOT_NAMES = ["lewa góra", "prawa góra", "lewy dół", "prawy dół"]
DEFAULT_PRINTER = None                # ustalana przy starcie: domyślna drukarka systemu (lpstat -d)
DEFAULT_DIR = os.path.expanduser("~")
APP_NAME = "Etykiety A4"

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "etykiety-a4")
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")


def quad_rect(i):
    col, row = i % 2, i // 2
    return (col * QW, row * QH, (col + 1) * QW, (row + 1) * QH)


def allowed_rect(i, margin):
    """Obszar, w którym wolno umieścić zawartość etykiety w ćwiartce i:
    przy krawędziach kartki obowiązuje margines drukarki, między ćwiartkami mały luz."""
    x0, y0, x1, y1 = quad_rect(i)
    left = margin if x0 <= 0 else PAD_MM
    right = margin if x1 >= A4_W else PAD_MM
    top = margin if y0 <= 0 else PAD_MM
    bottom = margin if y1 >= A4_H else PAD_MM
    return (x0 + left, y0 + top, x1 - right, y1 - bottom)


# ----------------------------------------------------------------------------
# Renderowanie PDF (poppler-utils)
# ----------------------------------------------------------------------------
def pdf_page_count(path):
    out = subprocess.run(["pdfinfo", path], capture_output=True, text=True)
    m = re.search(r"^Pages:\s+(\d+)", out.stdout, re.M)
    if out.returncode != 0 or not m:
        raise ValueError(f"Nie udało się odczytać PDF: {os.path.basename(path)}")
    return int(m.group(1))


def render_pdf_page(path, page, dpi):
    """Zwraca stronę PDF jako obraz 'L' (skala szarości) w zadanej rozdzielczości."""
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "p")
        subprocess.run(
            ["pdftoppm", "-r", f"{dpi:.4f}", "-gray", "-png", "-f", str(page), "-l", str(page),
             "-singlefile", path, base],
            check=True, capture_output=True)
        img = Image.open(base + ".png")
        img.load()
        return img.convert("L")


def to_surface(img):
    rgba = img.convert("RGBA")
    w, h = rgba.size
    data = bytearray(rgba.tobytes("raw", "BGRA"))
    surf = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, w, h, w * 4)
    return surf, data  # data trzymamy, żeby nie zniknęło z pamięci


class Item:
    """Jedna strona PDF = jedna etykieta."""

    def __init__(self, path, page, npages):
        self.path, self.page = path, page
        self.name = os.path.basename(path) + (f" (str. {page})" if npages > 1 else "")
        self._base = render_pdf_page(path, page, PREVIEW_DPI)
        self._views = {}

    def view(self, rot):
        """Obraz obrócony o rot (0/90/180/270 w prawo) + rozmiar i bbox zawartości w mm."""
        v = self._views.get(rot)
        if v is None:
            img = self._base.rotate(-rot, expand=True) if rot else self._base
            mm = 25.4 / PREVIEW_DPI
            mask = ImageOps.invert(img).point(lambda p: 255 if p > 60 else 0)
            bb = mask.getbbox() or (0, 0, img.width, img.height)
            v = {
                "img": img,
                "size_mm": (img.width * mm, img.height * mm),
                "bbox_mm": tuple(c * mm for c in bb),
                "surface": None,
            }
            self._views[rot] = v
        return v

    def surface(self, rot):
        v = self.view(rot)
        if v["surface"] is None:
            v["surface"] = to_surface(v["img"])
        return v["surface"][0]


class Slot:
    def __init__(self):
        self.item = None
        self.used = False     # naklejka już zużyta
        self.rot = 0
        self.auto = True      # automatyczne dopasowanie skali
        self.pct = 100.0
        self.dx = 0.0
        self.dy = 0.0


def compute_layout(i, slot, margin):
    """Wylicza skalę, położenie i ostrzeżenia dla ćwiartki i. None, gdy pusta/zużyta."""
    if slot.item is None or slot.used:
        return None
    view = slot.item.view(slot.rot)
    ax0, ay0, ax1, ay1 = allowed_rect(i, margin)
    bx0, by0, bx1, by1 = view["bbox_mm"]
    cw, ch = bx1 - bx0, by1 - by0
    if slot.auto:
        s = min(1.0, (ax1 - ax0) / cw, (ay1 - ay0) / ch)
    else:
        s = slot.pct / 100.0
    tx = (ax0 + ax1) / 2 + slot.dx
    ty = (ay0 + ay1) / 2 + slot.dy
    ccx, ccy = (bx0 + bx1) / 2, (by0 + by1) / 2
    origin = (tx - s * ccx, ty - s * ccy)                      # lewy górny róg obrazu strony
    content = (tx - s * cw / 2, ty - s * ch / 2, tx + s * cw / 2, ty + s * ch / 2)
    warns, eps = [], 0.05
    if (content[0] < margin - eps or content[1] < margin - eps or
            content[2] > A4_W - margin + eps or content[3] > A4_H - margin + eps):
        warns.append(f"wychodzi poza obszar druku drukarki (margines {margin:g} mm)")
    q = quad_rect(i)
    if content[0] < q[0] - eps or content[1] < q[1] - eps or content[2] > q[2] + eps or content[3] > q[3] + eps:
        warns.append("wystaje poza naklejkę")
    return {"scale": s, "origin": origin, "content": content, "warnings": warns}


# ----------------------------------------------------------------------------
# Rysowanie podglądu (czyste cairo — działa też bez okna, do testów)
# ----------------------------------------------------------------------------
def draw_sheet(cr, w, h, slots, margin, selected):
    pad = 14
    k = min((w - 2 * pad) / A4_W, (h - 2 * pad) / A4_H)
    ox, oy = (w - A4_W * k) / 2, (h - A4_H * k) / 2

    cr.save()
    cr.translate(ox, oy)
    cr.scale(k, k)
    lw = 1.0 / k   # 1 piksel ekranu w mm

    # cień + biała kartka
    cr.set_source_rgba(0, 0, 0, 0.18)
    cr.rectangle(1.2, 1.2, A4_W, A4_H)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.rectangle(0, 0, A4_W, A4_H)
    cr.fill()

    # obraz etykiet
    for i, slot in enumerate(slots):
        x0, y0, x1, y1 = quad_rect(i)
        lay = compute_layout(i, slot, margin)
        if slot.used:
            cr.set_source_rgb(0.9, 0.9, 0.9)
            cr.rectangle(x0, y0, QW, QH)
            cr.fill()
            cr.set_source_rgb(0.65, 0.65, 0.65)
            cr.set_line_width(lw * 1.5)
            cr.move_to(x0 + 8, y0 + 8); cr.line_to(x1 - 8, y1 - 8)
            cr.move_to(x1 - 8, y0 + 8); cr.line_to(x0 + 8, y1 - 8)
            cr.stroke()
            _center_text(cr, "zużyta", (x0 + x1) / 2, (y0 + y1) / 2, 7, (0.35, 0.35, 0.35))
        elif lay is None:
            _center_text(cr, "pusta", (x0 + x1) / 2, (y0 + y1) / 2, 7, (0.7, 0.7, 0.7))
            _center_text(cr, "przeciągnij tu PDF", (x0 + x1) / 2, (y0 + y1) / 2 + 9, 4, (0.7, 0.7, 0.7))
        else:
            surf = slot.item.surface(slot.rot)
            cr.save()
            cr.rectangle(0, 0, A4_W, A4_H)
            cr.clip()
            cr.translate(*lay["origin"])
            f = lay["scale"] * 25.4 / PREVIEW_DPI
            cr.scale(f, f)
            cr.set_source_surface(surf, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            cr.restore()
            if lay["warnings"]:
                c = lay["content"]
                cr.set_source_rgb(0.85, 0.1, 0.1)
                cr.set_line_width(lw * 1.5)
                cr.set_dash([2, 1.5])
                cr.rectangle(c[0], c[1], c[2] - c[0], c[3] - c[1])
                cr.stroke()
                cr.set_dash([])

    # strefa niedrukowalna (czerwonawy pas) + linia
    cr.set_source_rgba(0.9, 0.2, 0.2, 0.10)
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.rectangle(0, 0, A4_W, A4_H)
    cr.rectangle(margin, margin, A4_W - 2 * margin, A4_H - 2 * margin)
    cr.fill()
    cr.set_source_rgba(0.9, 0.2, 0.2, 0.5)
    cr.set_line_width(lw)
    cr.set_dash([1.5, 1.5])
    cr.rectangle(margin, margin, A4_W - 2 * margin, A4_H - 2 * margin)
    cr.stroke()

    # granice naklejek (ćwiartek)
    cr.set_source_rgba(0.3, 0.3, 0.3, 0.6)
    cr.set_line_width(lw)
    cr.set_dash([3, 2])
    cr.move_to(QW, 0); cr.line_to(QW, A4_H)
    cr.move_to(0, QH); cr.line_to(A4_W, QH)
    cr.stroke()
    cr.set_dash([])

    # numerki + zaznaczenie
    for i in range(4):
        x0, y0, x1, y1 = quad_rect(i)
        _center_text(cr, str(i + 1), x0 + 6, y0 + 8, 6, (0.45, 0.55, 0.75))
        if i == selected:
            cr.set_source_rgb(0.15, 0.45, 0.9)
            cr.set_line_width(lw * 2.5)
            cr.rectangle(x0 + 0.8, y0 + 0.8, QW - 1.6, QH - 1.6)
            cr.stroke()
    cr.restore()
    return k, ox, oy


def _center_text(cr, text, cx, cy, size, rgb):
    cr.save()
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    e = cr.text_extents(text)
    cr.set_source_rgb(*rgb)
    cr.move_to(cx - e.width / 2 - e.x_bearing, cy - e.height / 2 - e.y_bearing)
    cr.show_text(text)
    cr.restore()


# ----------------------------------------------------------------------------
# Składanie arkusza do druku (600 dpi) i wysyłka do drukarki
# ----------------------------------------------------------------------------
def _blit_darker(canvas, img, ox, oy):
    """Wkleja img na canvas w (ox,oy) biorąc ciemniejszy piksel (białe tło etykiety nie zakrywa sąsiada)."""
    x0, y0 = max(ox, 0), max(oy, 0)
    x1, y1 = min(ox + img.width, canvas.width), min(oy + img.height, canvas.height)
    if x1 <= x0 or y1 <= y0:
        return
    part = img.crop((x0 - ox, y0 - oy, x1 - ox, y1 - oy))
    region = canvas.crop((x0, y0, x1, y1))
    canvas.paste(ImageChops.darker(region, part), (x0, y0))


def compose_sheet(slots, margin, onebit=True, guides=False):
    px = PRINT_DPI / 25.4
    canvas = Image.new("L", (round(A4_W * px), round(A4_H * px)), 255)
    for i, slot in enumerate(slots):
        lay = compute_layout(i, slot, margin)
        if lay is None:
            continue
        img = render_pdf_page(slot.item.path, slot.item.page, PRINT_DPI * lay["scale"])
        if slot.rot:
            img = img.rotate(-slot.rot, expand=True)
        _blit_darker(canvas, img, round(lay["origin"][0] * px), round(lay["origin"][1] * px))
    if guides:   # ramki naklejek — tylko do próby na zwykłym papierze
        d = ImageDraw.Draw(canvas)
        for i in range(4):
            x0, y0, x1, y1 = (round(v * px) for v in quad_rect(i))
            d.rectangle((x0, y0, x1 - 1, y1 - 1), outline=120, width=3)
    if onebit:   # ostre czarno-białe: kody kreskowe bez rozmycia rastrem sterownika
        canvas = canvas.point(lambda p: 255 if p >= 140 else 0).convert("1")
    return canvas


def save_sheet_pdf(canvas, path):
    canvas.save(path, "PDF", resolution=PRINT_DPI)


def list_printers():
    r = subprocess.run(["lpstat", "-e"], capture_output=True, text=True)
    return [p for p in r.stdout.split() if p] if r.returncode == 0 else []


def default_printer():
    """Nazwa domyślnej drukarki CUPS albo None."""
    r = subprocess.run(["lpstat", "-d"], capture_output=True, text=True)
    m = re.search(r":\s*(\S+)\s*$", r.stdout.strip()) if r.returncode == 0 else None
    return m.group(1) if m else None


def send_to_printer(pdf, printer, copies):
    cmd = ["lp", "-d", printer, "-n", str(copies), "-t", APP_NAME,
           "-o", "media=A4", "-o", "print-scaling=none", "-o", "fit-to-page=false",
           "-o", "StpiShrinkOutput=Crop",     # sterownik Gutenprint: nie skaluj strony
           pdf]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "lp zwrócił błąd")
    return r.stdout.strip()


# ----------------------------------------------------------------------------
# Stan zapisywany między uruchomieniami
# ----------------------------------------------------------------------------
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# GUI (GTK 3)
# ----------------------------------------------------------------------------
def run_gui(argv_files):
    global DEFAULT_PRINTER
    DEFAULT_PRINTER = default_printer()
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
    from urllib.parse import unquote, urlparse

    class Window(Gtk.Window):
        def __init__(self):
            super().__init__(title=APP_NAME)
            self.set_default_size(980, 720)
            self.state = load_state()
            self.slots = [Slot() for _ in range(4)]
            for i in self.state.get("used", []):
                if 0 <= i < 4:
                    self.slots[i].used = True
            self.items = []
            self.sel = next((i for i, s in enumerate(self.slots) if not s.used), 0)
            self._lock = False
            self.k = self.ox = self.oy = 0

            hb = Gtk.HeaderBar(show_close_button=True, title=APP_NAME, subtitle="etykiety na ćwiartkach A4")
            self.set_titlebar(hb)
            b_open = Gtk.Button(label="Otwórz PDF…")
            b_open.connect("clicked", self.on_open)
            hb.pack_start(b_open)
            b_new = Gtk.Button(label="Nowy arkusz")
            b_new.set_tooltip_text("Włożyłem nowy arkusz naklejek — wszystkie ćwiartki znowu wolne")
            b_new.connect("clicked", self.on_new_sheet)
            hb.pack_start(b_new)

            root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            self.add(root)

            self.da = Gtk.DrawingArea()
            self.da.set_size_request(380, 520)
            self.da.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.da.connect("draw", self.on_draw)
            self.da.connect("button-press-event", self.on_click)
            root.pack_start(self.da, True, True, 0)

            side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
            side.set_size_request(330, -1)
            root.pack_start(side, False, False, 0)

            # --- Ćwiartka ---
            f1 = Gtk.Frame(label=" Wybrana ćwiartka ")
            g1 = Gtk.Grid(row_spacing=6, column_spacing=8, margin=8)
            f1.add(g1)
            self.lbl_slot = Gtk.Label(xalign=0)
            g1.attach(self.lbl_slot, 0, 0, 2, 1)
            self.chk_used = Gtk.CheckButton(label="Naklejka zużyta (pomiń)")
            self.chk_used.connect("toggled", self.on_used)
            g1.attach(self.chk_used, 0, 1, 2, 1)
            g1.attach(Gtk.Label(label="Plik:", xalign=0), 0, 2, 1, 1)
            self.cmb_item = Gtk.ComboBoxText(hexpand=True)
            self.cmb_item.connect("changed", self.on_item)
            g1.attach(self.cmb_item, 1, 2, 1, 1)
            self.btn_rot = Gtk.Button(label="Obróć o 90° ↻")
            self.btn_rot.connect("clicked", self.on_rotate)
            g1.attach(self.btn_rot, 0, 3, 2, 1)
            side.pack_start(f1, False, False, 0)

            # --- Skala ---
            f2 = Gtk.Frame(label=" Skala i położenie ")
            g2 = Gtk.Grid(row_spacing=6, column_spacing=8, margin=8)
            f2.add(g2)
            self.chk_auto = Gtk.CheckButton(label="Dopasuj automatycznie")
            self.chk_auto.connect("toggled", self.on_scale_changed)
            g2.attach(self.chk_auto, 0, 0, 3, 1)
            self.sp_pct = self._spin(g2, 1, "Skala:", 20, 200, 1, 0, "% oryginału", True)
            self.sp_dx = self._spin(g2, 2, "Przesuń w prawo:", -30, 30, 0.5, 1, "mm", True)
            self.sp_dy = self._spin(g2, 3, "Przesuń w dół:", -30, 30, 0.5, 1, "mm", True)
            self.lbl_info = Gtk.Label(xalign=0, wrap=True)
            self.lbl_info.get_style_context().add_class("dim-label")
            g2.attach(self.lbl_info, 0, 4, 3, 1)
            b_all = Gtk.Button(label="Zastosuj do wszystkich ćwiartek")
            b_all.connect("clicked", self.on_apply_all)
            g2.attach(b_all, 0, 5, 3, 1)
            side.pack_start(f2, False, False, 0)

            # --- Drukowanie ---
            f3 = Gtk.Frame(label=" Drukowanie ")
            g3 = Gtk.Grid(row_spacing=6, column_spacing=8, margin=8)
            f3.add(g3)
            g3.attach(Gtk.Label(label="Drukarka:", xalign=0), 0, 0, 1, 1)
            self.cmb_printer = Gtk.ComboBoxText(hexpand=True)
            printers = list_printers()
            want = self.state.get("printer", DEFAULT_PRINTER)
            for n, p in enumerate(printers):
                self.cmb_printer.append_text(p)
                if p == want or (want not in printers and p == DEFAULT_PRINTER):
                    self.cmb_printer.set_active(n)
            if self.cmb_printer.get_active() < 0 and printers:
                self.cmb_printer.set_active(0)
            self.cmb_printer.connect("changed", lambda *_: self.persist())
            g3.attach(self.cmb_printer, 1, 0, 2, 1)
            self.sp_margin = self._spin(g3, 1, "Margines druku:", 0, 15, 0.5, 1, "mm")
            self.sp_margin.set_value(self.state.get("margin", 4.0))
            self.sp_margin.connect("value-changed", self.on_margin)
            self.sp_copies = self._spin(g3, 2, "Kopie:", 1, 20, 1, 0, "")
            self.sp_copies.set_value(1)
            self.chk_1bit = Gtk.CheckButton(label="Ostre czarno-białe (kody kreskowe)")
            self.chk_1bit.set_active(self.state.get("onebit", True))
            self.chk_1bit.connect("toggled", lambda *_: self.persist())
            g3.attach(self.chk_1bit, 0, 3, 3, 1)
            self.chk_guides = Gtk.CheckButton(label="Rysuj ramki naklejek (próba na zwykłym papierze)")
            self.chk_guides.get_child().set_line_wrap(True)
            g3.attach(self.chk_guides, 0, 4, 3, 1)
            side.pack_start(f3, False, False, 0)

            bb = Gtk.Box(spacing=8)
            b_save = Gtk.Button(label="Zapisz PDF…")
            b_save.connect("clicked", self.on_save)
            bb.pack_start(b_save, True, True, 0)
            b_print = Gtk.Button(label="Drukuj")
            b_print.get_style_context().add_class("suggested-action")
            b_print.connect("clicked", self.on_print)
            bb.pack_start(b_print, True, True, 0)
            side.pack_start(bb, False, False, 0)

            self.lbl_status = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=False)
            self.lbl_status.set_max_width_chars(40)
            side.pack_start(self.lbl_status, True, True, 0)

            # przeciąganie plików na okno
            self.drag_dest_set(Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY)
            self.connect("drag-data-received", self.on_drop)
            self.connect("delete-event", lambda *_: (self.persist(), False)[1])

            self.sync_panel()
            self.show_all()
            if argv_files:
                GLib.idle_add(lambda: self.load_files(argv_files) and False)

        # ---- helpery UI ----
        def _spin(self, grid, row, label, lo, hi, step, digits, unit, scale=False):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            sp = Gtk.SpinButton.new_with_range(lo, hi, step)
            sp.set_digits(digits)
            sp.set_value(0 if lo < 0 else lo)
            if scale:
                sp.connect("value-changed", self.on_scale_changed)
            grid.attach(sp, 1, row, 1, 1)
            grid.attach(Gtk.Label(label=unit, xalign=0), 2, row, 1, 1)
            return sp

        @property
        def margin(self):
            return self.sp_margin.get_value()

        def persist(self):
            if self._lock:
                return
            self.state.update(
                used=[i for i, s in enumerate(self.slots) if s.used],
                printer=self.cmb_printer.get_active_text() or DEFAULT_PRINTER or "",
                margin=self.margin, onebit=self.chk_1bit.get_active())
            save_state(self.state)

        def status(self, text, kind="info"):
            colors = {"ok": "#2e7d32", "warn": "#c62828", "info": None}
            esc = GLib.markup_escape_text(text)
            c = colors[kind]
            self.lbl_status.set_markup(f'<span foreground="{c}">{esc}</span>' if c else esc)

        def refresh(self):
            self.da.queue_draw()
            self.update_info()

        # ---- panel <-> slot ----
        def sync_panel(self):
            self._lock = True
            s = self.slots[self.sel]
            self.lbl_slot.set_markup(f"<b>{self.sel + 1}</b> — {SLOT_NAMES[self.sel]}")
            self.chk_used.set_active(s.used)
            self.cmb_item.remove_all()
            self.cmb_item.append_text("— pusta —")
            for it in self.items:
                self.cmb_item.append_text(it.name)
            self.cmb_item.set_active(0 if s.item is None else self.items.index(s.item) + 1)
            self.cmb_item.set_sensitive(not s.used)
            self.btn_rot.set_sensitive(s.item is not None)
            self.chk_auto.set_active(s.auto)
            self.sp_pct.set_value(s.pct)
            self.sp_dx.set_value(s.dx)
            self.sp_dy.set_value(s.dy)
            self._lock = False
            self.refresh()

        def update_info(self):
            s = self.slots[self.sel]
            lay = compute_layout(self.sel, s, self.margin)
            self._lock = True
            self.sp_pct.set_sensitive(not s.auto and s.item is not None and not s.used)
            self.sp_dx.set_sensitive(s.item is not None and not s.used)
            self.sp_dy.set_sensitive(s.item is not None and not s.used)
            if lay:
                if s.auto:
                    self.sp_pct.set_value(round(lay["scale"] * 100))
                c = lay["content"]
                self.lbl_info.set_text(f"Zawartość po skalowaniu: {c[2]-c[0]:.0f} × {c[3]-c[1]:.0f} mm "
                                       f"(naklejka {QW:.0f} × {QH:.1f} mm)")
            else:
                self.lbl_info.set_text("")
            self._lock = False
            # ostrzeżenia ze wszystkich ćwiartek
            msgs = []
            for i, sl in enumerate(self.slots):
                l = compute_layout(i, sl, self.margin)
                if l and l["warnings"]:
                    msgs.append(f"Ćwiartka {i + 1}: " + "; ".join(l["warnings"]) + ".")
            if msgs:
                self.status("⚠ " + "\n".join(msgs) + "\nZmniejsz skalę lub przesuń etykietę.", "warn")
            elif any(compute_layout(i, sl, self.margin) for i, sl in enumerate(self.slots)):
                self.status("✔ Wszystko mieści się na naklejkach i w obszarze druku.", "ok")
            else:
                self.status("Otwórz PDF albo przeciągnij pliki na okno.")

        # ---- zdarzenia ----
        def on_draw(self, widget, cr):
            w, h = widget.get_allocated_width(), widget.get_allocated_height()
            self.k, self.ox, self.oy = draw_sheet(cr, w, h, self.slots, self.margin, self.sel)
            return False

        def on_click(self, widget, ev):
            if not self.k:
                return
            mx, my = (ev.x - self.ox) / self.k, (ev.y - self.oy) / self.k
            if 0 <= mx < A4_W and 0 <= my < A4_H:
                self.sel = int(mx // QW) + 2 * int(my // QH)
                self.sync_panel()

        def on_used(self, btn):
            if self._lock:
                return
            s = self.slots[self.sel]
            s.used = btn.get_active()
            if s.used:
                s.item = None
            self.persist()
            self.sync_panel()

        def on_item(self, cmb):
            if self._lock:
                return
            n = cmb.get_active()
            self.slots[self.sel].item = None if n <= 0 else self.items[n - 1]
            self.sync_panel()

        def on_rotate(self, _):
            s = self.slots[self.sel]
            s.rot = (s.rot + 90) % 360
            self.refresh()

        def on_scale_changed(self, *_):
            if self._lock:
                return
            s = self.slots[self.sel]
            s.auto = self.chk_auto.get_active()
            if not s.auto:
                s.pct = self.sp_pct.get_value()
            s.dx, s.dy = self.sp_dx.get_value(), self.sp_dy.get_value()
            self.refresh()

        def on_margin(self, *_):
            self.persist()
            self.refresh()

        def on_apply_all(self, _):
            src = self.slots[self.sel]
            for s in self.slots:
                s.auto, s.pct, s.dx, s.dy = src.auto, src.pct, src.dx, src.dy
            self.refresh()

        def on_new_sheet(self, _):
            for s in self.slots:
                s.used = False
            self.sel = 0
            self.persist()
            self.sync_panel()
            self.status("Nowy arkusz — wszystkie 4 ćwiartki wolne.", "ok")

        # ---- wczytywanie plików ----
        def on_open(self, _):
            dlg = Gtk.FileChooserDialog(title="Wybierz etykiety (PDF)", parent=self,
                                        action=Gtk.FileChooserAction.OPEN)
            dlg.add_buttons("Anuluj", Gtk.ResponseType.CANCEL, "Otwórz", Gtk.ResponseType.OK)
            dlg.set_select_multiple(True)
            flt = Gtk.FileFilter()
            flt.set_name("Pliki PDF")
            flt.add_mime_type("application/pdf")
            dlg.add_filter(flt)
            start = self.state.get("last_dir") or DEFAULT_DIR
            if os.path.isdir(start):
                dlg.set_current_folder(start)
            if dlg.run() == Gtk.ResponseType.OK:
                files = dlg.get_filenames()
                dlg.destroy()
                self.load_files(files)
            else:
                dlg.destroy()

        def on_drop(self, widget, ctx, x, y, data, info, time):
            files = []
            for uri in data.get_uris():
                u = urlparse(uri)
                if u.scheme == "file":
                    files.append(unquote(u.path))
            ctx.finish(True, False, time)
            self.load_files(files)

        def load_files(self, files):
            added, errors = [], []
            for path in files:
                try:
                    n = pdf_page_count(path)
                    for p in range(1, n + 1):
                        added.append(Item(path, p, n))
                except (ValueError, subprocess.CalledProcessError) as e:
                    errors.append(f"{os.path.basename(path)}: {e}")
            if files:
                self.state["last_dir"] = os.path.dirname(files[0])
            self.items.extend(added)
            # auto-przydział do wolnych ćwiartek, zaczynając od wybranej
            order = [(self.sel + j) % 4 for j in range(4)]
            free = [i for i in order if not self.slots[i].used and self.slots[i].item is None]
            for it, i in zip(added, free):
                self.slots[i].item = it
            leftover = max(0, len(added) - len(free))
            self.sync_panel()
            msg = f"Wczytano {len(added)} etykiet(y)."
            if leftover:
                msg += f" {leftover} nie zmieściło się na arkuszu — przypiszesz je po wydruku."
            if errors:
                msg += "\n" + "\n".join(errors)
            self.status(msg, "warn" if errors else "info")
            if not errors:
                GLib.timeout_add(2500, lambda: (self.update_info(), False)[1])

        # ---- zapis / druk ----
        def active_slots(self):
            return [i for i, s in enumerate(self.slots) if compute_layout(i, s, self.margin)]

        def build_sheet(self):
            return compose_sheet(self.slots, self.margin, self.chk_1bit.get_active(), self.chk_guides.get_active())

        def message(self, kind, text, buttons=Gtk.ButtonsType.OK, secondary=None):
            d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=kind, buttons=buttons, text=text)
            if secondary:
                d.format_secondary_text(secondary)
            r = d.run()
            d.destroy()
            return r

        def on_save(self, _):
            if not self.active_slots():
                return self.message(Gtk.MessageType.INFO, "Nie ma czego zapisać", secondary="Wczytaj najpierw plik PDF.")
            dlg = Gtk.FileChooserDialog(title="Zapisz arkusz jako PDF", parent=self, action=Gtk.FileChooserAction.SAVE)
            dlg.add_buttons("Anuluj", Gtk.ResponseType.CANCEL, "Zapisz", Gtk.ResponseType.OK)
            dlg.set_do_overwrite_confirmation(True)
            dlg.set_current_name("arkusz-etykiet.pdf")
            start = self.state.get("last_dir") or DEFAULT_DIR
            if os.path.isdir(start):
                dlg.set_current_folder(start)
            if dlg.run() == Gtk.ResponseType.OK:
                path = dlg.get_filename()
                dlg.destroy()
                try:
                    save_sheet_pdf(self.build_sheet(), path)
                    self.status(f"Zapisano: {path}", "ok")
                except Exception as e:  # noqa: BLE001
                    self.message(Gtk.MessageType.ERROR, "Nie udało się zapisać PDF", secondary=str(e))
            else:
                dlg.destroy()

        def on_print(self, _):
            act = self.active_slots()
            if not act:
                return self.message(Gtk.MessageType.INFO, "Nie ma czego drukować",
                                    secondary="Przypisz plik do przynajmniej jednej wolnej ćwiartki.")
            printer = self.cmb_printer.get_active_text()
            if not printer:
                return self.message(Gtk.MessageType.ERROR, "Brak drukarki", secondary="Nie wykryto drukarek CUPS (lpstat -e).")
            warn = any(compute_layout(i, self.slots[i], self.margin)["warnings"] for i in act)
            names = ", ".join(str(i + 1) for i in act)
            sec = f"Drukarka: {printer}\nKopie: {int(self.sp_copies.get_value())}"
            if self.chk_guides.get_active():
                sec += "\nRamki naklejek: TAK (tryb próbny)"
            if warn:
                sec += "\n\n⚠ Są ostrzeżenia o wychodzeniu poza naklejkę/obszar druku!"
            r = self.message(Gtk.MessageType.WARNING if warn else Gtk.MessageType.QUESTION,
                             f"Wydrukować ćwiartki: {names}?", Gtk.ButtonsType.OK_CANCEL, sec)
            if r != Gtk.ResponseType.OK:
                return
            self.status("Przygotowuję arkusz (600 dpi)…")
            while Gtk.events_pending():
                Gtk.main_iteration()
            try:
                with tempfile.TemporaryDirectory() as d:
                    pdf = os.path.join(d, "arkusz-etykiet.pdf")
                    save_sheet_pdf(self.build_sheet(), pdf)
                    out = send_to_printer(pdf, printer, int(self.sp_copies.get_value()))
            except Exception as e:  # noqa: BLE001
                self.status(f"Błąd drukowania: {e}", "warn")
                return self.message(Gtk.MessageType.ERROR, "Nie udało się wysłać do drukarki", secondary=str(e))
            if not self.chk_guides.get_active():      # wydruk próbny nie zużywa naklejek
                for i in act:
                    self.slots[i].used = True
                    self.slots[i].item = None
                nxt = next((i for i, s in enumerate(self.slots) if not s.used), self.sel)
                self.sel = nxt
                self.persist()
            self.sync_panel()
            self.status(f"Wysłano do drukarki ({out}). Zużyte ćwiartki: {names}."
                        if not self.chk_guides.get_active() else f"Wysłano wydruk próbny ({out}).", "ok")

    win = Window()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    run_gui([os.path.abspath(a) for a in sys.argv[1:] if os.path.isfile(a)])
