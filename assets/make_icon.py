"""Regenerate assets/veiltunnel.ico (requires Pillow): py assets/make_icon.py"""

import os

from PIL import Image, ImageDraw

SIZE = 256
BG = (24, 36, 52, 255)
RINGS = [(200, (64, 196, 180, 255)), (140, (120, 220, 205, 255)), (80, (200, 245, 238, 255))]


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=48, fill=BG)
    cx, base = SIZE // 2, 196
    # Nested arches = a tunnel mouth seen head-on.
    for width, color in RINGS:
        r = width // 2
        d.pieslice((cx - r, base - r, cx + r, base + r), 180, 360, fill=color)
        inner = r - 16
        d.pieslice((cx - inner, base - inner, cx + inner, base + inner), 180, 360, fill=BG)
    d.rectangle((24, base, SIZE - 24, base + 14), fill=(64, 196, 180, 255))
    return img


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    img = draw()
    img.save(os.path.join(here, "veiltunnel.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(os.path.join(here, "veiltunnel.png"))
    print("wrote veiltunnel.ico / veiltunnel.png in", here)
