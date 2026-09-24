"""Lightweight, resolution-independent Mac workspace icons."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_NAV = "#46588f"
_ACCENT = "#6038e8"
_ACTIVE = "#ffffff"


def nav_icon(name: str, size: int = 16) -> QIcon:
    """Return a compact sidebar icon, with a white variant for checked buttons."""
    icon = QIcon()
    icon.addPixmap(_render(name, size, _NAV), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_render(name, size, _ACTIVE), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(_render(name, size, _NAV), QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(_render(name, size, _ACTIVE), QIcon.Mode.Active, QIcon.State.On)
    return icon


def heading_icon(name: str, size: int = 28) -> QIcon:
    """Return a larger purple icon for a page heading."""
    return QIcon(_render(name, size, _ACCENT))


def action_icon(name: str, size: int = 16, color: str = _NAV) -> QIcon:
    """Return a blue icon for compact actions such as Add or Choose folder."""
    return QIcon(_render(name, size, color))


def _render(name: str, size: int, color: str) -> QPixmap:
    draw = _DRAWERS.get(name.casefold())
    if draw is None:
        raise ValueError(f"Unknown Mac icon: {name!r}")
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.scale(size / 24, size / 24)
    pen = QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    draw(painter)
    painter.end()
    return pixmap


def _queue(p: QPainter) -> None:
    p.drawRoundedRect(4.5, 3.5, 15, 17, 2, 2)
    for y in (8, 12, 16):
        p.drawLine(8, y, 16.5, y)
    p.drawLine(6.5, 8, 6.5, 8)
    p.drawLine(6.5, 12, 6.5, 12)
    p.drawLine(6.5, 16, 6.5, 16)


def _speech(p: QPainter) -> None:
    p.drawRoundedRect(9, 3.5, 6, 11, 3, 3)
    p.drawArc(6, 6, 12, 12, 180 * 16, 180 * 16)
    p.drawLine(12, 18, 12, 21)
    p.drawLine(8.5, 21, 15.5, 21)


def _translate(p: QPainter) -> None:
    p.drawLine(3.5, 6, 15.5, 6)
    p.drawLine(9.5, 4, 9.5, 6)
    p.drawLine(5.5, 9, 13.5, 17)
    p.drawLine(13.5, 9, 5.5, 17)
    p.drawLine(14.5, 19, 20, 19)
    p.drawLine(18, 16, 21, 19)
    p.drawLine(18, 22, 21, 19)


def _history(p: QPainter) -> None:
    p.drawArc(5, 5, 14, 14, 35 * 16, 300 * 16)
    p.drawLine(5, 5, 5, 10)
    p.drawLine(5, 5, 10, 5)
    p.drawLine(12, 8, 12, 12)
    p.drawLine(12, 12, 15, 14)


def _settings(p: QPainter) -> None:
    points = [(12, 2.5), (14, 3), (14.7, 5), (16.8, 6.2), (18.8, 5.6), (20.2, 7),
              (19.4, 9), (20.2, 11), (22, 12), (22, 14), (20.2, 15), (19.4, 17),
              (20.2, 19), (18.8, 20.4), (16.8, 19.8), (14.7, 21), (14, 23), (12, 23.5),
              (10, 23), (9.3, 21), (7.2, 19.8), (5.2, 20.4), (3.8, 19), (4.6, 17),
              (3.8, 15), (2, 14), (2, 12), (3.8, 11), (4.6, 9), (3.8, 7), (5.2, 5.6),
              (7.2, 6.2), (9.3, 5), (10, 3)]
    path = QPainterPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(9, 9, 6, 6)


def _help(p: QPainter) -> None:
    p.drawEllipse(3.5, 3.5, 17, 17)
    p.drawText(3.5, 4, 17, 17, Qt.AlignmentFlag.AlignCenter, "?")


def _folder(p: QPainter) -> None:
    p.drawPath(_path([(3, 6), (9, 6), (11, 8), (21, 8), (21, 19), (3, 19)], close=True))
    p.drawLine(3, 10, 21, 10)


def _add(p: QPainter) -> None:
    p.drawEllipse(3.5, 3.5, 17, 17)
    p.drawLine(12, 7.5, 12, 16.5)
    p.drawLine(7.5, 12, 16.5, 12)


def _path(points: list[tuple[float, float]], close: bool = False) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    if close:
        path.closeSubpath()
    return path


_DRAWERS: dict[str, Callable[[QPainter], None]] = {
    "queue": _queue,
    "speech": _speech,
    "microphone": _speech,
    "translate": _translate,
    "history": _history,
    "settings": _settings,
    "help": _help,
    "folder": _folder,
    "add": _add,
}
