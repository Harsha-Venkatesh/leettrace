#!/usr/bin/env python3
"""Draws the app icon (a small binary tree with a trace arrow) and writes AppIcon.icns. Needs Pillow."""
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw

S = 1024


def draw():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # gradient squircle
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)], fill=(int(40 + 30 * t), int(52 + 10 * t), int(120 - 40 * t), 255))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([100, 100, 924, 924], radius=190, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    nodes = {"a": (512, 300), "b": (340, 520), "c": (684, 520), "d": (250, 730), "e": (430, 730), "f": (774, 730)}
    edges = [("a", "b"), ("a", "c"), ("b", "d"), ("b", "e"), ("c", "f")]
    path = {"a", "b", "e"}
    for u, v in edges:
        col = (255, 160, 90, 255) if u in path and v in path else (190, 200, 235, 255)
        d.line([nodes[u], nodes[v]], fill=col, width=22)
    for k, (x, y) in nodes.items():
        r = 62
        if k == "e":
            d.ellipse([x - r - 18, y - r - 18, x + r + 18, y + r + 18], fill=(125, 149, 255, 110))
            d.ellipse([x - r, y - r, x + r, y + r], fill=(125, 149, 255, 255))
        elif k in path:
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 196, 110, 255))
        else:
            d.ellipse([x - r, y - r, x + r, y + r], fill=(235, 240, 255, 255))
    return img


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    img = draw()
    tmp = tempfile.mkdtemp()
    iconset = os.path.join(tmp, "AppIcon.iconset")
    os.makedirs(iconset)
    for size in (16, 32, 128, 256, 512):
        img.resize((size, size), Image.LANCZOS).save(os.path.join(iconset, f"icon_{size}x{size}.png"))
        img.resize((size * 2, size * 2), Image.LANCZOS).save(os.path.join(iconset, f"icon_{size}x{size}@2x.png"))
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(here, "AppIcon.icns")], check=True)
    shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
