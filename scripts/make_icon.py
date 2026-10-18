"""Build Raven's icons from two source PNGs.

  python scripts/make_icon.py "<app icon.png>" "<small logo.png>"

  app icon   -> packaging/icon_1024.png, packaging/Raven.icns (Dock/Finder) and raven2/assets/icon.png
  small logo -> raven2/assets/logo.png (top-left of the window and the menubar icon)

The sources are full-bleed rounded squares on an opaque near-black canvas (no transparency), so each is clipped to
a rounded rectangle (corner radius measured from the art: ~24% of the side), slightly inset so no dark fringe is
left at the edge. The app icon is then fitted to macOS's icon grid (artwork 824px inside a 1024px canvas)."""
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
ASSETS = ROOT / "raven2" / "assets"
CORNER = 0.236   # corner radius / side, measured where the shape edge crosses the corner diagonal
INSET = 5        # px (of 1254) clipped inside the art's own edge, to drop the dark antialiasing fringe


def rounded_cutout(img: QImage) -> QImage:
    """Clip to the art's rounded square: transparent outside, antialiased edge."""
    img = img.convertToFormat(QImage.Format_ARGB32)
    s = min(img.width(), img.height())
    inset = INSET * s / 1254
    r = CORNER * s - inset
    out = QImage(img.width(), img.height(), QImage.Format_ARGB32)
    out.fill(0)
    path = QPainterPath()
    path.addRoundedRect(QRectF(inset, inset, img.width() - 2 * inset, img.height() - 2 * inset), r, r)
    p = QPainter(out)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    p.setClipPath(path)
    p.drawImage(0, 0, img)
    p.end()
    return out


def on_grid(img: QImage, shape: int = 824, canvas: int = 1024) -> QImage:
    art = img.scaled(shape, shape, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = QImage(canvas, canvas, QImage.Format_ARGB32)
    out.fill(0)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage((canvas - art.width()) // 2, (canvas - art.height()) // 2, art)
    p.end()
    return out


def tight(img: QImage) -> QImage:
    """Crop to the cut-out shape (so the small logo fills its box)."""
    inset = int(INSET * min(img.width(), img.height()) / 1254)
    return img.copy(inset, inset, img.width() - 2 * inset, img.height() - 2 * inset)


def main(app_src: str, logo_src: str | None) -> None:
    QGuiApplication.instance() or QGuiApplication([])
    PKG.mkdir(exist_ok=True)
    ASSETS.mkdir(exist_ok=True)
    master = on_grid(rounded_cutout(QImage(app_src)))
    master.save(str(PKG / "icon_1024.png"))
    master.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(ASSETS / "icon.png"))
    iconset = PKG / "Raven.iconset"
    iconset.mkdir(exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            master.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(
                str(iconset / f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(PKG / "Raven.icns")], check=True)
    for f in iconset.iterdir():
        f.unlink()
    iconset.rmdir()
    print("wrote", PKG / "icon_1024.png", PKG / "Raven.icns", ASSETS / "icon.png")
    if logo_src:
        tight(rounded_cutout(QImage(logo_src))).scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(
            str(ASSETS / "logo.png"))
        print("wrote", ASSETS / "logo.png")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
