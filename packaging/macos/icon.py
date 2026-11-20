"""Draw the app icon: python icon.py OUT.icns [PREVIEW.png] (needs PyObjC; run by build-macos.sh).

A camera outline with a Wi-Fi badge, on the macOS rounded-square grid.
"""

import math
import subprocess
import sys
import tempfile
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSCalibratedRGBColorSpace,
    NSColor,
    NSGradient,
    NSGraphicsContext,
    NSLineCapStyleRound,
    NSLineJoinStyleRound,
    NSPNGFileType,
    NSShadow,
)
from Foundation import NSMakePoint, NSMakeRect, NSMakeSize

CANVAS = 1024
BODY = NSMakeRect(100, 100, 824, 824)  # macOS icon grid: 824 pt rounded square
CORNER = 185
BACKGROUND = ((0.20, 0.27, 0.40), (0.07, 0.09, 0.15))  # top, bottom
LINE = (0.96, 0.97, 0.99)
STROKE = 34

CAMERA = NSMakeRect(212, 290, 560, 360)
CAMERA_CORNER = 70
PRISM = [(392, 650), (432, 740), (552, 740), (592, 650)]  # viewfinder hump, left to right
SHUTTER = NSMakeRect(262, 650, 90, 30)
LENS_CENTER = (492, 465)
LENS_RADII = (130, 72)

BADGE_CENTER = (760, 680)
BADGE_RADIUS = 145
WIFI = (0.98, 0.62, 0.15)
WIFI_DOT = 26
WIFI_RADII = (78, 130)
WIFI_SPAN = (45, 135)  # degrees: the fan opens upward
WIFI_ORIGIN = (760, 605)

SIZES = [16, 32, 128, 256, 512]  # each also at @2x


def rgb(c, alpha=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(*c, alpha)


def stroke(path, color, width=STROKE) -> None:
    path.setLineWidth_(width)
    path.setLineCapStyle_(NSLineCapStyleRound)
    path.setLineJoinStyle_(NSLineJoinStyleRound)
    rgb(color).set()
    path.stroke()


def circle(center, radius) -> NSBezierPath:
    x, y = center
    return NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - radius, y - radius, 2 * radius, 2 * radius))


def body_gradient(path) -> None:
    """Fill path with the background, aligned to the whole icon."""
    NSGraphicsContext.saveGraphicsState()
    path.addClip()
    NSGradient.alloc().initWithStartingColor_endingColor_(rgb(BACKGROUND[0]), rgb(BACKGROUND[1])).drawInRect_angle_(BODY, -90)
    NSGraphicsContext.restoreGraphicsState()


def background(body) -> None:
    shadow = NSShadow.alloc().init()
    shadow.setShadowColor_(rgb((0, 0, 0), 0.35))
    shadow.setShadowOffset_(NSMakeSize(0, -12))
    shadow.setShadowBlurRadius_(28)
    NSGraphicsContext.saveGraphicsState()
    shadow.set()
    rgb(BACKGROUND[1]).set()
    body.fill()
    NSGraphicsContext.restoreGraphicsState()
    body_gradient(body)


def camera() -> None:
    outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(CAMERA, CAMERA_CORNER, CAMERA_CORNER)
    stroke(outline, LINE)
    prism = NSBezierPath.bezierPath()
    prism.moveToPoint_(NSMakePoint(*PRISM[0]))
    for point in PRISM[1:]:
        prism.lineToPoint_(NSMakePoint(*point))
    stroke(prism, LINE)
    stroke(NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(SHUTTER, 12, 12), LINE)
    for radius in LENS_RADII:
        stroke(circle(LENS_CENTER, radius), LINE)


def wifi() -> None:
    # Knock the camera lines out behind the badge, then draw the Wi-Fi fan.
    body_gradient(circle(BADGE_CENTER, BADGE_RADIUS))
    rgb(WIFI).set()
    circle(WIFI_ORIGIN, WIFI_DOT).fill()
    start, end = WIFI_SPAN
    for radius in WIFI_RADII:
        arc = NSBezierPath.bezierPath()
        arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(NSMakePoint(*WIFI_ORIGIN), radius, start, end)
        stroke(arc, WIFI)


def draw() -> NSBitmapImageRep:
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, CANVAS, CANVAS, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
    )
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(BODY, CORNER, CORNER)
    background(body)
    NSGraphicsContext.saveGraphicsState()
    body.addClip()
    camera()
    wifi()
    NSGraphicsContext.restoreGraphicsState()
    NSGraphicsContext.restoreGraphicsState()
    return rep


def main(out: Path, preview: Path | None = None) -> None:
    png = draw().representationUsingType_properties_(NSPNGFileType, {})
    with tempfile.TemporaryDirectory() as tmp:
        master = Path(tmp) / "icon.png"
        png.writeToFile_atomically_(str(master), True)
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in SIZES:
            for scale, suffix in ((1, ""), (2, "@2x")):
                px = size * scale
                target = iconset / f"icon_{size}x{size}{suffix}.png"
                subprocess.run(["sips", "-z", str(px), str(px), str(master), "--out", str(target)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
        if preview:
            master.replace(preview)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
