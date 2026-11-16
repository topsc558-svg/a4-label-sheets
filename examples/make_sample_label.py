#!/usr/bin/env python3
"""Generates examples/sample-label.pdf — a fictional 4x6" shipping label (no real data)."""
import random
import cairo

W, H = 288, 432  # 4x6 inch in points
out = __file__.rsplit("/", 1)[0] + "/sample-label.pdf"
s = cairo.PDFSurface(out, W, H)
cr = cairo.Context(s)
cr.set_source_rgb(1, 1, 1); cr.paint()
cr.set_source_rgb(0, 0, 0)


def text(x, y, t, size, bold=False, mono=False):
    cr.select_font_face("DejaVu Sans Mono" if mono else "DejaVu Sans", cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size); cr.move_to(x, y); cr.show_text(t)


def bars(x, y, w, h, seed):
    rnd = random.Random(seed); cx = x
    while cx < x + w - 3:
        bw = rnd.choice([1, 1, 2, 3]); gap = rnd.choice([1, 2, 2])
        cr.rectangle(cx, y, bw, h); cr.fill(); cx += bw + gap


cr.set_line_width(1)
text(14, 22, "ORIGIN: EXAMPLETOWN", 7); text(14, 32, "ACME SHIPPING CO.", 7); text(14, 42, "1 SAMPLE ROAD", 7)
text(160, 22, "SHIP DATE: 01JAN30", 7); text(160, 32, "WEIGHT: 1.00 KG", 7); text(160, 42, "BILL SENDER", 7)
cr.move_to(150, 12); cr.line_to(150, 48); cr.stroke()
cr.move_to(8, 54); cr.line_to(280, 54); cr.stroke()
text(14, 78, "JANE EXAMPLE", 15, bold=True)
text(14, 108, "123 SAMPLE STREET", 13, bold=True)
text(14, 138, "EXAMPLE CITY XX 00000", 13, bold=True)
text(14, 150, "REF: SAMPLE-0001", 7)
bars(14, 162, 190, 80, 7)
cr.rectangle(216, 170, 56, 56); cr.set_line_width(4); cr.stroke(); cr.set_line_width(1)
text(232, 210, "E", 40, bold=True)
text(212, 160, "ACME", 14, bold=True)
cr.move_to(8, 262); cr.set_line_width(2); cr.line_to(280, 262); cr.stroke()
text(14, 290, "TRK#  0000 0000 0000", 13, bold=True)
text(14, 345, "XX SAMPLE", 28, bold=True)
text(200, 292, "10:30A", 15, bold=True, mono=True); text(200, 312, "IP EXP", 15, bold=True, mono=True)
bars(30, 360, 228, 60, 21)
s.finish()
print("written", out)
