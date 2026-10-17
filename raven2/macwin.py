"""macOS window chrome: a dark appearance and a unified title bar (transparent, same navy as the window, only the
traffic-light buttons showing), like Slack or Spotify. Everything here is a no-op off macOS / off the Cocoa Qt
platform (e.g. the headless test platform) and never raises: a styling failure must not stop the app."""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("raven")


def _cocoa() -> bool:
    from PySide6.QtGui import QGuiApplication

    return sys.platform == "darwin" and QGuiApplication.platformName() == "cocoa"


def force_dark_appearance() -> bool:
    """Dark native chrome for the whole app: title bars, scrollbars, standard dialogs."""
    if not _cocoa():
        return False
    try:
        from AppKit import NSApp, NSAppearance

        NSApp.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        return True
    except Exception:
        log.warning("couldn't force dark appearance", exc_info=True)
        return False


def unify_titlebar(win, bg_hex: str) -> bool:
    """Make the window's title bar transparent over the window's own background, hide the title text, and let
    the content run underneath it. Call after the window is shown (the native window must exist)."""
    if not _cocoa():
        return False
    try:
        import objc
        from AppKit import NSColor, NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden

        nswin = objc.objc_object(c_void_p=ctypes.c_void_p(int(win.winId()))).window()  # Qt's winId is the NSView
        if nswin is None:
            return False
        nswin.setTitlebarAppearsTransparent_(True)
        nswin.setTitleVisibility_(NSWindowTitleHidden)
        nswin.setStyleMask_(nswin.styleMask() | NSWindowStyleMaskFullSizeContentView)
        r, g, b = (int(bg_hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
        nswin.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0))
        nswin.setMovableByWindowBackground_(True)  # drag the window by its header, not just the (hidden) title
        return True
    except Exception:
        log.warning("couldn't style the title bar", exc_info=True)
        return False
