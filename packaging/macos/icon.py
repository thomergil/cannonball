"""Draw the app icon: python icon.py OUT.icns [PREVIEW.png] (needs PyObjC; run by build-macos.sh).

A camera outline with a Wi-Fi fan, on the macOS rounded-square grid. The
README shows icon.png, made from PREVIEW.png with: sips -z 256 256 PREVIEW.png --out icon.png
"""

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
    NSPNGFileType,
    NSShadow,
)
from Foundation import NSMakePoint, NSMakeRect, NSMakeSize

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cannonball import drawing  # noqa: E402

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
SHUTTER_CORNER = 12
LENS_CENTER = (492, 465)
LENS_RADII = (130, 72)

BADGE_CENTER = (760, 680)  # center of the background-filled disc behind the fan
BADGE_RADIUS = 145
FAN_COLOR = (0.98, 0.62, 0.15)
FAN_DOT = 26
FAN_RADII = (78, 130)
FAN_SPAN = (45, 135)  # degrees: the fan opens upward
FAN_CENTER = (760, 605)

SIZES = [16, 32, 128, 256, 512]  # each also at @2x


def rgb(c, alpha=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(*c, alpha)


def stroke(path) -> None:
    drawing.stroke(path, STROKE, rgb(LINE))


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
    stroke(outline)
    prism = NSBezierPath.bezierPath()
    prism.moveToPoint_(NSMakePoint(*PRISM[0]))
    for point in PRISM[1:]:
        prism.lineToPoint_(NSMakePoint(*point))
    stroke(prism)
    stroke(NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(SHUTTER, SHUTTER_CORNER, SHUTTER_CORNER))
    for radius in LENS_RADII:
        stroke(drawing.circle(LENS_CENTER, radius))


def fan() -> None:
    # Fill a disc with the background to hide the camera lines behind the fan, then draw the fan.
    body_gradient(drawing.circle(BADGE_CENTER, BADGE_RADIUS))
    drawing.wifi_fan(FAN_CENTER, FAN_RADII, STROKE, FAN_DOT, FAN_SPAN, rgb(FAN_COLOR))


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
    fan()
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
