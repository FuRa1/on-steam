#!/usr/bin/env python3
"""Draw the app icons (icon.png 80x80, largeIcon.png 130x130) with only the standard library.

A blue rounded square with a white ring and a "play" triangle. Replace the PNGs with your
own art if you like; build-tv.sh only regenerates them when they are missing.
"""

import struct
import sys
import zlib
from pathlib import Path

TOP = (0x3B, 0x82, 0xF6)
BOTTOM = (0x1D, 0x3F, 0x9E)
WHITE = (255, 255, 255)
TRI = ((0.43, 0.33), (0.43, 0.67), (0.69, 0.50))


def in_rounded_square(u, v, r=0.22):
    cu = min(max(u, r), 1 - r)
    cv = min(max(v, r), 1 - r)
    return (u - cu) ** 2 + (v - cv) ** 2 <= r * r


def in_triangle(u, v):
    (x1, y1), (x2, y2), (x3, y3) = TRI
    d1 = (u - x2) * (y1 - y2) - (x1 - x2) * (v - y2)
    d2 = (u - x3) * (y2 - y3) - (x2 - x3) * (v - y3)
    d3 = (u - x1) * (y3 - y1) - (x3 - x1) * (v - y1)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def sample(u, v):
    """RGBA of one point (u, v in 0..1)."""
    if not in_rounded_square(u, v):
        return (0, 0, 0, 0)
    d2 = (u - 0.5) ** 2 + (v - 0.5) ** 2
    if 0.30 ** 2 <= d2 <= 0.355 ** 2 or in_triangle(u, v):
        return WHITE + (255,)
    col = tuple(round(t + (b - t) * v) for t, b in zip(TOP, BOTTOM))
    return col + (255,)


def render(size, ss=4):
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter type: none
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    r, g, b, a = sample((x + (sx + 0.5) / ss) / size, (y + (sy + 0.5) / ss) / size)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            n = ss * ss
            a = acc[3]
            if a:
                rows += bytes((round(acc[0] / a), round(acc[1] / a), round(acc[2] / a), round(a / n)))
            else:
                rows += b"\0\0\0\0"
    return bytes(rows)


def png(size):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(render(size), 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    (out / "icon.png").write_bytes(png(80))
    (out / "largeIcon.png").write_bytes(png(130))
    print(f"wrote {out / 'icon.png'} and {out / 'largeIcon.png'}")
