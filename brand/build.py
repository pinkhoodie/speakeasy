"""Speakeasy brand assets from one source: the 1a keyhole (soundwave cut into the round top).

Writes brand/*.svg and mac/Resources/AppIcon.icns. Run: python3 brand/build.py (needs Google Chrome
for rasterising and macOS iconutil)."""
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "brand"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BRASS, BRASS2, BAR = "#d4a24c", "#f0c872", "#0d0b09"

# Keyhole in a 512 box: round top centred (256,196) r100, tapered foot to y414. The bars sit a
# little above the circle's centre so they read as centred in the visible round top.
KEYHOLE = "M214 270.9 A100 100 0 1 1 298 270.9 L332 414 H180 Z"
BARS = [(34,), (70,), (104,), (70,), (34,)]
BAR_W, BAR_GAP, BAR_CY = 18, 13, 186


def bar_rects() -> list[tuple[float, float, float, float]]:
    total = len(BARS) * BAR_W + (len(BARS) - 1) * BAR_GAP
    x, out = 256 - total / 2, []
    for (h,) in BARS:
        out.append((x, BAR_CY - h / 2, BAR_W, h))
        x += BAR_W + BAR_GAP
    return out


def bars_svg(fill: str) -> str:
    return "".join(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="{w / 2}" fill="{fill}"/>'
                   for x, y, w, h in bar_rects())


def grad(y1=90, y2=420) -> str:
    return (f'<defs><linearGradient id="g" gradientUnits="userSpaceOnUse" x1="0" y1="{y1}" x2="0" y2="{y2}">'
            f'<stop offset="0" stop-color="{BRASS2}"/><stop offset="1" stop-color="{BRASS}"/></linearGradient></defs>')


def mark_svg() -> str:
    """Brass keyhole, transparent background (site header, docs)."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="156 86 200 338">{grad()}'
            f'<path d="{KEYHOLE}" fill="url(#g)"/>{bars_svg(BAR)}</svg>')


def icon_svg() -> str:
    """macOS app icon on Apple's grid: 1024 canvas, 824 rounded square, keyhole in brass on bar-dark."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">{grad(180, 840)}'
            '<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#221c15"/>'
            '<stop offset="1" stop-color="#0d0b09"/></linearGradient></defs>'
            '<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#bg)"/>'
            '<rect x="100.5" y="100.5" width="823" height="823" rx="184.5" fill="none" stroke="#3a3024" stroke-width="2"/>'
            f'<g transform="translate(512 520) scale(1.52) translate(-256 -250)"><path d="{KEYHOLE}" fill="url(#g)"/>'
            f'{bars_svg("#15110c")}</g></svg>')


def template_svg() -> str:
    """Single-colour glyph (black on transparent) for menu bars / favicons on light."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="156 86 200 338">'
            f'<path fill-rule="evenodd" d="{KEYHOLE} ' + " ".join(
                f"M{x + w / 2:.1f} {y:.1f} a{w / 2} {w / 2} 0 0 1 {w / 2} {w / 2} v{h - w} a{w / 2} {w / 2} 0 0 1 {-w} 0 v{-(h - w)} a{w / 2} {w / 2} 0 0 1 {w / 2} {-w / 2} Z"
                for x, y, w, h in bar_rects()) + '"/></svg>')


def rasterise(svg: str, size: int, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "i.html"
        sized = svg.replace("<svg ", '<svg width="%d" height="%d" ' % (size, size), 1)
        page.write_text('<html><body style="margin:0;background:transparent">'
                        f'<div style="width:{size}px;height:{size}px">{sized}</div></body></html>')
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--default-background-color=00000000",
                        f"--screenshot={out}", f"--window-size={size},{size}", f"file://{page}"],
                       check=True, capture_output=True, timeout=60)


def main() -> None:
    BRAND.mkdir(exist_ok=True)
    (BRAND / "mark.svg").write_text(mark_svg())
    (BRAND / "app-icon.svg").write_text(icon_svg())
    (BRAND / "glyph-template.svg").write_text(template_svg())
    with tempfile.TemporaryDirectory() as tmp:
        master = Path(tmp) / "icon_1024.png"
        rasterise(icon_svg(), 1024, master)
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for pt in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = pt * scale
                name = f"icon_{pt}x{pt}{'@2x' if scale == 2 else ''}.png"
                shutil.copy(master, iconset / name)
                subprocess.run(["sips", "-z", str(px), str(px), str(iconset / name)], check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "mac/Resources/AppIcon.icns")], check=True)
        shutil.copy(master, BRAND / "app-icon-1024.png")
    print("wrote brand/*.svg, brand/app-icon-1024.png, mac/Resources/AppIcon.icns")


if __name__ == "__main__":
    main()
