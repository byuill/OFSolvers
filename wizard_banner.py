"""Small, original pixel-art interlude, painted locally with Qt (no assets/network).

The scene is drawn on a low-resolution image and enlarged by an integer factor.
Animation uses elapsed time, with exactly one scheduled trick per crossing.
"""
from dataclasses import dataclass
import math
import time

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPolygon
from PyQt6.QtWidgets import QPushButton, QWidget


TRICKS = ('hop', 'flame', 'speech', 'breakdance', 'stumble', 'pipe', 'lightning')
DURATIONS = (1.25, 1.8, 2.3, 2.1, 1.5, 2.9, 1.9)
SCALE = 3
SCENE_HEIGHT = 56
GROUND = 51


@dataclass
class WizardMotion:
    """A crossing pauses once at its midpoint, then completes and reverses."""
    progress: float = 0.
    pass_number: int = 0
    phase: str = 'walk'
    trick_elapsed: float = 0.
    performed: bool = False
    elapsed: float = 0.

    @property
    def direction(self):
        return 1 if self.pass_number % 2 == 0 else -1

    @property
    def trick(self):
        return TRICKS[self.pass_number % len(TRICKS)]

    @property
    def duration(self):
        return DURATIONS[self.pass_number % len(TRICKS)]

    @property
    def trick_fraction(self):
        return min(1., self.trick_elapsed/self.duration)

    def advance(self, seconds, width):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Animation time must be finite and nonnegative.')
        remaining = seconds
        travel = max(1., width-44.)
        self.elapsed += seconds
        while remaining > 1e-10:
            if self.phase == 'trick':
                step = min(remaining, self.duration-self.trick_elapsed)
                self.trick_elapsed += step
                remaining -= step
                if self.trick_elapsed >= self.duration-1e-10:
                    self.phase = 'walk'
                    self.performed = True
                continue
            target = 1. if self.performed else .5
            step = min(remaining, max(0., (target-self.progress)*travel/38.))
            self.progress = min(target, self.progress+step*38./travel)
            remaining -= step
            if self.progress >= target-1e-10:
                self.progress = target
                if not self.performed:
                    self.phase = 'trick'
                    self.trick_elapsed = 0.
                else:
                    self.pass_number += 1
                    self.progress = 0.
                    self.performed = False

    def x(self, width):
        fraction = self.progress if self.direction == 1 else 1-self.progress
        return round(22+fraction*max(1, width-44))


def rectangle(painter, color, x, y, width, height):
    painter.fillRect(int(x), int(y), int(width), int(height), QColor(color))


def polygon(painter, color, points):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPolygon(QPolygon([QPoint(round(x), round(y)) for x, y in points]))


def line(painter, color, points, width=1):
    painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPolyline(QPolygon([QPoint(round(x), round(y)) for x, y in points]))


