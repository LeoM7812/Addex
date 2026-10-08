"""Writes the Addex logo (256x256, white on transparent) and background PNGs.

Usage: python services/api/scripts/make_images.py services/api/src/addex_api/static
"""
import math
import struct
import sys
import zlib
from pathlib import Path


def png(path, w, h, rows, alpha):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    color = 6 if alpha else 2
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    Path(path).write_bytes(data)


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def inside_tri(px, py, a, b, c):
    def s(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = s((px, py), a, b), s((px, py), b, c), s((px, py), c, a)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def logo_cover(x, y):
    # Magnifying glass: ring + handle, with a play triangle in the lens.
    cx, cy, r, thick = 108, 108, 72, 18
    if abs(math.hypot(x - cx, y - cy) - r) <= thick / 2:
        return True
    if seg_dist(x, y, 162, 162, 220, 220) <= 15:
        return True
    return inside_tri(x, y, (88, 72), (88, 144), (148, 108))


def logo(path, size=256, ss=4):
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            hits = sum(logo_cover(x + (i + 0.5) / ss, y + (j + 0.5) / ss)
                       for i in range(ss) for j in range(ss))
            row += [255, 255, 255, round(255 * hits / (ss * ss))]
        rows.append(row)
    png(path, size, size, rows, alpha=True)


def background(path, w=1280, h=800):
    top, bottom, glow = (27, 22, 64), (8, 8, 16), (93, 63, 211)
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            t = y / (h - 1)
            g = max(0.0, 1 - math.hypot((x - w * 0.75) / (w * 0.6), (y - h * 0.2) / (h * 0.7)))
            row += [round(top[k] * (1 - t) + bottom[k] * t + glow[k] * 0.35 * g * g) for k in range(3)]
        rows.append([min(255, v) for v in row])
    png(path, w, h, rows, alpha=False)


out = Path(sys.argv[1])
logo(out / "logo.png")
background(out / "background.png")
print("ok")
