"""Turn a source PNG into the macOS app icon: solid rounded square, transparent outside it, scaled to the standard
icon grid (artwork 824px inside a 1024 canvas), then a full .iconset -> Raven.icns via iconutil.

Usage: python scripts/make_icon.py "<source.png>"   (writes packaging/icon_1024.png and packaging/Raven.icns)"""
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

OUT = Path(__file__).resolve().parent.parent / "packaging"


NAVY = (0x0B, 0x12, 0x22)  # the icon's background navy, sampled from the artwork


def solid_shape(img: QImage) -> QImage:
    """The source PNG is a rounded square on a transparent canvas, but its background removal left transparent
    HOLES inside the shape (they show through as white blotches on a light Dock/Finder). Keep the outer edge
    soft, and composite everything inside the shape over the icon's own navy so the shape is solid."""
    import numpy as np
    from scipy import ndimage

    img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(h, w, 4).copy()  # BGRA
    alpha = arr[..., 3]
    lab, _ = ndimage.label(alpha < 128)
    outside = lab == lab[0, 0]                         # transparent area connected to the canvas border
    ring = ndimage.binary_dilation(outside, iterations=4) & ~outside   # antialiased outer edge: keep as is
    inner = ~outside & ~ring
    a = alpha[inner].astype(np.float32)[:, None] / 255.0
    navy = np.array(NAVY[::-1], np.float32)             # BGR
    arr[..., :3][inner] = (arr[..., :3][inner] * a + navy * (1 - a)).astype(np.uint8)
    arr[..., 3][inner] = 255
    arr[..., 3][outside] = 0
    return QImage(arr.data, w, h, w * 4, QImage.Format_ARGB32).copy()


def fit_to_grid(img: QImage, shape: int = 824, canvas: int = 1024) -> QImage:
    xs, ys = [], []
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            if img.pixelColor(x, y).alpha() > 200:
                xs.append(x); ys.append(y)
    box = QRect(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
    art = img.copy(box).scaled(shape, shape, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = QImage(canvas, canvas, QImage.Format_ARGB32)
    out.fill(0)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage((canvas - art.width()) // 2, (canvas - art.height()) // 2, art)
    p.end()
    return out


def main(src: str) -> None:
    QGuiApplication.instance() or QGuiApplication([])
    master = fit_to_grid(solid_shape(QImage(src)))
    OUT.mkdir(exist_ok=True)
    master.save(str(OUT / "icon_1024.png"))
    assets = OUT.parent / "raven2" / "assets"
    assets.mkdir(exist_ok=True)
    master.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(assets / "icon.png"))
    iconset = OUT / "Raven.iconset"
    iconset.mkdir(exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            master.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(
                str(iconset / f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "Raven.icns")], check=True)
    for f in iconset.iterdir():
        f.unlink()
    iconset.rmdir()
    print("wrote", OUT / "icon_1024.png", "and", OUT / "Raven.icns")


if __name__ == "__main__":
    main(sys.argv[1])
