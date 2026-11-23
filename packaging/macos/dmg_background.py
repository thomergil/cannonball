"""Draw the disk image window's background: the app's name and an arrow from
the app to Applications, in the icon's colors.

    python packaging/macos/dmg_background.py OUT_DIR

writes OUT_DIR/background.png and background@2x.png; dmgbuild combines the two
into one image that stays sharp on Retina screens. The window, the icon
positions and this drawing share WINDOW, APP_AT and APPLICATIONS_AT, which
packaging/macos/dmg-settings.py reads.
"""

import sys
from pathlib import Path

from AppKit import (
    NSAffineTransform,
    NSBezierPath,
    NSBitmapImageRep,
    NSCalibratedRGBColorSpace,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSLineCapStyleRound,
    NSLineJoinStyleRound,
    NSPNGFileType,
)
from Foundation import NSAttributedString, NSMakePoint, NSMakeRect

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cannonball import consts  # noqa: E402

# Finder window content, in points; positions are icon centers, from the top left
WINDOW = (640, 350)
APP_AT = (145, 165)
APPLICATIONS_AT = (495, 165)
ICON_SIZE = 104

PAPER = ((0.98, 0.98, 0.99), (0.93, 0.94, 0.96))  # top, bottom: light, so Finder's black labels read
INK = (0.20, 0.27, 0.40)  # the icon's navy
ARROW = (0.98, 0.62, 0.15)  # the icon's Wi-Fi fan orange
RULE = (0.85, 0.86, 0.89)
TITLE_AT = (32, 54)  # baseline start
TITLE_SIZE = 22
RULE_Y = 83
ARROW_SHAFT = (266, 356)  # x from, x to, at the icons' height
ARROW_HEAD = 23  # how far the head reaches back, and half its height less 3
ARROW_WIDTH = 14
SCALES = {"background.png": 1, "background@2x.png": 2}


def rgb(c):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(*c, 1.0)


def flip(x: float, y: float):
    """A point given from the top left, in AppKit's bottom-left coordinates."""
    return NSMakePoint(x, WINDOW[1] - y)


def line(points, width: float, color) -> None:
    path = NSBezierPath.bezierPath()
    path.setLineWidth_(width)
    path.setLineCapStyle_(NSLineCapStyleRound)
    path.setLineJoinStyle_(NSLineJoinStyleRound)
    path.moveToPoint_(flip(*points[0]))
    for point in points[1:]:
        path.lineToPoint_(flip(*point))
    rgb(color).set()
    path.stroke()


def paint() -> None:
    width, height = WINDOW
    gradient = NSGradient.alloc().initWithStartingColor_endingColor_(rgb(PAPER[0]), rgb(PAPER[1]))
    gradient.drawInRect_angle_(NSMakeRect(0, 0, width, height), -90)
    title = NSAttributedString.alloc().initWithString_attributes_(
        consts.APP_NAME,
        {NSFontAttributeName: NSFont.boldSystemFontOfSize_(TITLE_SIZE), NSForegroundColorAttributeName: rgb(INK)},
    )
    # drawAtPoint_ places the line's bottom-left corner; lift it by the font's descender
    descender = -NSFont.boldSystemFontOfSize_(TITLE_SIZE).descender()
    origin = flip(*TITLE_AT)
    title.drawAtPoint_(NSMakePoint(origin.x, origin.y - descender))
    line([(TITLE_AT[0], RULE_Y), (width - TITLE_AT[0], RULE_Y)], 1, RULE)
    start, end = ARROW_SHAFT
    y = APP_AT[1]
    line([(start, y), (end, y)], ARROW_WIDTH, ARROW)
    tip = end + 9
    line([(tip - ARROW_HEAD, y - 20), (tip, y), (tip - ARROW_HEAD, y + 20)], ARROW_WIDTH, ARROW)


def render(scale: int) -> NSBitmapImageRep:
    width, height = WINDOW
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, width * scale, height * scale, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
    )
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    zoom = NSAffineTransform.transform()
    zoom.scaleBy_(scale)
    zoom.concat()
    paint()
    NSGraphicsContext.restoreGraphicsState()
    rep.setSize_((width, height))  # points, so the @2x image is twice as dense, not twice as big
    return rep


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, scale in SCALES.items():
        png = render(scale).representationUsingType_properties_(NSPNGFileType, {})
        png.writeToFile_atomically_(str(out / name), True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
