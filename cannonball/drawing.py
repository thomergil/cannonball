"""Vector drawing shared by the menu bar icon and the app icon (AppKit)."""

from AppKit import NSBezierPath, NSLineCapStyleRound, NSLineJoinStyleRound
from Foundation import NSMakePoint, NSMakeRect


def circle(center, radius: float) -> NSBezierPath:
    x, y = center
    return NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - radius, y - radius, 2 * radius, 2 * radius))


def stroke(path: NSBezierPath, width: float, color) -> None:
    """Stroke path with round caps and joins."""
    path.setLineWidth_(width)
    path.setLineCapStyle_(NSLineCapStyleRound)
    path.setLineJoinStyle_(NSLineJoinStyleRound)
    color.set()
    path.stroke()


def wifi_fan(center, radii, line: float, dot: float, span: tuple[float, float], lit_color, faint_color=None, lit=None) -> None:
    """Draw a Wi-Fi fan into the current graphics context.

    A dot of radius `dot` at `center` and one arc per radius, from span[0] to
    span[1] degrees. The first `lit` arcs (all, if None) use lit_color, the
    rest faint_color.
    """
    lit = len(radii) if lit is None else lit
    lit_color.set()
    circle(center, dot).fill()
    for i, radius in enumerate(radii):
        arc = NSBezierPath.bezierPath()
        arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(NSMakePoint(*center), radius, *span)
        stroke(arc, line, lit_color if i < lit else faint_color)
