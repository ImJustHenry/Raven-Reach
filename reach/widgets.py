"""Compact, phone-call style widgets for the glasses.

All targets subclass the Raven SDK Button so gaze dwell / click behave exactly like
the SDK; only the painting is custom. Dwell progress is the SDK's own counter.
"""
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from raven_framework.components.button import Button

INK = "#F4F6F8"
MUTED = "#8A949C"
DIM = "#4A535A"
SURFACE = "#12181C"
SURFACE_ON = "#1E2A24"
GREEN = "#34C759"
RED = "#FF453A"
AMBER = "#FFB340"
BLUE = "#4FA3FF"
GRAY = "#3A4248"

AVATAR_COLORS = ["#5E5CE6", "#0A84FF", "#30B0C7", "#34C759", "#FF9F0A", "#BF5AF2", "#FF375F"]


def initials(name):
    words = [w for w in "".join(c if c.isalnum() or c == " " else " " for c in name).split() if w]
    if not words:
        return "?"
    return (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()


def avatar_color(name):
    return AVATAR_COLORS[sum(map(ord, name or "?")) % len(AVATAR_COLORS)]


def font(base, px, weight=QFont.Weight.DemiBold):
    f = QFont(base)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def icon(p, name, cx, cy, size, color):
    """Small vector icons drawn in code: no emoji, image files or brand marks."""
    p.save()
    p.translate(cx, cy)
    s = size / 2
    pen = QPen(QColor(color), max(2.0, size * 0.11), Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1, y1, x2, y2):
        p.drawLine(QPointF(x1 * s, y1 * s), QPointF(x2 * s, y2 * s))

    if name in {"phone", "hangup"}:
        # Same handset shape: horizontal = end call, tilted = call / accept.
        p.rotate(0 if name == "hangup" else 225)
        path = QPainterPath(QPointF(-0.72 * s, 0.05 * s))
        path.quadTo(QPointF(0, -0.95 * s), QPointF(0.72 * s, 0.05 * s))
        p.drawPath(path)
        thick = QPen(QColor(color), size * 0.26, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(thick)
        line(-0.72, 0.05, -0.62, 0.42)
        line(0.72, 0.05, 0.62, 0.42)
    elif name == "person":
        p.drawEllipse(QPointF(0, -0.35 * s), 0.3 * s, 0.3 * s)
        path = QPainterPath(QPointF(-0.7 * s, 0.8 * s))
        path.cubicTo(QPointF(-0.7 * s, 0.05 * s), QPointF(0.7 * s, 0.05 * s), QPointF(0.7 * s, 0.8 * s))
        p.drawPath(path)
    elif name == "qr":
        for x, y in [(-0.75, -0.75), (0.15, -0.75), (-0.75, 0.15)]:
            p.drawRect(QRectF(x * s, y * s, 0.6 * s, 0.6 * s))
        p.setBrush(QColor(color))
        p.drawRect(QRectF(0.25 * s, 0.25 * s, 0.25 * s, 0.25 * s))
        p.drawRect(QRectF(0.55 * s, 0.55 * s, 0.2 * s, 0.2 * s))
    elif name == "x":
        line(-0.55, -0.55, 0.55, 0.55); line(-0.55, 0.55, 0.55, -0.55)
    elif name == "check":
        line(-0.6, 0.0, -0.15, 0.45); line(-0.15, 0.45, 0.65, -0.45)
    elif name == "back":
        line(0.6, 0, -0.6, 0); line(-0.6, 0, -0.15, -0.45); line(-0.6, 0, -0.15, 0.45)
    elif name == "next":
        line(-0.6, 0, 0.6, 0); line(0.6, 0, 0.15, -0.45); line(0.6, 0, 0.15, 0.45)
    elif name == "chev_l":
        line(0.25, -0.55, -0.25, 0); line(-0.25, 0, 0.25, 0.55)
    elif name == "chev_r":
        line(-0.25, -0.55, 0.25, 0); line(0.25, 0, -0.25, 0.55)
    elif name == "pin":
        path = QPainterPath(QPointF(0, 0.85 * s))
        path.cubicTo(QPointF(-0.95 * s, -0.1 * s), QPointF(-0.6 * s, -0.85 * s), QPointF(0, -0.85 * s))
        path.cubicTo(QPointF(0.6 * s, -0.85 * s), QPointF(0.95 * s, -0.1 * s), QPointF(0, 0.85 * s))
        p.drawPath(path)
        p.drawEllipse(QPointF(0, -0.3 * s), 0.22 * s, 0.22 * s)
    elif name == "pulse":
        path = QPainterPath(QPointF(-0.85 * s, 0))
        for x, y in [(-0.4, 0), (-0.2, -0.6), (0.1, 0.6), (0.35, 0), (0.85, 0)]:
            path.lineTo(x * s, y * s)
        p.drawPath(path)
    elif name == "home":
        path = QPainterPath(QPointF(-0.7 * s, -0.05 * s))
        for x, y in [(0, -0.7), (0.7, -0.05)]:
            path.lineTo(x * s, y * s)
        p.drawPath(path)
        p.drawRoundedRect(QRectF(-0.5 * s, -0.15 * s, 1.0 * s, 0.8 * s), 0.1 * s, 0.1 * s)
    elif name == "wifi":
        for r in (0.25, 0.55, 0.85):
            p.drawArc(QRectF(-r * s, (-r + 0.45) * s, 2 * r * s, 2 * r * s), 45 * 16, 90 * 16)
        p.setBrush(QColor(color)); p.drawEllipse(QPointF(0, 0.45 * s), 0.08 * s, 0.08 * s)
    elif name == "refresh":
        p.drawArc(QRectF(-0.6 * s, -0.6 * s, 1.2 * s, 1.2 * s), 60 * 16, 280 * 16)
        line(0.3, -0.52, 0.62, -0.62); line(0.3, -0.52, 0.42, -0.2)
    elif name == "eye":
        path = QPainterPath(QPointF(-0.85 * s, 0))
        path.quadTo(QPointF(0, -0.85 * s), QPointF(0.85 * s, 0))
        path.quadTo(QPointF(0, 0.85 * s), QPointF(-0.85 * s, 0))
        p.drawPath(path)
        p.drawEllipse(QPointF(0, 0), 0.25 * s, 0.25 * s)
    p.restore()


class GazeButton(Button):
    """style: 'round' (call button), 'row' (list row with avatar), 'chip' (toggle), 'pill'."""
    def __init__(self, key, style, w, h, dwell_ms, *, label="", sub="", icon_name="",
                 color=GRAY, avatar=None, on=False, text="", title_color=None):
        self.key, self.style = key, style
        self.label, self.sub, self.icon_name, self.color = label, sub, icon_name, color
        self.avatar, self.on, self.big_text = avatar, on, text
        self.title_color = title_color
        self.selected_until = 0.0
        super().__init__(center_text=label or key, width=w, height=h, text_size=18,
                         background_color="#000000", text_color=INK, use_fill_dwell=False,
                         use_gradient_border=False, outline_width=0, corner_radius=h // 2,
                         scale_by=0.0, delay_time=200, dwell_time=dwell_ms)

    def frac(self):
        if self.is_disabled() or not self.underMouse() or not self.max_progress:
            return 0.0
        return max(0.0, min(1.0, self.progress / self.max_progress))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        disabled = self.is_disabled()
        hover = self.underMouse() and not disabled
        picked = time.monotonic() < self.selected_until
        getattr(self, "_paint_" + self.style)(p, disabled, hover, picked)
        p.end()

    # -- round call button: circle + icon, label underneath --
    def _paint_round(self, p, disabled, hover, picked):
        w = self.width()
        d = w - 12
        cx, cy = w / 2, 6 + d / 2
        fill = QColor(DIM if disabled else self.color)
        if hover:
            fill = fill.lighter(118)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawEllipse(QPointF(cx, cy), d / 2, d / 2)
        ink = "#9AA2A8" if disabled else INK
        if self.big_text:
            p.setFont(font(self.font, int(d * 0.27), QFont.Weight.Bold))
            p.setPen(QColor(ink))
            p.drawText(QRectF(cx - d / 2, cy - d / 2, d, d), Qt.AlignmentFlag.AlignCenter, self.big_text)
        else:
            icon(p, "check" if picked else self.icon_name, cx, cy, d * 0.42, ink)
        f = self.frac()
        if f > 0:
            p.setPen(QPen(QColor(INK), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = d / 2 + 3
            p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), 90 * 16, -int(360 * 16 * f))
        if self.label:
            p.setFont(font(self.font, 15, QFont.Weight.Medium))
            p.setPen(QColor(DIM if disabled else MUTED))
            p.drawText(QRectF(0, d + 12, w, self.height() - d - 12),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.label)

    def _bar(self, p, r):
        f = self.frac()
        if f > 0:
            p.setPen(QPen(QColor(INK), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            y = r.bottom() - 5
            p.drawLine(QPointF(r.left() + 18, y), QPointF(r.left() + 18 + (r.width() - 36) * f, y))

    # -- list row: avatar · name / subtitle · trailing round icon --
    def _paint_row(self, p, disabled, hover, picked):
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor("#3A4650" if hover else "#00000000"), 1.5))
        p.setBrush(QColor("#1A2227" if hover else SURFACE))
        p.drawRoundedRect(r, 20, 20)
        h = self.height()
        ad = h - 24
        name = self.avatar or self.label
        p.setPen(Qt.PenStyle.NoPen)
        sos = self.avatar == "SOS"
        p.setBrush(QColor(RED if sos else avatar_color(name)))
        p.drawEllipse(QRectF(12, 12, ad, ad))
        if sos:
            icon(p, "phone", 12 + ad / 2, 12 + ad / 2, ad * 0.42, INK)
        else:
            p.setPen(QColor(INK))
            p.setFont(font(self.font, int(ad * 0.38), QFont.Weight.Bold))
            p.drawText(QRectF(12, 12, ad, ad), Qt.AlignmentFlag.AlignCenter, initials(name))
        tx = 12 + ad + 14
        tw = self.width() - tx - h
        p.setFont(font(self.font, 19))
        p.setPen(QColor(self.title_color or INK))
        title = p.fontMetrics().elidedText(self.label, Qt.TextElideMode.ElideRight, int(tw))
        p.drawText(QRectF(tx, 12, tw, ad / 2 + 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        p.setFont(font(self.font, 14, QFont.Weight.Normal))
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(tx, 12 + ad / 2, tw, ad / 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.sub)
        bd = h - 30
        bx = self.width() - bd - 15
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(DIM if disabled else self.color))
        p.drawEllipse(QRectF(bx, 15, bd, bd))
        icon(p, "check" if picked else self.icon_name, bx + bd / 2, 15 + bd / 2, bd * 0.45, INK)
        self._bar(p, r)

    # -- toggle chip --
    def _paint_chip(self, p, disabled, hover, picked):
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.on and not disabled
        p.setPen(QPen(QColor(GREEN if on else ("#52606A" if hover else "#2A3338")), 2 if on or hover else 1.2))
        p.setBrush(QColor(SURFACE_ON if on else SURFACE))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        dot = 22
        dx = 16
        p.setPen(QPen(QColor(GREEN if on else MUTED), 2))
        p.setBrush(QColor(GREEN) if on else Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(dx, (self.height() - dot) / 2, dot, dot))
        if on:
            icon(p, "check", dx + dot / 2, self.height() / 2, dot * 0.6, "#0B1A10")
        p.setFont(font(self.font, 17))
        p.setPen(QColor(DIM if disabled else INK))
        p.drawText(QRectF(dx + dot + 10, 0, self.width() - dx - dot - 18, self.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.label)
        self._bar(p, r)

    # -- small pill button (icon + text) --
    def _paint_pill(self, p, disabled, hover, picked):
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor("#52606A" if hover else "#2A3338"), 1.2))
        p.setBrush(QColor(SURFACE))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        ink = DIM if disabled else INK
        p.setFont(font(self.font, 16))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(self.label)
        iw = 18 if self.icon_name else 0
        x0 = (self.width() - tw - iw - (8 if iw else 0)) / 2
        if self.icon_name:
            icon(p, self.icon_name, x0 + iw / 2, self.height() / 2, iw, ink)
        p.setPen(QColor(ink))
        p.drawText(QRectF(x0 + iw + (8 if iw else 0), 0, tw + 2, self.height()),
                   Qt.AlignmentFlag.AlignVCenter, self.label)
        self._bar(p, r)


class Avatar(QWidget):
    """Initials avatar; `ringing` animates soft rings like an incoming-call screen."""
    def __init__(self, parent, size, name="", color=None, ringing=False, icon_name=""):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.d, self.name, self.color, self.ringing, self.icon_name = size, name, color, ringing, icon_name
        pad = int(size * 0.35) if ringing else 0
        self.pad = pad
        self.setFixedSize(size + 2 * pad, size + 2 * pad)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.width() / 2
        base = QColor(self.color or avatar_color(self.name))
        if self.ringing:
            t = time.monotonic()
            for k in range(2):
                phase = ((t * 0.8) + k * 0.5) % 1.0
                r = self.d / 2 + phase * self.pad
                ring = QColor(base)
                ring.setAlphaF(0.45 * (1 - phase))
                p.setPen(QPen(ring, 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(c, c), r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawEllipse(QPointF(c, c), self.d / 2, self.d / 2)
        if self.icon_name:
            icon(p, self.icon_name, c, c, self.d * 0.42, INK)
        else:
            p.setPen(QColor(INK))
            p.setFont(font(self.font(), int(self.d * 0.36), QFont.Weight.Bold))
            p.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignCenter, initials(self.name))
        p.end()


def ellipsize(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


__all__ = ["GazeButton", "Avatar", "icon", "initials", "ellipsize", "INK", "MUTED", "DIM", "GREEN",
           "RED", "AMBER", "BLUE", "GRAY", "SURFACE", "math"]


class QRView(QWidget):
    """Draws a QR code (dark modules on a white tile with a quiet zone)."""
    def __init__(self, parent, size, data):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(size, size)
        import qrcode
        q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(data)
        q.make(fit=True)
        self.matrix = q.get_matrix()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(self.rect()), 18, 18)
        n = len(self.matrix)
        quiet = 4
        cell = self.width() / (n + 2 * quiet)
        p.setBrush(QColor("#000000"))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for y, row in enumerate(self.matrix):
            for x, on in enumerate(row):
                if on:
                    p.drawRect(QRectF((x + quiet) * cell, (y + quiet) * cell, cell + 0.5, cell + 0.5))
        p.end()
