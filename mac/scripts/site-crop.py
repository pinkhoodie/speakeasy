"""Crop PanelSmoke 2x snapshots into website images: drop the grey margin, round the corners."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

PAD, RADIUS = 32, 36  # 16 pt margin and 18 pt panel radius, at 2x

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
dst.mkdir(parents=True, exist_ok=True)
for name, out in [pair.split("=") for pair in sys.argv[3:]]:
    im = Image.open(src / name).convert("RGBA")
    w, h = im.size
    im = im.crop((PAD, PAD, w - PAD, h - PAD))
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.size[0] - 1, im.size[1] - 1), RADIUS, fill=255)
    im.putalpha(mask)
    im.save(dst / out, optimize=True)
    print(out, im.size)