class WizardBanner(QWidget):
    """A compact banner; animation freezes while hidden, paused or minimized."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('wizardBanner')
        self.setFixedHeight(SCALE*SCENE_HEIGHT)
        self.setMinimumWidth(320)
        self.setAccessibleName('Pixel wizard landscape')
        self.setAccessibleDescription('A wizard crosses a forest, red castle and snowy mountain, performing one trick each pass.')
        self.setToolTip('One trick per crossing: hop, staff flame, ESSAYONS!, arm wave, stumble, pipe smoke ring, lightning.')
        self.motion = WizardMotion()
        self.paused = False
        self._background = None
        self._last_tick = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.pause_button = QPushButton('Pause wizard', self)
        self.pause_button.setCheckable(True)
        self.pause_button.setAccessibleName('Pause wizard animation')
        self.pause_button.setToolTip('Pause or resume the decorative animation')
        self.pause_button.setStyleSheet('QPushButton { color: #ecf4ef; background: #1c3148; border: 1px solid #68879b; padding: 3px 8px; } QPushButton:hover { background: #354f69; }')
        self.pause_button.toggled.connect(self.set_paused)
        self.pause_button.adjustSize()

    def sizeHint(self):
        return QSize(900, SCALE*SCENE_HEIGHT)

    def set_paused(self, paused):
        self.paused = bool(paused)
        self.pause_button.setChecked(self.paused)
        self.pause_button.setText('Resume wizard' if self.paused else 'Pause wizard')
        self.pause_button.setAccessibleName(self.pause_button.text()+' animation')
        self.pause_button.adjustSize()
        self._position_button()
        self._update_timer()

    def _position_button(self):
        self.pause_button.move(self.width()-self.pause_button.width()-8, 7)

    def _update_timer(self):
        self._last_tick = time.monotonic()
        if self.isVisible() and not self.paused:
            self.timer.start()
        else:
            self.timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_timer()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        self._background = None
        self._position_button()
        super().resizeEvent(event)

    def _tick(self):
        now = time.monotonic()
        seconds = min(.15, now-self._last_tick)
        self._last_tick = now
        if not self.window().isMinimized() and not self.paused:
            self.motion.advance(seconds, math.ceil(self.width()/SCALE))
            self.update()

    def frame_image(self):
        width = math.ceil(self.width()/SCALE)
        if self._background is None or self._background.width() != width:
            self._background = landscape(width)
        frame = self._background.copy()
        painter = QPainter(frame)
        try:
            draw_wizard(painter, self.motion, width)
        finally:
            painter.end()
        return frame

    def paintEvent(self, event):
        frame = self.frame_image()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(QRect(0, 0, frame.width()*SCALE, SCENE_HEIGHT*SCALE), frame)
        painter.end()


def pine(painter, x, base, height, dark, light):
    rectangle(painter, '#413e45', x-1, base-height//3, 3, height//3)
    for fraction, spread in ((0, .24), (.24, .36), (.48, .47)):
        top = base-height+round(height*fraction)
        half = max(2, round(height*spread))
        polygon(painter, dark, [(x, top), (x+half, top+height//2), (x-half, top+height//2)])
        polygon(painter, light, [(x, top+1), (x, top+height//2), (x-half+1, top+height//2)])


def castle(painter, x, base):
    """An original red three-tower engineering-castle silhouette."""
    rectangle(painter, '#442943', x-33, base-19, 67, 20)
    rectangle(painter, '#b63b42', x-31, base-18, 63, 18)
    rectangle(painter, '#ef745b', x-31, base-18, 63, 2)
    for dx, height in ((-25, 25), (0, 31), (25, 25)):
        left, top = x+dx-8, base-height
        rectangle(painter, '#442943', left-1, top, 19, height)
        rectangle(painter, '#ce484a', left, top, 16, height-1)
        rectangle(painter, '#ec6f57', left, top, 4, height-1)
        rectangle(painter, '#a22f40', left+13, top, 3, height-1)
        rectangle(painter, '#f28a68', left-2, top+4, 21, 2)
        for crenel in range(3):
            rectangle(painter, '#442943', left-2+crenel*8, top-3, 5, 8)
            rectangle(painter, '#ef745b', left-1+crenel*8, top-3, 3, 6)
        rectangle(painter, '#592f41', left+6, top+10, 4, 7)
        rectangle(painter, '#f6bc78', left+7, top+10, 1, 5)
        for row in range(top+20, base-1, 4):
            rectangle(painter, '#e36a55', left+2, row, 10, 1)
    # Tall central arched gate, heavy masonry base and a tiny red pennant.
    rectangle(painter, '#562d40', x-7, base-12, 15, 12)
    rectangle(painter, '#562d40', x-5, base-15, 11, 3)
    rectangle(painter, '#292b3b', x-5, base-11, 11, 11)
    rectangle(painter, '#292b3b', x-3, base-13, 7, 3)
    for gate_x in range(x-3, x+5, 3):
        rectangle(painter, '#784c4c', gate_x, base-10, 1, 10)
    rectangle(painter, '#eeaa77', x-35, base, 71, 2)
    rectangle(painter, '#786552', x-36, base+2, 73, 2)
    rectangle(painter, '#d9b68b', x, base-43, 1, 10)
    polygon(painter, '#f07861', [(x+1, base-43), (x+10, base-41), (x+1, base-38)])


def landscape(width):
    image = QImage(width, SCENE_HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor('#182941'))
    p = QPainter(image)
    try:
        rectangle(p, '#223851', 0, 16, width, 17)
        rectangle(p, '#31495c', 0, 33, width, 12)
        for i in range(max(6, width//19)):
            x, y = (17+i*43) % width, 3+(i*7) % 19
            rectangle(p, '#adc7cf', x, y, 1, 1)
            if i % 4 == 0:
                rectangle(p, '#688ca7', x-1, y, 3, 1)
                rectangle(p, '#688ca7', x, y-1, 1, 3)
                rectangle(p, '#f0e9c3', x, y, 1, 1)
        # Stepped snow cap, lit face and shaded ridge behind the red castle.
        peak = round(width*.82)
        polygon(p, '#536a87', [(peak-68, 44), (peak-26, 19), (peak, 3), (peak+56, 44)])
        polygon(p, '#748ba3', [(peak-68, 44), (peak, 3), (peak-7, 27), (peak+5, 44)])
        polygon(p, '#354d6a', [(peak, 3), (peak+56, 44), (peak+12, 32), (peak-7, 27)])
        polygon(p, '#edf4de', [(peak-25, 19), (peak, 3), (peak+23, 20), (peak+11, 17),
                              (peak+7, 21), (peak-1, 16), (peak-9, 22), (peak-14, 18)])
        polygon(p, '#adc7d4', [(peak, 3), (peak+23, 20), (peak+11, 17), (peak+7, 21), (peak-1, 16)])
        # Two forest depths. Integer geometry remains crisp at every frame.
        for i in range(width//9+2):
            pine(p, i*9, 43, 12+(i*7) % 12, '#1c4350', '#2c5b60')
        castle(p, round(width*.66), 43)
        for i in range(max(4, width//35)):
            pine(p, i*12-3, 46, 19+(i*5) % 12, '#183c42', '#326054')
        for i in range(3):
            pine(p, width-i*10+2, 46, 23-i*3, '#183c42', '#326054')
        rectangle(p, '#28483f', 0, 46, width, 3)
        rectangle(p, '#8c8070', 0, 49, width, 4)
        rectangle(p, '#b5a284', 0, 49, width, 1)
        rectangle(p, '#263d3c', 0, 53, width, 3)
        for i in range(width//7):
            x = i*7+(i % 3)
            rectangle(p, '#5c7760', x, 47, 2, 1)
            rectangle(p, '#6d695e', x+2, 51+(i % 2), 2, 1)
        rectangle(p, '#71959b', 0, 0, width, 1)
    finally:
        p.end()
    return image


LETTERS = {
    'E': ('11111','10000','10000','11110','10000','10000','11111'),
    'S': ('01111','10000','10000','01110','00001','00001','11110'),
    'A': ('01110','10001','10001','11111','10001','10001','10001'),
    'Y': ('10001','10001','01010','00100','00100','00100','00100'),
    'O': ('01110','10001','10001','10001','10001','10001','01110'),
    'N': ('10001','11001','11001','10101','10011','10011','10001'),
    '!': ('00100','00100','00100','00100','00100','00000','00100'),
}


def speech(painter, x, width):
    left = max(2, min(width-63, x-30))
    rectangle(painter, '#101b31', left, 2, 62, 16)
    rectangle(painter, '#fff3ce', left+1, 3, 60, 14)
    polygon(painter, '#101b31', [(x-3, 17), (x+3, 17), (x+1, 22)])
    polygon(painter, '#fff3ce', [(x-2, 16), (x+2, 16), (x+1, 20)])
    for i, letter in enumerate('ESSAYONS!'):
        for row, pixels in enumerate(LETTERS[letter]):
            for col, pixel in enumerate(pixels):
                if pixel == '1':
                    rectangle(painter, '#25334a', left+5+i*6+col, 6+row, 1, 1)


def draw_staff(p, x=12):
    rectangle(p, '#101b31', x-1, -28, 3, 28)
    rectangle(p, '#b67c43', x, -27, 1, 27)
    rectangle(p, '#efc479', x-2, -28, 5, 2)
    polygon(p, '#142039', [(x, -33), (x+3, -30), (x, -26), (x-3, -30)])
    polygon(p, '#58d7e3', [(x, -32), (x+2, -30), (x, -27), (x-2, -30)])
    rectangle(p, '#dcfff0', x, -31, 1, 3)


def draw_wizard(p, motion, width):
    active = motion.trick if motion.phase == 'trick' else None
    t = motion.trick_fraction if active else 0.
    x = motion.x(width)
    foot = GROUND-round(8*math.sin(math.pi*t)) if active == 'hop' else GROUND
    stride = (0, 2, 0, -2)[int(motion.elapsed*8) % 4] if not active else 0
    spread = active == 'hop' and .18 < t < .85
    rectangle(p, '#414d47', x-9, GROUND, 19, 2)
    p.save()
    try:
        p.translate(x, foot)
        p.scale(motion.direction, 1)
        if active == 'stumble':
            # A forward lurch, flailing arms, then a small backward recovery.
            p.rotate(27*math.sin(2*math.pi*t)*math.sin(math.pi*t))
        if active == 'breakdance':
            p.translate(0, -round(abs(math.sin(t*math.pi*4))))

        # Boots/legs are behind the robe; the hop opens the arms and both legs.
        if spread:
            line(p, '#142039', [(-3,-5),(-10,-1),(-14,-3)], 4)
            line(p, '#142039', [(3,-5),(10,-1),(14,-3)], 4)
            line(p, '#a58bd5', [(-3,-5),(-10,-1)], 2)
            line(p, '#a58bd5', [(3,-5),(10,-1)], 2)
        else:
            rectangle(p, '#111a30', -7-stride, -3, 6, 4)
            rectangle(p, '#111a30', 2+stride, -3, 7, 4)
            rectangle(p, '#8e7366', -6-stride, -2, 4, 1)
            rectangle(p, '#8e7366', 3+stride, -2, 4, 1)

        if active == 'breakdance':
            wave = int(t*8) % 4
            arms = (
                ([(-5,-14),(-11,-19),(-17,-19)], [(5,-14),(11,-10),(17,-10)]),
                ([(-5,-14),(-11,-10),(-17,-16)], [(5,-14),(11,-19),(17,-19)]),
                ([(-5,-14),(-11,-9),(-17,-9)], [(5,-14),(11,-19),(17,-13)]),
                ([(-5,-14),(-11,-19),(-17,-13)], [(5,-14),(11,-9),(17,-15)]),
            )[wave]
        elif spread:
            arms = ([(-5,-15),(-12,-17),(-18,-17)], [(5,-15),(12,-17),(18,-17)])
        elif active == 'stumble':
            arms = ([(-5,-15),(-10,-23),(-14,-24)], [(5,-15),(11,-20),(16,-23)])
        elif active == 'lightning':
            arms = ([(-5,-15),(-10,-21),(-8,-27)], [(5,-15),(10,-20),(12,-24)])
        elif active == 'pipe':
            arms = ([(-5,-15),(-8,-9),(-3,-10)], [(5,-15),(10,-13),(9,-20)])
        else:
            arms = ([(-5,-15),(-9,-9-stride),(-7,-7-stride)], [(5,-15),(10,-12),(12,-17)])
        for points in arms:
            line(p, '#111a30', points, 5)
            line(p, '#8266c1', points, 3)
            hand = points[-1]
            rectangle(p, '#f5c28a', hand[0]-1, hand[1]-1, 3, 3)

        polygon(p, '#111a30', [(-6,-17),(5,-17),(9,-4),(6,-2),(-9,-2)])
        polygon(p, '#6351ad', [(-5,-16),(4,-16),(7,-4),(-7,-4)])
        polygon(p, '#8e72d0', [(-5,-15),(-2,-15),(-3,-4),(-7,-4)])
        rectangle(p, '#453c88', 3, -13, 2, 9)
        rectangle(p, '#d7b878', -3, -8, 3, 1)
        rectangle(p, '#d7b878', -2, -9, 1, 3)
        # Face, profile nose, eye and a long silver beard.
        rectangle(p, '#111a30', -5, -24, 12, 10)
        rectangle(p, '#efbd88', -3, -23, 9, 8)
        rectangle(p, '#ffdb9c', 5, -20, 3, 3)
        rectangle(p, '#252540', 4, -22, 1, 2)
        rectangle(p, '#f6f2d5', 3, -22, 1, 1)
        polygon(p, '#7c8da4', [(-5,-19),(-1,-17),(6,-18),(6,-12),(1,-7),(-4,-12)])
        polygon(p, '#d2dcd6', [(-4,-19),(0,-17),(5,-18),(5,-13),(1,-9),(-2,-12)])
        line(p, '#fcf6de', [(-3,-17),(-2,-13),(0,-11)], 1)
        rectangle(p, '#fff0c8', 2, -18, 4, 1)
        # Bent pointed hat, brim, gold buckle and a tiny embroidered star.
        polygon(p, '#111a30', [(-10,-23),(-6,-25),(-5,-29),(0,-34),(4,-32),
                              (3,-30),(6,-25),(9,-24),(9,-22),(-10,-22)])
        polygon(p, '#6453ad', [(-8,-24),(-5,-25),(-4,-29),(0,-33),(3,-32),
                              (1,-30),(5,-25),(7,-24)])
        polygon(p, '#9376d2', [(-5,-25),(-4,-29),(0,-33),(1,-32),(-2,-28),(-2,-25)])
        rectangle(p, '#b18cdb', -9, -24, 18, 1)
        rectangle(p, '#3b3b78', -5, -26, 10, 2)
        rectangle(p, '#e9c778', 1, -26, 2, 2)
        rectangle(p, '#f5dfa0', -1, -30, 1, 1)
        staff_x = -15 if active == 'pipe' else (20 if active == 'breakdance' or spread else 12)
        draw_staff(p, staff_x)

        if active == 'flame' and .1 < t < .95:
            length = round(44*min(1., (t-.1)*6, (.95-t)*8))
            flicker = int(t*24) % 3
            polygon(p, '#df4e37', [(13,-30),(19,-35),(25,-32),(31,-36+flicker),
                                  (13+length,-30),(32,-27),(26,-23),(19,-27)])
            polygon(p, '#ffa443', [(13,-30),(21,-33),(27,-30),(37,-33),
                                  (12+length,-30),(29,-27),(22,-29),(18,-27)])
            polygon(p, '#fff0a7', [(13,-30),(21,-31),(30,-30),(22,-29)])
            rectangle(p, '#ffd06b', 19+length, -34+flicker, 2, 1)
            rectangle(p, '#f18142', 23+length, -28-flicker, 1, 1)
        elif active == 'pipe':
            line(p, '#382f37', [(7,-19),(12,-19),(13,-16),(16,-16)], 3)
            rectangle(p, '#b4784b', 13, -20, 5, 5)
            rectangle(p, '#fba568', 14, -21, 3, 2)
            if t > .36:
                smoke = (t-.36)/.64
                radius = round(2+4*smoke)
                cx, cy = round(18+16*smoke), round(-23-13*smoke)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor('#e0e4d8' if smoke < .8 else '#9bb3b9'), 1))
                p.drawEllipse(QRect(cx-radius, cy-radius, radius*2, radius*2))
                if smoke < .4:
                    rectangle(p, '#b4c8c6', 18, -23, 1, 2)
        elif active == 'lightning':
            spark = int(t*10) % 3
            line(p, '#92f4ed', [(9,-35-spark),(12,-37-spark),(16,-35-spark)], 1)
            if .25 < t < .88:
                bolt = [(43,-48),(38,-38),(45,-38),(36,-27),(43,-27),(34,-9)]
                line(p, '#55aadd', bolt, 3)
                line(p, '#fff9ce', bolt, 1)
                line(p, '#baf3eb', [(39,-32),(49,-29),(47,-22)], 1)
                rectangle(p, '#baf3eb', 32, -7, 4, 1)
        elif active == 'stumble' and .15 < t < .8:
            rectangle(p, '#e9d3a0', -14, -1, 3, 1)
            rectangle(p, '#af9e81', -18, -3, 2, 1)
    finally:
        p.restore()
    if active == 'speech':
        speech(p, x, width)
