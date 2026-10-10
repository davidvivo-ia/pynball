#!/usr/bin/env python3
"""
pynball — un pinball completo en un solo fichero.

Python 3.12+, pygame-ce. Sin motor de física externo, sin assets.
La física está escrita aquí, legible de arriba abajo:

    0. Constantes de afinado
    1. Vec2 y utilidades
    2. Geometría de colisión (funciones puras sobre floats)
    3. Elementos de mesa
    4. Ball
    5. Table y build_table()
    6. Física: step()
    7. Sonido sintetizado
    8. Reglas y máquina de estados (Game)
    9. Render
   10. main()

Lo que se toma prestado y de dónde:
  - SpaceCadetPinball (descompilación MIT): flipper como cápsula rotatoria,
    "boost" proporcional a la distancia al pivote, corrección posicional
    antes de reflejar, tilt por acumulación de nudges con decaimiento.
  - winball: velocidad de superficie del flipper (omega x r) restada a la
    velocidad relativa de la bola. Escala: un fichero.
  - flipper-zero-pinball: contacto con 'surface' y 'bounce' por elemento,
    nudge_heat, combo.
  - BBC BASIC pinball: sonido por envolventes sintetizadas, no samples.

Controles: Shift izq / Z / flecha izq = flipper izquierdo; Shift der / "/" /
flecha der = flipper derecho; Espacio = plunger (mantener y soltar); X C V =
nudge; R = nueva partida; M = música; F1 = debug; P = pausa; Esc = salir.

Flags: --headless [N] simula N frames sin ventana; --dump-sounds DIR vuelca
todos los sonidos sintetizados a WAV; --mute arranca sin audio.
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

# =============================================================================
# 0. CONSTANTES DE AFINADO
# =============================================================================

W, H = 540, 960                 # lienzo lógico
HUD_H = 64                      # banda superior

FIXED_DT = 1.0 / 120.0          # física a 120 Hz
SUBSTEPS = 8                    # 8 subpasos -> 960 Hz efectivos
MAX_FRAME_DT = 0.05             # evita la espiral de la muerte al perder foco

GRAVITY = 1150.0                # px/s²          [800 .. 1600]
BALL_RADIUS = 9.0               # px
BALL_MAX_SPEED = 2400.0         # px/s           limitar evita túneles
AIR_DAMPING = 0.08              # 1/s            fricción con el aire/cristal

WALL_E = 0.55                   # restitución    [0.3 .. 0.8]
WALL_MU = 0.03                  # fricción tangencial en impacto
ROLL_MU = 0.004                 # fricción tangencial en contacto de rodadura
REST_VN = 70.0                  # px/s  por debajo de esto no hay rebote, hay apoyo
SKIN = 0.05                     # px    epsilon de separación tras corregir

BUMPER_KICK = 650.0             # px/s
BUMPER_E = 0.3
SLING_KICK = 520.0              # px/s
SLING_E = 0.4
SLING_THRESHOLD = 90.0          # px/s  velocidad normal mínima para disparar

FLIPPER_LEN = 85.0              # px
FLIPPER_R_BASE = 10.0           # px
FLIPPER_R_TIP = 7.0             # px
FLIPPER_OMEGA_UP = 26.0         # rad/s          [20 .. 40]
FLIPPER_OMEGA_DOWN = 16.0       # rad/s
FLIPPER_E = 0.35
FLIPPER_MU = 0.05
FLIPPER_REST = math.radians(30)     # ángulo en reposo respecto a la horizontal
FLIPPER_UP = math.radians(-28)

PLUNGER_MIN = 500.0             # px/s  con un toque
PLUNGER_MAX = 1650.0            # px/s  a carga completa
PLUNGER_CHARGE_S = 1.0          # s     para carga completa

BALL_SAVE_S = 6.0
TILT_HEAT_PER_NUDGE = 1.0
TILT_HEAT_DECAY = 0.55          # /s
TILT_WARN = 1.6
TILT_LIMIT = 2.5
NUDGE_VX = 170.0                # px/s  impulso lateral de un golpe
NUDGE_VY = -60.0                # px/s  todo golpe levanta un poco la bola
NUDGE_UP_VY = 650.0             # px/s  extra del golpe desde abajo (bang back); cuesta 1,6 de calor

BALLS_PER_GAME = 3
EXTRA_BALL_SCORE = 50_000
SKILL_SHOT_S = 4.0
STUCK_S = 2.5

HISCORE_FILE = Path.home() / ".pynball_hiscore"

# Paleta
C_BG = (14, 12, 22)
C_FIELD = (26, 22, 40)
C_WALL = (120, 112, 160)
C_WALL_DIM = (70, 64, 96)
C_BALL = (235, 235, 245)
C_BALL_SHADE = (150, 150, 170)
C_FLIPPER = (255, 196, 64)
C_FLIPPER_DIM = (150, 110, 40)
C_BUMPER = (255, 80, 110)
C_BUMPER_LIT = (255, 220, 230)
C_SLING = (90, 200, 255)
C_SLING_LIT = (220, 245, 255)
C_TARGET = (120, 240, 140)
C_TARGET_DIM = (40, 80, 50)
C_LIGHT_ON = (255, 240, 120)
C_LIGHT_OFF = (80, 72, 40)
C_TEXT = (240, 236, 255)
C_TEXT_DIM = (140, 134, 170)
C_DEBUG = (0, 255, 0)
C_DEBUG_N = (255, 255, 0)
C_DEBUG_V = (255, 60, 60)


# =============================================================================
# 1. VEC2 Y UTILIDADES
# =============================================================================

@dataclass(slots=True, frozen=True)
class Vec2:
    """Vector 2D inmutable. Se usa al construir la mesa y al dibujar; la
    física caliente trabaja con floats sueltos para no asignar objetos."""
    x: float
    y: float

    def __add__(self, o: Vec2) -> Vec2:
        return Vec2(self.x + o.x, self.y + o.y)

    def __sub__(self, o: Vec2) -> Vec2:
        return Vec2(self.x - o.x, self.y - o.y)

    def __mul__(self, k: float) -> Vec2:
        return Vec2(self.x * k, self.y * k)

    __rmul__ = __mul__

    def dot(self, o: Vec2) -> float:
        return self.x * o.x + self.y * o.y

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def normalized(self) -> Vec2:
        n = self.length()
        return Vec2(self.x / n, self.y / n) if n else Vec2(0.0, 0.0)

    def perp(self) -> Vec2:
        """Perpendicular a la izquierda (en coordenadas de pantalla, y abajo)."""
        return Vec2(self.y, -self.x)

    def rotated(self, a: float) -> Vec2:
        c, s = math.cos(a), math.sin(a)
        return Vec2(self.x * c - self.y * s, self.x * s + self.y * c)

    def tup(self) -> tuple[float, float]:
        return (self.x, self.y)


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = clamp(t, 0.0, 1.0)
    return (int(lerp(c1[0], c2[0], t)), int(lerp(c1[1], c2[1], t)), int(lerp(c1[2], c2[2], t)))


# =============================================================================
# 2. GEOMETRÍA DE COLISIÓN (funciones puras)
#
# Todas devuelven None si no hay contacto, o (nx, ny, penetración) con la
# normal apuntando desde el sólido hacia la bola.
# =============================================================================

def circle_vs_segment(px: float, py: float, r: float,
                      ax: float, ay: float, bx: float, by: float,
                      lnx: float, lny: float) -> tuple[float, float, float] | None:
    """Bola (px,py,r) contra segmento A-B. (lnx,lny) es la normal izquierda
    del segmento, usada como fallback si la bola está exactamente encima."""
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0.0:
        t = 0.0
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / l2
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    qx, qy = ax + dx * t, ay + dy * t
    ex, ey = px - qx, py - qy
    d2 = ex * ex + ey * ey
    if d2 >= r * r:
        return None
    d = math.sqrt(d2)
    if d < 1e-6:
        return (lnx, lny, r)
    return (ex / d, ey / d, r - d)


def circle_vs_circle(px: float, py: float, r: float,
                     cx: float, cy: float, cr: float) -> tuple[float, float, float] | None:
    ex, ey = px - cx, py - cy
    total = r + cr
    d2 = ex * ex + ey * ey
    if d2 >= total * total:
        return None
    d = math.sqrt(d2)
    if d < 1e-6:
        return (0.0, -1.0, total)
    return (ex / d, ey / d, total - d)


def circle_vs_arc_inside(px: float, py: float, r: float,
                         cx: float, cy: float, R: float) -> tuple[float, float, float] | None:
    """Bola dentro de un círculo de radio R: la pared es la cara interior.
    Normal hacia el centro."""
    ex, ey = px - cx, py - cy
    d = math.sqrt(ex * ex + ey * ey)
    limit = R - r
    if d <= limit:
        return None
    if d < 1e-6:
        return None
    return (-ex / d, -ey / d, d - limit)


# =============================================================================
# 3. ELEMENTOS DE MESA
# =============================================================================

@dataclass(slots=True)
class Wall:
    a: Vec2
    b: Vec2
    e: float = WALL_E
    mu: float = WALL_MU
    kick: float = 0.0               # impulso extra en la normal (slingshots)
    threshold: float = 0.0          # vn mínimo para disparar on_hit
    one_way: Vec2 | None = None     # si v·one_way > 0 la bola atraviesa
    name: str = ""                  # "sling", "target", "standup", "lane_divider"...
    label: str = ""                 # letra del standup
    active: bool = True
    lit: bool = False
    flash: float = 0.0              # segundos restantes de destello
    # cache
    nx: float = 0.0
    ny: float = 0.0
    xmin: float = 0.0
    xmax: float = 0.0
    ymin: float = 0.0
    ymax: float = 0.0

    def __post_init__(self) -> None:
        n = (self.b - self.a).perp().normalized()
        self.nx, self.ny = n.x, n.y
        self.xmin, self.xmax = min(self.a.x, self.b.x), max(self.a.x, self.b.x)
        self.ymin, self.ymax = min(self.a.y, self.b.y), max(self.a.y, self.b.y)


@dataclass(slots=True)
class Post:
    c: Vec2
    r: float
    e: float = WALL_E
    mu: float = WALL_MU
    kick: float = 0.0
    threshold: float = 0.0
    name: str = ""
    active: bool = True
    lit: bool = False
    flash: float = 0.0
    hits: int = 0


@dataclass(slots=True)
class Arc:
    """Cara interior de un círculo, válida solo en la mitad superior
    (y <= c.y). Por debajo toman el relevo las paredes rectas."""
    c: Vec2
    R: float
    e: float = WALL_E
    mu: float = WALL_MU
    name: str = "arc"
    active: bool = True


@dataclass(slots=True)
class Sensor:
    """Segmento que puntúa al cruzarlo, sin colisión física."""
    a: Vec2
    b: Vec2
    name: str = ""
    index: int = 0
    lit: bool = False
    inside: bool = False
    flash: float = 0.0


@dataclass(slots=True)
class Hole:
    """Agujero kickout: captura la bola si pasa despacio por el centro,
    la retiene un instante y la expulsa."""
    c: Vec2
    r: float = 15.0
    eject: Vec2 = field(default_factory=lambda: Vec2(120.0, 360.0))   # hacia el inlane izquierdo
    name: str = "hole"
    holding: bool = False
    cooldown: float = 0.0           # s sin capturar tras expulsar
    flash: float = 0.0
    visits: int = 0


@dataclass(slots=True)
class Flipper:
    pivot: Vec2
    rest: float                     # ángulo en reposo (rad)
    up: float                       # ángulo arriba (rad)
    length: float = FLIPPER_LEN
    r_base: float = FLIPPER_R_BASE
    r_tip: float = FLIPPER_R_TIP
    angle: float = 0.0
    omega: float = 0.0              # velocidad angular real del último subpaso
    pressed: bool = False
    name: str = "flipper"
    active: bool = True

    def __post_init__(self) -> None:
        self.angle = self.rest

    def tip(self) -> Vec2:
        return self.pivot + Vec2(math.cos(self.angle), math.sin(self.angle)) * self.length

    def step(self, dt: float, enabled: bool) -> None:
        target = self.up if (self.pressed and enabled) else self.rest
        speed = FLIPPER_OMEGA_UP if (self.pressed and enabled) else FLIPPER_OMEGA_DOWN
        prev = self.angle
        delta = clamp(target - self.angle, -speed * dt, speed * dt)
        self.angle += delta
        self.omega = (self.angle - prev) / dt if dt > 0 else 0.0

    def collide(self, px: float, py: float, r: float) -> tuple[float, float, float, float, float] | None:
        """Devuelve (nx, ny, penetración, svx, svy): normal y velocidad de la
        superficie del flipper en el punto de contacto (omega × radio)."""
        ax, ay = self.pivot.x, self.pivot.y
        c, s = math.cos(self.angle), math.sin(self.angle)
        bx, by = ax + c * self.length, ay + s * self.length
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = ((px - ax) * dx + (py - ay) * dy) / l2
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        qx, qy = ax + dx * t, ay + dy * t
        rad = self.r_base + (self.r_tip - self.r_base) * t   # cápsula cónica
        ex, ey = px - qx, py - qy
        d2 = ex * ex + ey * ey
        total = r + rad
        if d2 >= total * total:
            return None
        d = math.sqrt(d2)
        if d < 1e-6:
            nx, ny = -s, c   # normal "hacia arriba" del segmento
            if ny > 0:
                nx, ny = -nx, -ny
        else:
            nx, ny = ex / d, ey / d
        # Punto de contacto en la superficie del flipper y su velocidad
        cxp, cyp = qx + nx * rad, qy + ny * rad
        rx, ry = cxp - ax, cyp - ay
        svx, svy = -self.omega * ry, self.omega * rx
        return (nx, ny, total - d, svx, svy)


# =============================================================================
# 4. BALL
# =============================================================================

@dataclass(slots=True)
class Ball:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    r: float = BALL_RADIUS
    alive: bool = True
    held: bool = False              # retenida en un agujero
    still_time: float = 0.0
    trail: list[tuple[float, float]] = field(default_factory=list)

    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    def in_lane(self) -> bool:
        return self.x > LANE_X

    def bounce(self, nx: float, ny: float, pen: float, e: float, mu: float,
               kick: float = 0.0, svx: float = 0.0, svy: float = 0.0) -> float:
        """Resuelve un contacto. Devuelve la velocidad normal de impacto
        (positiva si hubo golpe). (svx, svy) es la velocidad de la superficie."""
        # 1. Corrección posicional
        self.x += nx * (pen + SKIN)
        self.y += ny * (pen + SKIN)
        # 2. Velocidad relativa a la superficie
        rvx, rvy = self.vx - svx, self.vy - svy
        vn = rvx * nx + rvy * ny
        if vn >= 0.0:
            return 0.0                   # ya se separa
        vtx, vty = rvx - nx * vn, rvy - ny * vn
        impact = -vn
        if impact < REST_VN and kick == 0.0 and svx == 0.0 and svy == 0.0:
            # Contacto de apoyo: sin rebote, rodadura con fricción mínima
            self.vx = vtx * (1.0 - ROLL_MU)
            self.vy = vty * (1.0 - ROLL_MU)
            return 0.0
        # Impacto: refleja la normal con restitución, frena la tangencial
        nvx = vtx * (1.0 - mu) - nx * vn * e + nx * kick
        nvy = vty * (1.0 - mu) - ny * vn * e + ny * kick
        self.vx, self.vy = nvx + svx, nvy + svy
        return impact


# =============================================================================
# 5. TABLE Y MÁQUINAS
#
# Todas las máquinas comparten el bastidor (arco, carril, flippers,
# slingshots, inlanes, outlanes, huecos laterales) y difieren en lo que
# llevan dentro: carriles superiores, bumpers, agujeros, bancos de dianas,
# spinner, kickbacks, lámparas, paleta y música.
# =============================================================================

LANE_X = 474.0                  # pared del carril de lanzamiento
LANE_FLOOR_Y = 950.0
PLUNGER_X = 497.0
DRAIN_Y = H + 20
ARC_C = Vec2(270.0, 320.0)
ARC_R = 250.0
PF_MID = 247.0                  # eje de simetría del campo de juego (20..474)


def mirror(p: Vec2) -> Vec2:
    return Vec2(2 * PF_MID - p.x, p.y)


def arc_y(x: float) -> float:
    return ARC_C.y - math.sqrt(ARC_R * ARC_R - (x - ARC_C.x) ** 2)


@dataclass(slots=True)
class Lamp:
    """Lámpara de inserto: solo luz, sin física. `group` la ata a un patrón."""
    c: Vec2
    group: str
    r: float = 5.0
    color: tuple[int, int, int] | None = None
    on: bool = False
    phase: float = 0.0


@dataclass(slots=True)
class Spinner:
    """Veleta que gira al atravesarla; puntúa cada media vuelta."""
    a: Vec2
    b: Vec2
    angle: float = 0.0
    omega: float = 0.0
    inside: bool = False
    name: str = "spinner"
    spins: int = 0
    flash: float = 0.0


@dataclass(slots=True)
class Kickback:
    """Resorte en el outlane: si está encendido, devuelve la bola al juego."""
    x: float
    y: float
    lit: bool = False
    flash: float = 0.0
    name: str = "kickback"


@dataclass(slots=True)
class Table:
    walls: list[Wall] = field(default_factory=list)
    posts: list[Post] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    sensors: list[Sensor] = field(default_factory=list)
    flippers: list[Flipper] = field(default_factory=list)
    bumpers: list[Post] = field(default_factory=list)
    slings: list[Wall] = field(default_factory=list)
    drop_targets: list[Wall] = field(default_factory=list)
    standups: list[Wall] = field(default_factory=list)
    rollovers: list[Sensor] = field(default_factory=list)
    holes: list[Hole] = field(default_factory=list)
    lamps: list[Lamp] = field(default_factory=list)
    spinners: list[Spinner] = field(default_factory=list)
    kickbacks: list[Kickback] = field(default_factory=list)
    sling_polys: list[list[Vec2]] = field(default_factory=list)
    letters: str = ""               # letras de los standups, en orden

    def polyline(self, pts: list[Vec2], **kw) -> list[Wall]:
        out = [Wall(pts[i], pts[i + 1], **kw) for i in range(len(pts) - 1)]
        self.walls.extend(out)
        return out

    def lamp(self, x: float, y: float, group: str, r: float = 5.0, color=None) -> Lamp:
        lp = Lamp(Vec2(x, y), group, r, color, phase=random.random())
        self.lamps.append(lp)
        return lp

    # --- piezas reutilizables ---------------------------------------------
    def frame(self) -> None:
        """Bastidor común: arco, paredes, carril, puerta, diagonales,
        techos, postes, guías, slingshots y flippers."""
        V = Vec2
        self.arcs.append(Arc(ARC_C, ARC_R))
        self.polyline([V(20, ARC_C.y), V(20, DRAIN_Y)])
        self.polyline([V(520, ARC_C.y), V(520, LANE_FLOOR_Y)])
        self.polyline([V(LANE_X, LANE_FLOOR_Y), V(520, LANE_FLOOR_Y)], e=0.2)
        self.polyline([V(LANE_X, 345), V(LANE_X, LANE_FLOOR_Y)])
        self.walls.append(Wall(V(LANE_X, 345), V(515, 305), one_way=V(0, -1), name="gate", e=0.3))
        for m in (False, True):
            M = mirror if m else (lambda p: p)
            # Diagonal de un sentido: desde arriba desvía la bola hacia el
            # banco; desde abajo deja pasar el disparo del kickback o un
            # bang back. Así el outlane está abierto por arriba, como en una
            # máquina real.
            self.walls.append(Wall(M(V(20, 345)), M(V(80, 455)), one_way=V(0, -1), name="side"))
            # Guía del outlane: cierra el hueco tras el banco sin techo
            self.polyline([M(V(66, 640)), M(V(66, 470)), M(V(80, 455))])
            self.polyline([M(V(80, 611)), M(V(80, 640))])
            self.posts.append(Post(M(V(80, 640)), 2.0))
            self.posts.append(Post(M(V(66, 640)), 2.0))
            self.posts.append(Post(M(V(65, 685)), 7.0))
            # raíl de lámparas por el lateral
            for k in range(6):
                self.lamp(M(V(34, 480 + k * 60)).x, 480 + k * 60, "rail", 3.0)
            piv = M(V(152, 850))
            ang = (math.pi - FLIPPER_REST) if m else FLIPPER_REST
            d = V(math.cos(ang), math.sin(ang))
            n = V(d.y, -d.x) if not m else V(-d.y, d.x)
            if n.y > 0:
                n = n * -1
            self.polyline([M(V(65, 690)), M(V(65, 792)), piv + n * FLIPPER_R_BASE], e=0.3)
            A, B, C = M(V(105, 690)), M(V(105, 780)), M(V(150, 790))
            self.sling_polys.append([A, B, C])
            self.polyline([A, B, C], e=0.4)
            face = Wall(C, A, e=SLING_E, kick=SLING_KICK, threshold=SLING_THRESHOLD, name="sling")
            if (face.nx > 0) != (not m):
                face = Wall(A, C, e=SLING_E, kick=SLING_KICK, threshold=SLING_THRESHOLD, name="sling")
            self.walls.append(face)
            self.slings.append(face)
            self.flippers.append(Flipper(piv, ang, (math.pi - FLIPPER_UP) if m else FLIPPER_UP))
            # lámparas de inlane
            for k in range(3):
                self.lamp(M(V(85, 705 + k * 28)).x, 705 + k * 28, "inlane", 3.5)

    def side_bank(self, right: bool, kind: str, letters: str = "") -> None:
        """Rellena el hueco lateral (y 455..611) con tres dianas o standups,
        o con pared lisa si kind == 'wall'."""
        M = mirror if right else (lambda p: p)
        V = Vec2
        if kind == "wall":
            self.polyline([M(V(80, 455)), M(V(80, 611))])
            return
        for i in range(3):
            y0, y1 = 455 + i * 52, 507 + i * 52
            w = Wall(M(V(80, y0)), M(V(80, y1)), e=0.45, threshold=120.0, name=kind,
                     label=letters[i] if i < len(letters) else "")
            self.walls.append(w)
            (self.standups if kind == "standup" else self.drop_targets).append(w)
            # flecha-lámpara que apunta a la diana
            self.lamp(M(V(112, (y0 + y1) / 2)).x, (y0 + y1) / 2, "arrowR" if right else "arrowL", 4.5)
        if kind == "standup":
            self.letters += letters

    def top_lanes(self, n: int) -> None:
        """n carriles con rollover entre n+1 divisores centrados en PF_MID."""
        V = Vec2
        xs = [PF_MID + (i - n / 2) * 56.0 for i in range(n + 1)]
        for x in xs:
            top = arc_y(x) + 2 * BALL_RADIUS + 6
            self.polyline([V(x, top), V(x, 180)], name="lane_divider")
            self.posts.append(Post(V(x, 180), 3.0))
            self.posts.append(Post(V(x, top), 3.0))
        for i in range(n):
            s = Sensor(V(xs[i] + 6, 150), V(xs[i + 1] - 6, 150), name="rollover", index=i)
            self.sensors.append(s)
            self.rollovers.append(s)

    def bumper(self, x: float, y: float, r: float = 24.0) -> Post:
        b = Post(Vec2(x, y), r, e=BUMPER_E, kick=BUMPER_KICK, threshold=40.0, name="bumper")
        self.posts.append(b)
        self.bumpers.append(b)
        return b

    def rubber(self, x: float, y: float, r: float = 7.0) -> Post:
        """Poste de goma: rebote vivo y sonido propio."""
        p = Post(Vec2(x, y), r, e=0.88, mu=0.01, name="rubber")
        self.posts.append(p)
        return p

    def mini_bumper(self, x: float, y: float) -> Post:
        b = self.bumper(x, y, 14.0)
        b.kick = BUMPER_KICK * 0.7
        return b

    def bumper_ring(self, b: Post, n: int = 6) -> None:
        """Anillo de lámparas alrededor de un bumper."""
        for i in range(n):
            a = math.tau * i / n
            self.lamp(b.c.x + math.cos(a) * (b.r + 11), b.c.y + math.sin(a) * (b.r + 11), "bring", 2.5)

    def hole(self, x: float, y: float, eject: Vec2) -> Hole:
        h = Hole(Vec2(x, y), eject=eject)
        self.holes.append(h)
        return h

    def spinner(self, x: float, y: float, half: float = 26.0) -> Spinner:
        s = Spinner(Vec2(x - half, y), Vec2(x + half, y))
        self.spinners.append(s)
        self.posts.append(Post(Vec2(x - half - 4, y), 3.0))
        self.posts.append(Post(Vec2(x + half + 4, y), 3.0))
        return s

    def kickback(self, right: bool) -> Kickback:
        x = (2 * PF_MID - 42.0) if right else 42.0
        k = Kickback(x, 905.0)
        self.kickbacks.append(k)
        self.lamp(x, 870, "kickR" if right else "kickL", 5.0)
        return k

    def ring_lamps(self, n: int = 14) -> None:
        """Corona de lámparas bajo el arco, para persecuciones."""
        for i in range(n):
            a = math.pi + math.pi * (i + 0.5) / n
            self.lamp(ARC_C.x + math.cos(a) * (ARC_R - 16), ARC_C.y + math.sin(a) * (ARC_R - 16), "ring", 4.0)

    def meter_lamps(self, x: float, y0: float, n: int = 5) -> None:
        """Columna de lámparas: medidor de combo."""
        for i in range(n):
            self.lamp(x, y0 - i * 16, "meter", 4.0)


@dataclass(slots=True)
class Machine:
    key: str
    name: str
    subtitle: str
    pal: dict[str, tuple[int, int, int]]
    bpm: float
    chords: list[list[int]]
    arp_wave: str
    build: object                   # Callable[[Table], None]


PAL_CLASSIC = dict(
    field=(26, 22, 40), wall=(120, 112, 160), wall_dim=(70, 64, 96),
    flipper=(255, 196, 64), flipper_dim=(150, 110, 40),
    bumper=(255, 80, 110), bumper_lit=(255, 220, 230),
    sling=(90, 200, 255), sling_lit=(220, 245, 255),
    target=(120, 240, 140), target_dim=(40, 80, 50),
    light_on=(255, 240, 120), light_off=(80, 72, 40), accent=(255, 196, 64),
)
PAL_NEBULA = dict(
    field=(10, 12, 32), wall=(96, 120, 210), wall_dim=(50, 60, 110),
    flipper=(90, 225, 255), flipper_dim=(40, 110, 130),
    bumper=(140, 90, 255), bumper_lit=(230, 215, 255),
    sling=(255, 110, 200), sling_lit=(255, 225, 245),
    target=(150, 255, 220), target_dim=(40, 90, 80),
    light_on=(190, 225, 255), light_off=(45, 55, 95), accent=(255, 110, 200),
)
PAL_TAVERN = dict(
    field=(44, 28, 18), wall=(180, 128, 72), wall_dim=(100, 70, 40),
    flipper=(245, 205, 120), flipper_dim=(130, 100, 55),
    bumper=(225, 95, 40), bumper_lit=(255, 225, 190),
    sling=(200, 60, 45), sling_lit=(255, 200, 190),
    target=(245, 220, 120), target_dim=(95, 80, 45),
    light_on=(255, 205, 90), light_off=(85, 60, 30), accent=(255, 150, 60),
)


def build_classic(t: Table) -> None:
    """La de siempre: tres carriles, tres bumpers, agujero, dianas y P-Y-N."""
    t.top_lanes(3)
    for x, y in ((177, 330), (317, 330), (247, 425)):
        t.bumper_ring(t.bumper(x, y))
    t.hole(105, 262, Vec2(120, 360))
    t.side_bank(False, "target")
    t.side_bank(True, "standup", "PYN")
    t.spinner(108, 400)
    t.kickback(False)
    t.ring_lamps()
    t.meter_lamps(247, 560)
    for x, y in ((177, 372), (317, 372), (247, 470)):
        t.lamp(x, y, "bumper", 4.0)
    for x, y in ((130, 520), (364, 520)):
        t.bumper_ring(t.mini_bumper(x, y), 5)
    for x, y in ((140, 610), (354, 610), (126, 236), (368, 236), (247, 540)):
        t.rubber(x, y)


def build_nebula(t: Table) -> None:
    """Nebulosa: cuatro carriles, rombo de bumpers, dos agujeros, N-E-B-U-L-A."""
    t.top_lanes(4)
    for x, y in ((247, 292), (187, 362), (307, 362), (247, 432)):
        t.bumper_ring(t.bumper(x, y, 22.0))
    t.hole(88, 250, Vec2(140, 340))
    t.hole(406, 250, Vec2(-140, 340))
    for i in range(6):                         # corona de gomas alrededor del rombo
        a = math.tau * i / 6 + math.pi / 6
        t.rubber(247 + math.cos(a) * 118, 362 + math.sin(a) * 118, 6.0)
    for x, y in ((130, 560), (364, 560)):
        t.bumper_ring(t.mini_bumper(x, y), 5)
    t.side_bank(False, "standup", "NEB")
    t.side_bank(True, "standup", "ULA")
    t.spinner(247, 540)
    t.kickback(False)
    t.kickback(True)
    t.ring_lamps(18)
    t.meter_lamps(247, 640)
    for i in range(16):                        # estrellas
        a = random.uniform(0, math.tau)
        rr = random.uniform(60, 190)
        x, y = ARC_C.x + math.cos(a) * rr, ARC_C.y + math.sin(a) * rr
        if y < 240 or 480 < y < 520:
            t.lamp(x, y, "star", 2.5)
    for x, y in ((247, 362), (217, 327), (277, 327), (217, 397), (277, 397)):
        t.lamp(x, y, "bumper", 3.5)


def build_tavern(t: Table) -> None:
    """Taberna: campo de pinchos tipo bagatela, barril, gran bumper, R-O-N."""
    t.top_lanes(3)
    t.hole(247, 262, Vec2(0, 420))
    rows = ((300, 5), (345, 4), (390, 5), (435, 4), (480, 5))
    for y, n in rows:
        for i in range(n):
            x = PF_MID + (i - (n - 1) / 2) * 52.0
            t.posts.append(Post(Vec2(x, y), 5.0, e=0.7, name=""))
    t.bumper_ring(t.bumper(247, 560, 28.0), 8)
    t.side_bank(False, "target")
    t.side_bank(True, "standup", "RON")
    t.spinner(386, 400)
    for x, y in ((120, 560), (374, 560)):
        t.bumper_ring(t.mini_bumper(x, y), 5)
    for x, y in ((140, 640), (354, 640), (128, 250), (366, 250)):
        t.rubber(x, y)
    t.kickback(True)
    t.ring_lamps(10)
    t.meter_lamps(247, 680)
    for i in range(5):
        t.lamp(PF_MID + (i - 2) * 40.0, 215, "sign", 5.0)
    t.lamp(247, 610, "bumper", 4.0)


MACHINES: list[Machine] = [
    Machine("classic", "PYNBALL", "la de siempre", PAL_CLASSIC, 112.0,
            [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]], "tri", build_classic),
    Machine("nebula", "NEBULOSA", "cuatro carriles, dos agujeros, seis letras", PAL_NEBULA, 96.0,
            [[52, 55, 59], [48, 52, 55], [55, 59, 62], [50, 54, 57]], "sine", build_nebula),
    Machine("tavern", "TABERNA", "bagatela de pinchos, barril y ron", PAL_TAVERN, 132.0,
            [[48, 52, 55], [53, 57, 60], [55, 59, 62], [48, 52, 55]], "square", build_tavern),
]


def build_table(machine: Machine) -> Table:
    t = Table()
    t.frame()
    machine.build(t)
    return t


# =============================================================================
# 6. FÍSICA
# =============================================================================

SCORING_WALLS = frozenset({"sling", "target", "standup"})
HIT_MIN = 110.0                 # px/s  velocidad normal mínima para sonar un golpe


class Physics:
    """Avanza la bola un subpaso y resuelve contactos. Las reacciones de
    juego (puntos, luces, sonido) salen por `events`:
    (nombre, objeto, velocidad_de_impacto). Los golpes contra paredes y
    postes sin nombre salen como ("hit", x, impacto)."""

    def __init__(self, table: Table) -> None:
        self.t = table
        self.events: list[tuple[str, object, float]] = []
        self.gravity_scale = 1.0        # < 1 con la máquina "levantada" (trampa)

    def substep(self, ball: Ball, dt: float, flippers_enabled: bool) -> None:
        t = self.t
        for f in t.flippers:
            f.step(dt, flippers_enabled)

        if not ball.alive or ball.held:
            return

        # Integración semi-implícita
        ball.vy += GRAVITY * self.gravity_scale * dt
        damp = 1.0 - AIR_DAMPING * dt
        ball.vx *= damp
        ball.vy *= damp
        sp = ball.speed()
        if sp > BALL_MAX_SPEED:
            k = BALL_MAX_SPEED / sp
            ball.vx *= k
            ball.vy *= k
        ball.x += ball.vx * dt
        ball.y += ball.vy * dt

        px, py, r = ball.x, ball.y, ball.r

        # Arco superior (solo mitad superior)
        for a in t.arcs:
            if py <= a.c.y:
                hit = circle_vs_arc_inside(px, py, r, a.c.x, a.c.y, a.R)
                if hit:
                    impact = ball.bounce(hit[0], hit[1], hit[2], a.e, a.mu)
                    if impact > HIT_MIN:
                        self.events.append(("hit", ball.x, impact))
                    px, py = ball.x, ball.y

        # Paredes
        for w in t.walls:
            if not w.active:
                continue
            if px + r < w.xmin or px - r > w.xmax or py + r < w.ymin or py - r > w.ymax:
                continue
            if w.one_way is not None and (ball.vx * w.one_way.x + ball.vy * w.one_way.y) > 0.0:
                continue
            hit = circle_vs_segment(px, py, r, w.a.x, w.a.y, w.b.x, w.b.y, w.nx, w.ny)
            if hit:
                nx, ny, pen = hit
                # Un slingshot solo dispara por su cara frontal
                front = (nx * w.nx + ny * w.ny) > 0.3
                kick = w.kick if (front and impact_ok(ball, nx, ny, w.threshold)) else 0.0
                impact = ball.bounce(nx, ny, pen, w.e, w.mu, kick)
                if w.name in SCORING_WALLS:
                    if impact > w.threshold:
                        self.events.append((w.name, w, impact))
                elif impact > HIT_MIN:
                    self.events.append(("hit", ball.x, impact))
                px, py = ball.x, ball.y

        # Postes y bumpers
        for p in t.posts:
            if not p.active:
                continue
            if abs(px - p.c.x) > r + p.r or abs(py - p.c.y) > r + p.r:
                continue
            hit = circle_vs_circle(px, py, r, p.c.x, p.c.y, p.r)
            if hit:
                nx, ny, pen = hit
                kick = p.kick if impact_ok(ball, nx, ny, p.threshold) else 0.0
                impact = ball.bounce(nx, ny, pen, p.e, p.mu, kick)
                if p.name == "bumper":
                    if impact > p.threshold or kick > 0:
                        self.events.append((p.name, p, max(impact, 1.0)))
                elif p.name == "rubber":
                    if impact > HIT_MIN:
                        self.events.append(("rubber", p, impact))
                elif impact > HIT_MIN:
                    self.events.append(("hit", ball.x, impact))
                px, py = ball.x, ball.y

        # Flippers
        for f in t.flippers:
            hit = f.collide(px, py, r)
            if hit:
                nx, ny, pen, svx, svy = hit
                impact = ball.bounce(nx, ny, pen, FLIPPER_E, FLIPPER_MU, 0.0, svx, svy)
                if impact > 250.0:
                    self.events.append(("flipper_hit", f, impact))
                px, py = ball.x, ball.y

        # Agujeros: capturan si la bola pasa despacio por el centro
        for h in t.holes:
            if h.cooldown > 0.0:
                h.cooldown -= dt
            elif not h.holding and abs(px - h.c.x) < 7.0 and abs(py - h.c.y) < 7.0 and ball.speed() < 520.0:
                ball.x, ball.y, ball.vx, ball.vy = h.c.x, h.c.y, 0.0, 0.0
                ball.held = True
                h.holding = True
                self.events.append(("hole", h, 0.0))

        # Tras los golpes: límite duro de velocidad
        sp = ball.speed()
        if sp > BALL_MAX_SPEED:
            k = BALL_MAX_SPEED / sp
            ball.vx *= k
            ball.vy *= k

        # Sensores (sin física)
        for s in t.sensors:
            inside = circle_vs_segment(px, py, r + 2.0, s.a.x, s.a.y, s.b.x, s.b.y, 0.0, -1.0) is not None
            if inside and not s.inside:
                self.events.append((s.name, s, 0.0))
            s.inside = inside

        # Spinners: al cruzarlos giran según la velocidad; cada media vuelta puntúa
        for sp in t.spinners:
            inside = circle_vs_segment(px, py, r, sp.a.x, sp.a.y, sp.b.x, sp.b.y, 0.0, -1.0) is not None
            if inside and not sp.inside:
                sp.omega = math.copysign(max(abs(ball.vy), 200.0) * 0.045, ball.vy)
                ball.vy *= 0.85                       # la veleta frena un poco
            sp.inside = inside
            if sp.omega:
                before = int(sp.angle / math.pi)
                sp.angle += sp.omega * dt
                sp.omega *= (1.0 - 1.6 * dt)
                if abs(sp.omega) < 0.3:
                    sp.omega = 0.0
                if int(sp.angle / math.pi) != before:
                    sp.spins += 1
                    self.events.append(("spinner", sp, 0.0))

        # Kickbacks: en el outlane, si está encendido, devuelve la bola
        for k in t.kickbacks:
            if k.lit and abs(px - k.x) < 24.0 and py > k.y - 10.0 and ball.vy > 0.0:
                k.lit = False
                k.flash = 0.4
                ball.vx = 0.0
                ball.vy = -1500.0
                ball.y = k.y - 12.0
                self.events.append(("kickback", k, 0.0))

        # Desagüe
        if ball.y > DRAIN_Y:
            ball.alive = False
            self.events.append(("drain", None, 0.0))


def impact_ok(ball: Ball, nx: float, ny: float, threshold: float) -> bool:
    """¿La bola se acerca con suficiente velocidad normal para disparar un kick?"""
    return -(ball.vx * nx + ball.vy * ny) > threshold


# =============================================================================
# 7. SONIDO
#
# Todo el audio se sintetiza al arrancar con numpy. Nada de samples.
#
# Modelo: un pinball suena a MECÁNICA más ELECTRÓNICA.
#   - Mecánica: solenoides (ráfaga de ruido filtrada + golpe grave), bola de
#     acero contra metal (parciales inarmónicos que decaen: síntesis modal),
#     bola rodando (ruido marrón en bucle cuyo volumen sigue la velocidad).
#   - Electrónica: campanas de las dianas y carriles en pentatónica, el
#     bumper sube de nota con el combo, jingles de inicio, extra ball y
#     game over, y un bucle musical opcional (tecla M).
#
# Cada golpe tiene tres variantes con ligera desafinación para no cansar, se
# panea en estéreo según la x del elemento, y su volumen sigue la velocidad
# de impacto. Si numpy o el mixer fallan, todo es silencioso y el juego sigue.
# =============================================================================

MUSIC_ON_START = True
MASTER_VOL = 0.8
MUSIC_VOL = 0.22
ROLL_VOL = 0.5

PENTA = [69, 72, 74, 76, 79, 81, 84, 86, 88, 91]    # La menor pentatónica (MIDI)


def midi_hz(n: float) -> float:
    return 440.0 * 2.0 ** ((n - 69) / 12.0)


class Synth:
    """Funciones puras de síntesis sobre arrays numpy (float64, ~[-1, 1])."""

    def __init__(self, np, rate: int) -> None:
        self.np = np
        self.rate = rate

    # --- bloques básicos ---------------------------------------------------
    def t(self, dur: float):
        n = max(1, int(self.rate * dur))
        return self.np.arange(n) / self.rate

    def osc(self, dur: float, f0: float, f1: float | None = None, wave: str = "sine", sweep: str = "exp"):
        np = self.np
        tt = self.t(dur)
        f1 = f0 if f1 is None else f1
        if sweep == "exp" and f0 > 0 and f1 > 0:
            freq = f0 * (f1 / f0) ** (tt / max(dur, 1e-6))
        else:
            freq = f0 + (f1 - f0) * tt / max(dur, 1e-6)
        ph = 2.0 * np.pi * np.cumsum(freq) / self.rate
        frac = (ph / (2.0 * np.pi)) % 1.0
        match wave:
            case "square":
                return np.sign(np.sin(ph))
            case "tri":
                return 2.0 * np.abs(2.0 * frac - 1.0) - 1.0
            case "saw":
                return 2.0 * frac - 1.0
            case _:
                return np.sin(ph)

    def noise(self, dur: float):
        return self.np.random.uniform(-1.0, 1.0, max(1, int(self.rate * dur)))

    def env(self, dur: float, attack: float = 0.002, decay: float = 0.1, curve: float = 1.0):
        """Ataque lineal y caída exponencial con constante `decay` (s)."""
        np = self.np
        tt = self.t(dur)
        a = np.minimum(tt / max(attack, 1e-5), 1.0)
        d = np.exp(-tt / max(decay, 1e-5)) ** curve
        return a * d

    def adsr(self, dur: float, a: float, d: float, s: float, r: float):
        np = self.np
        n = max(1, int(self.rate * dur))
        out = np.ones(n) * s
        na, nd, nr = int(a * self.rate), int(d * self.rate), int(r * self.rate)
        if na:
            out[:na] = np.linspace(0, 1, na)
        if nd:
            out[na:na + nd] = np.linspace(1, s, nd)[: max(0, n - na)]
        if nr and nr < n:
            out[n - nr:] *= np.linspace(1, 0, nr)
        return out

    def band(self, x, lo: float, hi: float):
        """Filtro paso-banda por FFT con bordes suaves (medio octava)."""
        np = self.np
        n = len(x)
        X = np.fft.rfft(x)
        f = np.fft.rfftfreq(n, 1.0 / self.rate)
        f = np.maximum(f, 1.0)
        lf = np.log2(f)
        mask = 1.0 / (1.0 + np.exp(-(lf - np.log2(lo)) * 6.0)) * (1.0 - 1.0 / (1.0 + np.exp(-(lf - np.log2(hi)) * 6.0)))
        return np.fft.irfft(X * mask, n)

    def modal(self, dur: float, f0: float, partials: list[tuple[float, float, float]]):
        """Síntesis modal: suma de parciales (ratio, amplitud, decay_s).
        Ratios inarmónicos dan metal; armónicos dan madera o cuerda."""
        np = self.np
        tt = self.t(dur)
        out = np.zeros_like(tt)
        for ratio, amp, dec in partials:
            out += amp * np.sin(2.0 * np.pi * f0 * ratio * tt) * np.exp(-tt / dec)
        return out

    def mix(self, *parts, at: list[float] | None = None):
        """Superpone señales; `at` da el desplazamiento en segundos de cada una."""
        np = self.np
        offs = [int(a * self.rate) for a in (at or [0.0] * len(parts))]
        n = max(o + len(p) for o, p in zip(offs, parts))
        out = np.zeros(n)
        for o, p in zip(offs, parts):
            out[o:o + len(p)] += p
        return out

    def norm(self, x, peak: float = 0.9):
        np = self.np
        m = np.max(np.abs(x)) or 1.0
        return np.tanh(x / m * 1.2) * peak      # soft clip + normalización

    def fade(self, x, ms: float = 4.0):
        """Rampa breve al final para evitar clicks."""
        np = self.np
        k = min(len(x), max(1, int(self.rate * ms / 1000)))
        x = x.copy()
        x[-k:] *= np.linspace(1, 0, k)
        return x

    # --- instrumentos ------------------------------------------------------
    BELL = [(1.0, 1.0, 0.35), (2.0, 0.5, 0.25), (2.76, 0.3, 0.18), (5.4, 0.12, 0.08), (8.9, 0.05, 0.04)]
    METAL = [(1.0, 1.0, 0.03), (1.71, 0.6, 0.025), (2.63, 0.4, 0.02), (4.11, 0.2, 0.012)]
    WOOD = [(1.0, 1.0, 0.03), (2.0, 0.4, 0.02), (3.0, 0.2, 0.012)]

    def solenoid(self, thud_hz: float = 90.0, noise_ms: float = 12.0, bright: float = 1.0, vol: float = 1.0):
        """Clack de solenoide: ráfaga de ruido filtrada + golpe grave + click."""
        burst = self.band(self.noise(noise_ms / 1000), 900 * bright, 6000 * bright) * self.env(noise_ms / 1000, 0.0005, 0.004)
        thud = self.osc(0.07, thud_hz, thud_hz * 0.6) * self.env(0.07, 0.001, 0.018)
        click = self.modal(0.02, 2400 * bright, self.METAL) * 0.4
        return self.norm(self.mix(burst, thud, click), 0.9 * vol)

    def bell(self, midi: float, dur: float = 0.5, vol: float = 1.0, partials=None):
        return self.norm(self.modal(dur, midi_hz(midi), partials or self.BELL) * self.env(dur, 0.001, dur / 2.5), 0.9 * vol)

    def chord(self, notes: list[float], dur: float, gap: float = 0.06, vol: float = 1.0, wave: str = "tri"):
        parts = []
        for n in notes:
            tone = self.osc(dur, midi_hz(n), wave=wave) * self.adsr(dur, 0.005, 0.1, 0.5, 0.15)
            tone += self.osc(dur, midi_hz(n) * 2.003, wave="sine") * 0.25 * self.env(dur, 0.002, 0.2)
            parts.append(tone)
        return self.norm(self.mix(*parts, at=[i * gap for i in range(len(parts))]), 0.9 * vol)


class Sounds:
    """Banco de sonidos sintetizados, canal de rodadura y bucle de música.

    play(name, x=None, vol=1.0): reproduce con paneo según x (coordenada de
    mesa) y volumen. update(dt, ball): ajusta la rodadura a la velocidad."""

    RATE = 22050
    CH_ROLL = 0
    CH_MUSIC = 1

    def __init__(self, enabled: bool = True) -> None:
        self.bank: dict[str, list[pygame.mixer.Sound]] = {}
        self.ok = False
        self.music_on = MUSIC_ON_START
        self.roll_level = 0.0
        self.last_tick = -1.0
        self.np = None
        self.synth: Synth | None = None
        if not enabled:
            return
        try:
            import numpy as np
            self.np = np
            self.synth = Synth(np, self.RATE)
            pygame.mixer.quit()
            pygame.mixer.init(self.RATE, -16, 2, 256)
            init = pygame.mixer.get_init()
            if not init:
                return
            self.channels = init[2]
            pygame.mixer.set_num_channels(20)
            pygame.mixer.set_reserved(2)
            for name, variants in self.build_bank().items():
                self.bank[name] = [pygame.sndarray.make_sound(self._to_pcm(v)) for v in variants]
            self.ok = True
            self._start_loops()
        except Exception:
            self.ok = False

    # --- conversión ---------------------------------------------------------
    def _to_pcm(self, x):
        np = self.np
        arr = (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
        if self.channels > 1:
            arr = np.repeat(arr[:, None], self.channels, axis=1)
        return np.ascontiguousarray(arr)

    # --- banco ----------------------------------------------------------------
    def build_bank(self) -> dict[str, list]:
        """Devuelve {nombre: [variantes float]}. Puro numpy: se puede probar
        y volcar a WAV sin mixer."""
        S = self.synth
        assert S is not None
        bank: dict[str, list] = {}

        def var(name: str, fn, n: int = 3) -> None:
            bank[name] = [S.fade(fn(1.0 + random.uniform(-0.06, 0.06))) for _ in range(n)]

        # Mecánica
        var("flipper_up", lambda k: S.solenoid(95 * k, 12, 1.0, 0.8))
        var("flipper_down", lambda k: S.solenoid(70 * k, 8, 0.7, 0.45))
        var("flipper_hit", lambda k: S.norm(S.mix(
            S.solenoid(140 * k, 14, 1.2), S.modal(0.06, 1800 * k, S.METAL) * 0.5, S.osc(0.05, 220 * k, 160 * k) * S.env(0.05, 0.001, 0.02)), 0.95))
        var("tick", lambda k: S.norm(S.mix(S.modal(0.04, 2600 * k, S.METAL), S.band(S.noise(0.006), 1500, 9000) * S.env(0.006, 0.0003, 0.002) * 0.5), 0.6))
        var("thud", lambda k: S.norm(S.mix(S.osc(0.08, 120 * k, 60 * k) * S.env(0.08, 0.001, 0.025), S.band(S.noise(0.02), 200, 1200) * S.env(0.02, 0.0005, 0.006) * 0.5), 0.7))
        var("bumper", lambda k: S.norm(S.mix(
            S.solenoid(110 * k, 10, 0.9), S.osc(0.05, 900 * k, 300 * k, wave="tri") * S.env(0.05, 0.001, 0.015) * 0.8), 0.9))
        var("sling", lambda k: S.norm(S.mix(S.solenoid(150 * k, 9, 1.4, 0.8), S.osc(0.06, 640 * k, 420 * k, wave="tri") * S.env(0.06, 0.001, 0.02) * 0.6), 0.85))
        var("drain", lambda k: S.norm(S.mix(
            S.osc(0.5, 90 * k, 40 * k) * S.env(0.5, 0.002, 0.12), S.band(S.noise(0.25), 120, 900) * S.env(0.25, 0.001, 0.06) * 0.6,
            S.modal(0.3, 400 * k, S.WOOD) * 0.3), 0.9))
        var("ratchet", lambda k: S.norm(S.mix(S.band(S.noise(0.008), 1500, 7000) * S.env(0.008, 0.0003, 0.002), S.modal(0.02, 1400 * k, S.WOOD) * 0.5), 0.5), 3)
        var("launch", lambda k: S.norm(S.mix(
            S.osc(0.22, 140 * k, 760 * k, wave="saw") * S.env(0.22, 0.004, 0.07) * 0.7, S.solenoid(120 * k, 10, 1.0, 0.7),
            S.modal(0.25, 520 * k, [(1.0, 1.0, 0.12), (1.5, 0.5, 0.08), (2.98, 0.3, 0.05)]) * 0.4), 0.9))
        var("hole_in", lambda k: S.norm(S.mix(S.osc(0.3, 700 * k, 110 * k, wave="sine") * S.env(0.3, 0.002, 0.08), S.osc(0.12, 80, 50) * S.env(0.12, 0.001, 0.04) * 0.8), 0.8), 2)
        var("hole_out", lambda k: S.norm(S.mix(S.solenoid(130 * k, 10, 1.1, 0.8), S.osc(0.15, 300 * k, 900 * k, wave="tri") * S.env(0.15, 0.002, 0.05) * 0.5), 0.85), 2)
        var("nudge", lambda k: S.norm(S.mix(S.osc(0.12, 70 * k, 45) * S.env(0.12, 0.002, 0.04), S.band(S.noise(0.05), 100, 700) * S.env(0.05, 0.001, 0.015) * 0.7), 0.8), 2)

        # Campanas (electrónica)
        bank["bumper_note"] = [S.bell(n, 0.35, 0.55, [(1.0, 1.0, 0.18), (2.0, 0.4, 0.12), (3.01, 0.2, 0.08)]) for n in PENTA]
        bank["target"] = [S.bell(76 + d, 0.45, 0.7) for d in (0, 0.03, -0.03)]
        bank["standup"] = [S.bell(79 + d, 0.4, 0.65, [(1.0, 1.0, 0.25), (2.4, 0.5, 0.15), (3.9, 0.25, 0.08)]) for d in (0, 0.04, -0.04)]
        bank["rollover"] = [S.bell(n, 0.3, 0.5, [(1.0, 1.0, 0.15), (2.0, 0.3, 0.1), (4.0, 0.15, 0.05)]) for n in (84, 86, 88)]
        bank["bank_complete"] = [S.chord([69, 76, 81, 88], 0.6, 0.05, 0.8)]
        bank["lanes_complete"] = [S.chord([72, 76, 79, 84], 0.5, 0.07, 0.8)]
        bank["mult"] = [S.chord([69, 74, 79, 86, 91], 0.7, 0.06, 0.85)]
        bank["skill"] = [S.chord([76, 81, 88, 93], 0.8, 0.05, 0.9)]
        bank["bonus_tick"] = [S.bell(n, 0.18, 0.55, [(1.0, 1.0, 0.07), (2.0, 0.4, 0.05), (3.0, 0.2, 0.03)]) for n in PENTA]
        bank["ball_save"] = [S.chord([74, 81], 0.4, 0.09, 0.8, wave="square")]
        bank["warn"] = [S.norm(S.osc(0.18, 220, 170, wave="square") * S.env(0.18, 0.002, 0.07) * (S.osc(0.18, 18, wave="square") * 0.5 + 0.5), 0.6)]
        bank["tilt"] = [S.norm(S.osc(0.9, 110, 95, wave="square") * S.env(0.9, 0.002, 0.5) * (S.osc(0.9, 11, wave="square") * 0.5 + 0.5) * 0.7
                               + S.osc(0.9, 55, 50, wave="saw") * S.env(0.9, 0.002, 0.4) * 0.5, 0.85)]
        bank["extra"] = [S.norm(S.mix(S.chord([69, 73, 76, 81], 0.35, 0.09, 0.9), S.chord([81, 85, 88, 93], 0.7, 0.0, 0.8)), 0.9)]
        bank["start"] = [S.norm(S.mix(S.chord([57, 64, 69], 0.5, 0.0, 0.7, wave="square"), S.chord([69, 72, 76, 81, 84], 0.9, 0.08, 0.9)), 0.9)]
        bank["gameover"] = [S.norm(S.mix(S.chord([76, 72, 69, 64], 0.45, 0.22, 0.8), S.chord([45, 52, 57], 1.4, 0.0, 0.7, wave="saw")), 0.9)]

        var("rubber", lambda k: S.norm(S.mix(S.osc(0.09, 420 * k, 300 * k, wave="tri") * S.env(0.09, 0.001, 0.03), S.band(S.noise(0.01), 800, 4000) * S.env(0.01, 0.0003, 0.003) * 0.5), 0.6))
        var("spin", lambda k: S.norm(S.mix(S.band(S.noise(0.012), 2500, 9000) * S.env(0.012, 0.0003, 0.003), S.modal(0.05, 3200 * k, S.METAL) * 0.6), 0.45), 3)
        var("kickback", lambda k: S.norm(S.mix(S.solenoid(100 * k, 14, 1.0), S.osc(0.3, 180 * k, 720 * k, wave="saw") * S.env(0.3, 0.004, 0.08) * 0.6), 0.9), 2)
        bank["select"] = [S.norm(S.mix(S.osc(0.08, 660, wave="square") * S.env(0.08, 0.002, 0.03), S.osc(0.12, 990, wave="square") * S.env(0.12, 0.002, 0.04) * 0.6, at=[0.0, 0.06]), 0.5)]
        bank["cheat"] = [S.norm(S.mix(*[S.osc(0.09, midi_hz(n), wave="square") * S.env(0.09, 0.002, 0.04) for n in (64, 68, 71, 76, 80)], at=[i * 0.06 for i in range(5)]), 0.6)]

        # Bucles: rodadura y música (se generan sin fade: periódicos por FFT)
        bank["roll"] = [self._rolling()]
        bank["music"] = [self._music(112.0, [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]], "tri")]
        return bank

    def _rolling(self):
        """Ruido marrón filtrado. Al filtrar por FFT la señal es periódica en
        su longitud: el bucle no tiene costura."""
        np, S = self.np, self.synth
        x = S.band(S.noise(1.0), 60, 1400)
        f = np.fft.rfftfreq(len(x), 1.0 / self.RATE)
        X = np.fft.rfft(x) / np.sqrt(np.maximum(f, 20.0))      # pendiente -3 dB/oct
        x = np.fft.irfft(X, len(x))
        return S.norm(x, 0.7)

    def _music(self, bpm: float, chords: list[list[int]], arp_wave: str = "tri"):
        """Bucle de 4 compases: bajo, arpegio, bombo, caja y charles, sobre
        la progresión que pida la máquina. Generado por notas y mezclado por
        offsets; lo que sobresale del final se pliega al principio."""
        S = self.synth
        beat = 60.0 / bpm
        step = beat / 4.0
        parts, offs = [], []

        def put(x, t0):
            parts.append(x)
            offs.append(t0)

        for bar, ch in enumerate(chords):
            t0 = bar * 16 * step
            root = ch[0] - 12
            bass_pat = [0, None, 7, None, 0, None, 12, None, 0, None, 7, None, 0, 10, None, 7]
            for i, iv in enumerate(bass_pat):
                if iv is None:
                    continue
                x = S.osc(step * 1.9, midi_hz(root + iv), wave="saw") * S.adsr(step * 1.9, 0.004, 0.08, 0.45, 0.05)
                put(S.band(x, 40, 900) * 0.55, t0 + i * step)
            arp = [ch[0] + 12, ch[1] + 12, ch[2] + 12, ch[1] + 24, ch[2] + 12, ch[0] + 24, ch[1] + 12, ch[2] + 24] * 2
            for i, n in enumerate(arp):
                x = S.osc(step * 1.2, midi_hz(n), wave=arp_wave) * S.env(step * 1.2, 0.003, step * 0.6)
                put(x * (0.33 if i % 4 == 0 else 0.22), t0 + i * step)
            for i in range(16):
                if i % 8 == 0:        # bombo
                    put(S.osc(0.14, 160, 42) * S.env(0.14, 0.001, 0.05) * 0.9, t0 + i * step)
                if i % 8 == 4:        # caja
                    put(S.mix(S.band(S.noise(0.14), 700, 5000) * S.env(0.14, 0.001, 0.04), S.osc(0.08, 190, 120) * S.env(0.08, 0.001, 0.02)) * 0.45, t0 + i * step)
                if i % 2 == 1:        # charles
                    put(S.band(S.noise(0.03), 5000, 11000) * S.env(0.03, 0.0005, 0.01) * 0.25, t0 + i * step)
        loop_len = int(4 * 16 * step * self.RATE)
        x = S.mix(*parts, at=offs)
        # Lo que sobresale del final se pliega al principio: el bucle cierra
        tail = x[loop_len:]
        x = x[:loop_len].copy()
        x[: len(tail)] += tail
        return S.norm(x, 0.8)

    # --- reproducción ----------------------------------------------------------
    def _start_loops(self) -> None:
        roll = pygame.mixer.Channel(self.CH_ROLL)
        roll.play(self.bank["roll"][0], loops=-1)
        roll.set_volume(0.0, 0.0)
        music = pygame.mixer.Channel(self.CH_MUSIC)
        music.play(self.bank["music"][0], loops=-1)
        v = MUSIC_VOL * MASTER_VOL if self.music_on else 0.0
        music.set_volume(v, v)

    def set_music(self, bpm: float, chords: list[list[int]], arp_wave: str = "tri") -> None:
        """Regenera el bucle musical para una máquina y lo reinicia."""
        if not self.ok:
            return
        try:
            self.bank["music"] = [pygame.sndarray.make_sound(self._to_pcm(self._music(bpm, chords, arp_wave)))]
            ch = pygame.mixer.Channel(self.CH_MUSIC)
            ch.stop()
            ch.play(self.bank["music"][0], loops=-1)
            v = MUSIC_VOL * MASTER_VOL if self.music_on else 0.0
            ch.set_volume(v, v)
        except Exception:
            pass

    def toggle_music(self) -> bool:
        self.music_on = not self.music_on
        if self.ok:
            v = MUSIC_VOL * MASTER_VOL if self.music_on else 0.0
            pygame.mixer.Channel(self.CH_MUSIC).set_volume(v, v)
        return self.music_on

    @staticmethod
    def pan(x: float | None) -> tuple[float, float]:
        if x is None:
            return (1.0, 1.0)
        p = clamp((x - PF_MID) / 300.0, -1.0, 1.0)
        return (clamp(1.0 - 0.6 * p, 0.3, 1.0), clamp(1.0 + 0.6 * p, 0.3, 1.0))

    def play(self, name: str, x: float | None = None, vol: float = 1.0, index: int | None = None) -> None:
        if not self.ok:
            return
        variants = self.bank.get(name)
        if not variants:
            return
        snd = variants[index % len(variants)] if index is not None else random.choice(variants)
        try:
            ch = snd.play()
            if ch is not None:
                lft, rgt = self.pan(x)
                v = clamp(vol, 0.0, 1.0) * MASTER_VOL
                ch.set_volume(lft * v, rgt * v)
        except Exception:
            pass

    def tick(self, now: float, x: float, impact: float) -> None:
        """Toque de bola contra metal, limitado a uno cada 35 ms."""
        if now - self.last_tick < 0.035:
            return
        self.last_tick = now
        vol = clamp(impact / 1400.0, 0.12, 1.0)
        self.play("tick" if impact > 320 else "thud", x, vol)

    def update(self, dt: float, ball: Ball, active: bool) -> None:
        """Rodadura: el volumen sigue la velocidad con un suavizado."""
        if not self.ok:
            return
        target = 0.0
        if active and ball.alive and not ball.held:
            target = clamp(ball.speed() / 1500.0, 0.0, 1.0) ** 1.4 * ROLL_VOL
        k = clamp(dt * 18.0, 0.0, 1.0)
        self.roll_level += (target - self.roll_level) * k
        lft, rgt = self.pan(ball.x)
        v = self.roll_level * MASTER_VOL
        try:
            pygame.mixer.Channel(self.CH_ROLL).set_volume(lft * v, rgt * v)
        except Exception:
            pass

    def dump(self, folder: str) -> int:
        """Vuelca cada sonido a WAV (para escucharlos o revisarlos). Funciona
        sin mixer: solo necesita numpy."""
        import wave
        if self.np is None:
            import numpy as np
            self.np = np
            self.synth = Synth(np, self.RATE)
        os.makedirs(folder, exist_ok=True)
        count = 0
        for name, variants in self.build_bank().items():
            for i, v in enumerate(variants):
                pcm = (self.np.clip(v, -1.0, 1.0) * 32767).astype(self.np.int16)
                with wave.open(os.path.join(folder, f"{name}_{i}.wav"), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(self.RATE)
                    w.writeframes(pcm.tobytes())
                count += 1
        return count


# =============================================================================
# 8. REGLAS, LUCES, TRAMPAS Y MÁQUINA DE ESTADOS
# =============================================================================

LEFT_KEYS = (pygame.K_LSHIFT, pygame.K_z, pygame.K_LEFT, pygame.K_LCTRL)
RIGHT_KEYS = (pygame.K_RSHIFT, pygame.K_SLASH, pygame.K_RIGHT, pygame.K_RCTRL)
CHEAT_WORD = "trampa"


class State(Enum):
    ATTRACT = auto()
    PLAYING = auto()
    BALL_LOST = auto()
    GAME_OVER = auto()


@dataclass(slots=True)
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    color: tuple[int, int, int]


class Game:
    def __init__(self, sounds: Sounds, machine_index: int = 0) -> None:
        self.snd = sounds
        self.state = State.ATTRACT
        self.time = 0.0
        self.timers: list[tuple[float, object]] = []
        self.ball = Ball(PLUNGER_X, 900.0)
        self.ball.alive = False
        self.hiscores = self.load_hiscores()
        self.machine_index = machine_index
        self.machine = MACHINES[machine_index]
        self.table = build_table(self.machine)
        self.phys = Physics(self.table)
        self.pal = self.machine.pal
        self.score = 0
        self.balls_left = 0
        self.ball_number = 0
        self.bonus = 0
        self.multiplier = 1
        self.extra_ball_given = False
        self.tilt = False
        self.tilt_heat = 0.0
        self.ball_save_until = 0.0
        self.launch_time = -1.0
        self.skill_lane = -1
        self.skill_active = False
        self.plunger_charge = 0.0
        self.plunger_held = False
        self.ratchet_step = 0
        self.combo = 0
        self.combo_time = 0.0
        self.message = ""
        self.message_until = 0.0
        self.shake = 0.0
        self.shake_x = 0.0
        self.shake_y = 0.0
        self.particles: list[Particle] = []
        self.debug = False
        self.paused = False
        self.flash = 0.0
        self.show_until = 0.0           # persecución de luces por evento
        self.show_kind = ""
        # trampas
        self.cheat = False
        self.typed = ""
        self.gravity_scale = 1.0
        self.tilt_immune = False
        self.slowmo = False
        self.dragging = False
        self.drag_prev: tuple[float, float] | None = None
        self.mouse = (0.0, 0.0)
        self.snd.set_music(self.machine.bpm, self.machine.chords, self.machine.arp_wave)

    # --- récords por máquina ----------------------------------------------
    @staticmethod
    def load_hiscores() -> dict[str, int]:
        try:
            raw = HISCORE_FILE.read_text().strip()
            if raw.isdigit():                       # formato antiguo: un entero
                return {"classic": int(raw)}
            data = json.loads(raw or "{}")
            return {str(k): int(v) for k, v in data.items()}
        except Exception:
            return {}

    @property
    def hiscore(self) -> int:
        return self.hiscores.get(self.machine.key, 0)

    def save_hiscore(self) -> None:
        if self.cheat:
            return
        try:
            HISCORE_FILE.write_text(json.dumps(self.hiscores))
        except Exception:
            pass

    # --- utilidades -------------------------------------------------------
    def after(self, delay: float, fn) -> None:
        self.timers.append((self.time + delay, fn))

    def say(self, text: str, dur: float = 1.6) -> None:
        self.message = text
        self.message_until = self.time + dur

    def light_show(self, kind: str, dur: float = 1.6) -> None:
        self.show_kind = kind
        self.show_until = self.time + dur

    def add_score(self, pts: int) -> None:
        if self.tilt or self.state is not State.PLAYING:
            return
        self._bump(pts * self.multiplier)

    def _bump(self, pts: int) -> None:
        """Suma puntos ya multiplicados y comprueba récord y bola extra."""
        self.score += pts
        if self.score > self.hiscore and not self.cheat:
            self.hiscores[self.machine.key] = self.score
        if not self.extra_ball_given and self.score >= EXTRA_BALL_SCORE:
            self.extra_ball_given = True
            self.balls_left += 1
            self.say("EXTRA BALL", 2.5)
            self.snd.play("extra")
            self.flash = 0.4
            self.light_show("sweep", 2.5)

    def spawn_particles(self, x: float, y: float, n: int, color: tuple[int, int, int], speed: float = 260.0) -> None:
        for _ in range(n):
            a = random.uniform(0, math.tau)
            s = random.uniform(0.3, 1.0) * speed
            self.particles.append(Particle(x, y, math.cos(a) * s, math.sin(a) * s, random.uniform(0.25, 0.55), color))

    # --- máquinas -----------------------------------------------------------
    def select_machine(self, delta: int) -> None:
        self.machine_index = (self.machine_index + delta) % len(MACHINES)
        self.machine = MACHINES[self.machine_index]
        self.table = build_table(self.machine)
        self.phys = Physics(self.table)
        self.pal = self.machine.pal
        self.ball.alive = False
        self.particles.clear()
        self.snd.play("select", None, 0.7)
        self.snd.set_music(self.machine.bpm, self.machine.chords, self.machine.arp_wave)

    # --- partida ----------------------------------------------------------
    def new_game(self) -> None:
        self.score = 0
        self.balls_left = BALLS_PER_GAME
        self.ball_number = 0
        self.multiplier = 1
        self.extra_ball_given = False
        self.tilt = False
        self.tilt_heat = 0.0
        self.combo = 0
        self.timers.clear()
        self.particles.clear()
        for w in self.table.drop_targets:
            w.lit = False
        for w in self.table.standups:
            w.lit = False
        for s in self.table.rollovers:
            s.lit = False
        for h in self.table.holes:
            h.visits = 0
        for k in self.table.kickbacks:
            k.lit = True                    # el primer kickback de la partida va de regalo
        self.state = State.PLAYING
        self.snd.play("start")
        self.light_show("sweep", 1.5)
        self.next_ball()

    def next_ball(self) -> None:
        if self.balls_left <= 0:
            self.state = State.GAME_OVER
            self.save_hiscore()
            self.snd.play("gameover")
            self.say("GAME OVER", 4.0)
            self.light_show("fade", 3.0)
            return
        self.balls_left -= 1
        self.ball_number += 1
        self.bonus = 0
        self.tilt = False
        self.tilt_heat = 0.0
        self.combo = 0
        self.state = State.PLAYING
        self.reset_ball_to_plunger()
        self.say(f"BOLA {self.ball_number}", 1.5)

    def reset_ball_to_plunger(self) -> None:
        b = self.ball
        b.x, b.y, b.vx, b.vy = PLUNGER_X, LANE_FLOOR_Y - b.r - 1, 0.0, 0.0
        b.alive = True
        b.held = False
        for h in self.table.holes:
            h.holding = False
        b.trail.clear()
        b.still_time = 0.0
        self.launch_time = -1.0
        self.skill_active = False
        self.plunger_charge = 0.0

    def launch(self) -> None:
        b = self.ball
        if not (b.alive and b.in_lane() and b.y > 880 and abs(b.vy) < 40):
            return
        speed = PLUNGER_MIN + (PLUNGER_MAX - PLUNGER_MIN) * self.plunger_charge
        b.vy = -speed
        b.vx = 0.0
        self.snd.play("launch", b.x, 0.5 + 0.5 * self.plunger_charge)
        if self.launch_time < 0:
            self.launch_time = self.time
            self.ball_save_until = self.time + BALL_SAVE_S
            self.skill_lane = random.randrange(len(self.table.rollovers))
            self.skill_active = True
            self.say("SKILL SHOT", 1.2)

    # --- golpes a la máquina ------------------------------------------------
    def nudge(self, dx: float, dy: float) -> None:
        """Golpe a la máquina. dx: lateral (-1 izq, +1 der); dy: -1 desde abajo.
        La bola recibe el impulso; el calor de tilt sube y decae con el tiempo."""
        if self.state is not State.PLAYING or self.tilt or not self.ball.alive:
            return
        if self.ball.in_lane() and not self.cheat:
            return
        heat = TILT_HEAT_PER_NUDGE * (1.6 if dy else 1.0)
        if not self.tilt_immune:
            self.tilt_heat += heat
        b = self.ball
        if not b.held:
            b.vx += dx * NUDGE_VX
            b.vy += NUDGE_VY + dy * NUDGE_UP_VY
        self.shake = 0.2
        self.shake_x = dx
        self.shake_y = dy
        self.snd.play("nudge", b.x, 0.8 if not dy else 1.0)
        if self.tilt_heat > TILT_LIMIT:
            self.do_tilt()
        elif self.tilt_heat > TILT_WARN:
            self.say("CUIDADO", 1.0)
            self.snd.play("warn")

    def do_tilt(self) -> None:
        self.tilt = True
        self.bonus = 0
        for f in self.table.flippers:
            f.pressed = False
        self.say("TILT", 3.0)
        self.snd.play("tilt")
        self.light_show("off", 3.0)

    def lane_change(self) -> None:
        """Pulsar un flipper rota las luces de los carriles superiores."""
        r = self.table.rollovers
        if any(s.inside for s in r):
            return
        lits = [s.lit for s in r]
        lits = [lits[-1]] + lits[:-1]
        for s, v in zip(r, lits):
            s.lit = v

    # --- trampas --------------------------------------------------------------
    def toggle_cheat(self) -> None:
        self.cheat = not self.cheat
        if self.cheat:
            self.say("MODO TRAMPA: sin récord", 2.5)
            self.snd.play("cheat")
        else:
            self.say("TRAMPAS OFF", 1.5)
            self.gravity_scale = 1.0
            self.tilt_immune = False
            self.slowmo = False
            self.phys.gravity_scale = 1.0

    def cheat_key(self, key: int) -> bool:
        if not self.cheat:
            return False
        match key:
            case pygame.K_F2:
                self.gravity_scale = 0.45 if self.gravity_scale == 1.0 else 1.0
                self.phys.gravity_scale = self.gravity_scale
                self.say("MÁQUINA LEVANTADA" if self.gravity_scale < 1 else "MÁQUINA EN EL SUELO", 1.5)
            case pygame.K_F3:
                self.tilt_immune = not self.tilt_immune
                self.tilt_heat = 0.0
                self.say("PLOMADA ATADA" if self.tilt_immune else "PLOMADA LIBRE", 1.5)
            case pygame.K_F4:
                if self.state is State.PLAYING:
                    self.balls_left += 1
                    self.say("BOLA DE REGALO", 1.5)
            case pygame.K_F5:
                if self.state is State.PLAYING:
                    self._bump(10_000)
                    self.say("+10.000", 1.0)
            case pygame.K_F6:
                self.slowmo = not self.slowmo
                self.say("CÁMARA LENTA" if self.slowmo else "VELOCIDAD NORMAL", 1.5)
            case pygame.K_F7:
                if self.state is State.PLAYING:
                    b = self.ball
                    b.x, b.y, b.vx, b.vy = self.mouse[0], self.mouse[1], 0.0, 0.0
                    b.alive, b.held = True, False
                    self.say("BOLA EN LA MANO", 1.0)
            case _:
                return False
        self.snd.play("cheat", None, 0.5)
        return True

    def mouse_down(self, pos: tuple[float, float]) -> None:
        self.mouse = pos
        b = self.ball
        if self.cheat and b.alive and math.hypot(pos[0] - b.x, pos[1] - b.y) < 40:
            self.dragging = True
            self.drag_prev = pos
            b.held = False
            for h in self.table.holes:
                h.holding = False

    def mouse_move(self, pos: tuple[float, float]) -> None:
        self.mouse = pos
        if self.dragging and self.ball.alive:
            b = self.ball
            if self.drag_prev is not None:
                b.vx = (pos[0] - self.drag_prev[0]) * 40.0
                b.vy = (pos[1] - self.drag_prev[1]) * 40.0
            b.x, b.y = pos
            self.drag_prev = pos

    def mouse_up(self, pos: tuple[float, float]) -> None:
        self.mouse = pos
        self.dragging = False
        self.drag_prev = None

    # --- eventos de física ------------------------------------------------
    def handle_events(self) -> None:
        for name, obj, impact in self.phys.events:
            match name:
                case "bumper":
                    assert isinstance(obj, Post)
                    obj.flash = 0.12
                    obj.hits += 1
                    self.combo = self.combo + 1 if self.time - self.combo_time < 1.2 else 1
                    self.combo_time = self.time
                    pts = 100 * min(self.combo, 10)
                    self.add_score(pts)
                    self.snd.play("bumper", obj.c.x, 0.9)
                    self.snd.play("bumper_note", obj.c.x, 0.55, index=min(self.combo, 10) - 1)
                    self.spawn_particles(obj.c.x, obj.c.y, 10, self.pal["bumper_lit"])
                    for lp in self.table.lamps:
                        if lp.group == "bumper" or (lp.group == "bring" and math.hypot(lp.c.x - obj.c.x, lp.c.y - obj.c.y) < obj.r + 14):
                            lp.phase = self.time
                    if self.combo >= 5 and self.combo % 5 == 0:
                        self.say(f"COMBO x{self.combo}", 0.8)
                        self.light_show("flash", 0.6)
                case "sling":
                    assert isinstance(obj, Wall)
                    obj.flash = 0.1
                    self.add_score(50)
                    mx, my = (obj.a.x + obj.b.x) / 2, (obj.a.y + obj.b.y) / 2
                    self.snd.play("sling", mx, 0.9)
                    self.spawn_particles(mx, my, 6, self.pal["sling_lit"], 180)
                case "target":
                    assert isinstance(obj, Wall)
                    if not obj.lit:
                        obj.lit = True
                        obj.flash = 0.2
                        self.add_score(500)
                        self.bonus += 1000
                        self.snd.play("target", obj.a.x)
                        if all(w.lit for w in self.table.drop_targets):
                            self.add_score(5000)
                            self.say("BANCO 5000", 1.5)
                            self.snd.play("bank_complete", obj.a.x)
                            self.light_show("sweep", 1.5)
                            self.after(1.0, self.reset_drop_targets)
                case "standup":
                    assert isinstance(obj, Wall)
                    obj.flash = 0.2
                    if not obj.lit:
                        obj.lit = True
                        self.add_score(300)
                        self.bonus += 500
                        self.snd.play("standup", obj.a.x)
                        if all(w.lit for w in self.table.standups):
                            self.add_score(3000 * len(self.table.standups) // 3)
                            self.say(f"{'-'.join(self.table.letters)} {3000 * len(self.table.standups) // 3}", 1.5)
                            self.snd.play("bank_complete", obj.a.x)
                            self.light_show("sweep", 1.5)
                            for k in self.table.kickbacks:
                                k.lit = True
                                k.flash = 0.5
                            self.after(1.0, self.reset_standups)
                    else:
                        self.add_score(50)
                        self.snd.tick(self.time, obj.a.x, impact)
                case "rollover":
                    assert isinstance(obj, Sensor)
                    obj.flash = 0.25
                    self.bonus += 1000
                    pts = 250
                    if self.skill_active:
                        self.skill_active = False
                        if obj.index == self.skill_lane:
                            pts = 5000
                            self.say("SKILL SHOT 5000", 1.5)
                            self.snd.play("skill")
                            self.flash = 0.3
                            self.light_show("sweep", 1.5)
                    self.add_score(pts)
                    rx = (obj.a.x + obj.b.x) / 2
                    self.snd.play("rollover", rx, 0.8, index=obj.index)
                    obj.lit = True
                    if all(s.lit for s in self.table.rollovers):
                        for s in self.table.rollovers:
                            s.lit = False
                        if self.multiplier < 5:
                            self.multiplier += 1
                            self.say(f"MULTIPLICADOR x{self.multiplier}", 1.5)
                            self.snd.play("mult")
                        else:
                            self.add_score(10000)
                            self.say("CARRILES 10000", 1.5)
                            self.snd.play("lanes_complete")
                        self.light_show("chase", 1.5)
                case "hole":
                    assert isinstance(obj, Hole)
                    obj.visits += 1
                    obj.flash = 1.2
                    award = min(obj.visits, 5) * 1000
                    self.add_score(award)
                    self.bonus += 2000
                    self.say(f"AGUJERO {award}", 1.2)
                    self.snd.play("hole_in", obj.c.x)
                    self.flash = 0.2
                    self.light_show("flash", 0.5)
                    self.after(1.2, lambda h=obj: self.eject(h))
                case "rubber":
                    assert isinstance(obj, Post)
                    obj.flash = 0.12
                    self.add_score(10)
                    self.snd.play("rubber", obj.c.x, clamp(impact / 1200.0, 0.3, 1.0))
                case "spinner":
                    assert isinstance(obj, Spinner)
                    obj.flash = 0.15
                    self.add_score(100)
                    self.snd.play("spin", (obj.a.x + obj.b.x) / 2, 0.6)
                case "kickback":
                    assert isinstance(obj, Kickback)
                    self.add_score(1000)
                    self.say("KICKBACK", 1.0)
                    self.snd.play("kickback", obj.x)
                    self.spawn_particles(obj.x, obj.y, 8, self.pal["light_on"], 220)
                case "flipper_hit":
                    assert isinstance(obj, Flipper)
                    self.combo = 0
                    self.snd.play("flipper_hit", obj.pivot.x, clamp(impact / 1800.0, 0.3, 1.0))
                case "hit":
                    self.snd.tick(self.time, float(obj), impact)
                case "drain":
                    self.on_drain()
        self.phys.events.clear()

    def eject(self, h: Hole) -> None:
        b = self.ball
        if not (h.holding and b.held):
            return
        h.holding = False
        h.cooldown = 1.5
        b.held = False
        b.x, b.y = h.c.x, h.c.y
        b.vx = h.eject.x + random.uniform(-40, 40)
        b.vy = h.eject.y + random.uniform(-40, 40)
        self.snd.play("hole_out", h.c.x)
        self.spawn_particles(h.c.x, h.c.y, 8, self.pal["light_on"], 200)

    def reset_drop_targets(self) -> None:
        for w in self.table.drop_targets:
            w.lit = False
            w.flash = 0.3

    def reset_standups(self) -> None:
        for w in self.table.standups:
            w.lit = False
            w.flash = 0.3

    def on_drain(self) -> None:
        self.snd.play("drain", self.ball.x)
        if self.state is not State.PLAYING:
            return
        if self.time < self.ball_save_until and not self.tilt:
            self.say("BALL SAVE", 1.5)
            self.snd.play("ball_save")
            self.after(0.8, self.ball_saved)
            return
        self.state = State.BALL_LOST
        earned = 0 if self.tilt else self.bonus * self.multiplier
        if earned:
            # Cuenta del bonus a golpes, como una máquina electromecánica
            steps = max(1, min(10, self.bonus // 1000))
            chunk = earned // steps
            rest = earned - chunk * steps
            for i in range(steps):
                self.after(0.5 + i * 0.13, lambda i=i: self._bonus_step(chunk + (rest if i == steps - 1 else 0), i, steps))
            self.after(0.5 + steps * 0.13 + 1.3, self.next_ball)
        else:
            if self.tilt:
                self.say("TILT: SIN BONUS", 2.0)
            self.after(2.2, self.next_ball)

    def _bonus_step(self, pts: int, i: int, steps: int) -> None:
        self._bump(pts)
        self.snd.play("bonus_tick", None, 0.7, index=min(i, 9))
        self.say(f"BONUS {self.bonus} x{self.multiplier}", 2.0)
        if i == steps - 1:
            self.flash = 0.15

    def ball_saved(self) -> None:
        if self.state is State.PLAYING:
            self.reset_ball_to_plunger()
            self.ball_save_until = self.time + 3.0
            self.launch_time = self.time   # no reactivar skill shot

    # --- entrada ----------------------------------------------------------
    def text_input(self, ch: str) -> None:
        """Acumula letras para los códigos secretos."""
        if ch and ch.isalpha():
            self.typed = (self.typed + ch.lower())[-12:]
            if self.typed.endswith(CHEAT_WORD):
                self.typed = ""
                self.toggle_cheat()

    def key_down(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            raise SystemExit
        if key == pygame.K_F1:
            self.debug = not self.debug
            return
        if key == pygame.K_p:
            self.paused = not self.paused
            return
        if key == pygame.K_m:
            self.say("MÚSICA ON" if self.snd.toggle_music() else "MÚSICA OFF", 1.0)
            return
        if self.cheat_key(key):
            return
        if self.state in (State.ATTRACT, State.GAME_OVER):
            if key in LEFT_KEYS:
                self.select_machine(-1)
                return
            if key in RIGHT_KEYS:
                self.select_machine(1)
                return
            if key in (pygame.K_r, pygame.K_RETURN, pygame.K_SPACE):
                self.new_game()
                return
            return
        if key == pygame.K_r:
            self.new_game()
            return
        if self.state is not State.PLAYING:
            return
        enabled = not self.tilt
        if key in LEFT_KEYS:
            self.table.flippers[0].pressed = True
            if enabled:
                self.snd.play("flipper_up", self.table.flippers[0].pivot.x)
                self.lane_change()
        elif key in RIGHT_KEYS:
            self.table.flippers[1].pressed = True
            if enabled:
                self.snd.play("flipper_up", self.table.flippers[1].pivot.x)
                self.lane_change()
        elif key == pygame.K_SPACE:
            self.plunger_held = True
            self.plunger_charge = 0.0
            self.ratchet_step = 0
        elif key == pygame.K_x:
            self.nudge(-1.0, 0.0)
        elif key == pygame.K_v:
            self.nudge(1.0, 0.0)
        elif key == pygame.K_c:
            self.nudge(0.0, -1.0)

    def key_up(self, key: int) -> None:
        if key in LEFT_KEYS:
            if self.table.flippers[0].pressed and not self.tilt:
                self.snd.play("flipper_down", self.table.flippers[0].pivot.x)
            self.table.flippers[0].pressed = False
        elif key in RIGHT_KEYS:
            if self.table.flippers[1].pressed and not self.tilt:
                self.snd.play("flipper_down", self.table.flippers[1].pivot.x)
            self.table.flippers[1].pressed = False
        elif key == pygame.K_SPACE:
            if self.plunger_held:
                self.plunger_held = False
                self.launch()
                self.plunger_charge = 0.0

    # --- luces ------------------------------------------------------------------
    def update_lamps(self) -> None:
        """Decide qué lámpara está encendida. Es un pequeño secuenciador:
        patrón de fondo por estado y persecuciones temporales por evento."""
        t = self.time
        show = self.show_kind if t < self.show_until else ""
        k_show = (self.show_until - t) if show else 0.0
        targets_unlit = [not w.lit for w in self.table.drop_targets]
        standups_unlit = [not w.lit for w in self.table.standups]
        combo_level = min(5, self.combo) if (t - self.combo_time) < 1.2 else 0
        attract = self.state in (State.ATTRACT, State.GAME_OVER)
        ring = [lp for lp in self.table.lamps if lp.group == "ring"]
        n_ring = max(1, len(ring))
        iL = iR = iM = 0
        blink = (t * 4.0) % 1.0 < 0.5
        for lp in self.table.lamps:
            g = lp.group
            on = False
            if show == "off":
                on = False
            elif show == "flash":
                on = (t * 12.0) % 1.0 < 0.5
            elif show == "fade":
                on = random.random() < k_show / 3.0 * 0.3
            elif g == "ring":
                idx = ring.index(lp)
                if show == "sweep":
                    head = (1.0 - k_show / 1.5) * n_ring * 1.6
                    on = abs(idx - head) < 2.0 or abs(idx - (n_ring - head)) < 2.0
                elif show == "chase" or attract:
                    on = (idx - int(t * 10)) % n_ring < 3 if show == "chase" else (idx - int(t * 4)) % n_ring < 2
                else:
                    on = idx < n_ring * clamp(self.tilt_heat / TILT_LIMIT, 0.0, 1.0) and blink
            elif g == "arrowL":
                on = attract and blink or (not attract and (targets_unlit[iL] if iL < len(targets_unlit) else False) and blink)
                iL += 1
            elif g == "arrowR":
                on = attract and blink or (not attract and (standups_unlit[iR] if iR < len(standups_unlit) else False) and blink)
                iR += 1
            elif g == "meter":
                on = (iM < combo_level) if not attract else ((int(t * 6) % 6) > iM)
                iM += 1
            elif g == "bumper":
                on = (t - lp.phase) < 0.25 or (attract and (t * 3 + lp.phase) % 1.0 < 0.3)
            elif g in ("kickL", "kickR"):
                lit = any(k.lit for k in self.table.kickbacks if (k.x < PF_MID) == (g == "kickL"))
                on = lit and (blink or attract)
            elif g == "inlane":
                on = (int(t * 8) + int(lp.c.y)) % 3 == 0 if (attract or show) else (self.time < self.ball_save_until and blink)
            elif g == "star":
                on = (math.sin(t * 2.0 + lp.phase * 20) > 0.6)
            elif g == "bring":
                on = (t - lp.phase) < 0.3 or (attract and (t * 2 + lp.phase) % 1.0 < 0.15)
            elif g == "rail":
                on = (int(t * 10) + int(lp.c.y / 60)) % 6 == 0 if (attract or show) else (combo_level > 0 and blink)
            elif g == "sign":
                on = (int(t * 5) % 5) == int(lp.phase * 5) % 5 if attract else blink or self.multiplier > 1
            lp.on = on

    # --- actualización ----------------------------------------------------
    def update(self, dt: float) -> None:
        """Un paso fijo de física + reglas."""
        if self.slowmo:
            dt *= 0.35
        self.time += dt
        due = [tm for tm in self.timers if tm[0] <= self.time]
        if due:
            self.timers = [tm for tm in self.timers if tm[0] > self.time]
            for _, fn in due:
                fn()

        if self.plunger_held:
            self.plunger_charge = clamp(self.plunger_charge + dt / PLUNGER_CHARGE_S, 0.0, 1.0)
            step = int(self.plunger_charge * 8)
            if step > self.ratchet_step:
                self.ratchet_step = step
                self.snd.play("ratchet", PLUNGER_X, 0.5 + 0.05 * step)

        self.tilt_heat = max(0.0, self.tilt_heat - TILT_HEAT_DECAY * dt)
        if self.skill_active and self.time - self.launch_time > SKILL_SHOT_S:
            self.skill_active = False

        playing = self.state in (State.PLAYING, State.BALL_LOST)
        sub = dt / SUBSTEPS
        flippers_on = not self.tilt and self.state is State.PLAYING
        if not self.dragging:
            for _ in range(SUBSTEPS):
                self.phys.substep(self.ball, sub, flippers_on)
        self.handle_events()
        self.snd.update(dt, self.ball, self.state is State.PLAYING and not self.dragging)

        # Bola atascada: pequeño empujón aleatorio
        b = self.ball
        held = any(f.pressed for f in self.table.flippers)
        if playing and b.alive and not b.held and not b.in_lane() and not held and not self.dragging:
            if b.speed() < 12.0:
                b.still_time += dt
                if b.still_time > STUCK_S:
                    b.vx += random.choice((-1, 1)) * 90.0
                    b.vy -= 160.0
                    b.still_time = 0.0
            else:
                b.still_time = 0.0

        if b.alive:
            b.trail.append((b.x, b.y))
            if len(b.trail) > 10:
                b.trail.pop(0)

        # Efectos
        for w in self.table.walls:
            if w.flash > 0:
                w.flash -= dt
        for p in self.table.posts:
            if p.flash > 0:
                p.flash -= dt
        for s in self.table.sensors:
            if s.flash > 0:
                s.flash -= dt
        for h in self.table.holes:
            if h.flash > 0:
                h.flash -= dt
        for sp in self.table.spinners:
            if sp.flash > 0:
                sp.flash -= dt
        for k in self.table.kickbacks:
            if k.flash > 0:
                k.flash -= dt
        self.shake = max(0.0, self.shake - dt)
        self.flash = max(0.0, self.flash - dt)
        alive: list[Particle] = []
        for p in self.particles:
            p.life -= dt
            if p.life > 0:
                p.vy += 600 * dt
                p.x += p.vx * dt
                p.y += p.vy * dt
                alive.append(p)
        self.particles = alive
        self.update_lamps()


# =============================================================================
# 9. RENDER
# =============================================================================

class Renderer:
    def __init__(self) -> None:
        self.surf = pygame.Surface((W, H))
        self.glow = pygame.Surface((W, H), pygame.SRCALPHA)
        self.font_big = pygame.font.Font(None, 56)
        self.font = pygame.font.Font(None, 30)
        self.font_small = pygame.font.Font(None, 22)
        self.font_title = pygame.font.Font(None, 110)

    def text(self, s: str, font: pygame.font.Font, color, pos: tuple[float, float], anchor: str = "topleft") -> None:
        img = font.render(s, True, color)
        rect = img.get_rect(**{anchor: pos})
        self.surf.blit(img, rect)

    def draw(self, g: Game, blink: bool) -> pygame.Surface:
        s = self.surf
        t = g.table
        pal = g.pal
        s.fill(C_BG)
        ox = oy = 0.0
        if g.shake > 0:
            k = math.sin(g.shake * 60) * (g.shake / 0.2)
            ox = k * 7 * g.shake_x
            oy = k * 7 * g.shake_y if g.shake_y else k * 2

        def P(v: Vec2 | tuple[float, float]) -> tuple[float, float]:
            if isinstance(v, Vec2):
                return (v.x + ox, v.y + oy)
            return (v[0] + ox, v[1] + oy)

        # Campo de juego
        pygame.draw.circle(s, pal["field"], P(ARC_C), ARC_R)
        pygame.draw.rect(s, pal["field"], pygame.Rect(20 + ox, ARC_C.y + oy, 500, H - ARC_C.y))
        pygame.draw.circle(s, pal["wall"], P(ARC_C), ARC_R, 3)
        pygame.draw.rect(s, pal["field"], pygame.Rect(0 + ox, ARC_C.y + oy, W, H))
        pygame.draw.rect(s, C_BG, pygame.Rect(0, ARC_C.y + oy, 20 + ox, H))
        pygame.draw.rect(s, C_BG, pygame.Rect(520 + ox, ARC_C.y + oy, W, H))

        # Lámparas (debajo de todo, con halo)
        self.glow.fill((0, 0, 0, 0))
        for lp in t.lamps:
            col = lp.color or pal["light_on"]
            if lp.on:
                pygame.draw.circle(self.glow, (*col, 60), P(lp.c), lp.r * 3.0)
                pygame.draw.circle(s, col, P(lp.c), lp.r)
                pygame.draw.circle(s, (255, 255, 255), P(lp.c), max(1.0, lp.r * 0.4))
            else:
                pygame.draw.circle(s, pal["light_off"], P(lp.c), lp.r)
                pygame.draw.circle(s, pal["wall_dim"], P(lp.c), lp.r, 1)
        s.blit(self.glow, (0, 0))

        # Slingshots
        for poly, face in zip(t.sling_polys, t.slings):
            pygame.draw.polygon(s, mix(pal["sling"], pal["sling_lit"], face.flash * 8), [P(v) for v in poly])

        # Paredes
        for w in t.walls:
            if w.name in ("target", "standup", "sling"):
                continue
            col, width = pal["wall"], 3
            if w.name in ("gate", "side"):
                col, width = pal["wall_dim"], 2
            elif w.name == "lane_divider":
                width = 4
            pygame.draw.line(s, col, P(w.a), P(w.b), width)

        # Dianas y standups
        for w in t.drop_targets:
            col = mix(pal["target_dim"] if w.lit else pal["target"], (255, 255, 255), w.flash * 4)
            a, b = P(w.a), P(w.b)
            pygame.draw.line(s, col, a, b, 6)
            if not w.lit:
                pygame.draw.line(s, (255, 255, 255), (a[0] - 1, a[1] + 6), (b[0] - 1, b[1] - 6), 1)
        for w in t.standups:
            col = mix(pal["light_on"] if w.lit else pal["target"], (255, 255, 255), w.flash * 4)
            a, b = P(w.a), P(w.b)
            pygame.draw.line(s, col, a, b, 6)
            if w.label:
                side = -1 if w.a.x > PF_MID else 1
                self.text(w.label, self.font_small, pal["light_on"] if w.lit else C_TEXT_DIM,
                          ((a[0] + b[0]) / 2 + 14 * side, (a[1] + b[1]) / 2), "center")

        # Rollovers
        for i, r in enumerate(t.rollovers):
            mx, my = (r.a.x + r.b.x) / 2, (r.a.y + r.b.y) / 2
            lit = r.lit or (g.skill_active and g.skill_lane == i and blink)
            col = mix(pal["light_on"] if lit else pal["light_off"], (255, 255, 255), r.flash * 3)
            pygame.draw.circle(s, col, P((mx, my)), 9)
            pygame.draw.circle(s, pal["wall_dim"], P((mx, my)), 9, 1)

        # Agujeros
        for h in t.holes:
            pygame.draw.circle(s, mix((40, 36, 60), pal["light_on"], h.flash * 0.8), P(h.c), h.r)
            pygame.draw.circle(s, pal["wall_dim"], P(h.c), h.r, 2)
            pygame.draw.circle(s, mix(pal["light_off"], pal["light_on"], min(1.0, h.visits / 4)), P(h.c), 4)

        # Spinners: barra que gira en falso 3D
        for sp in t.spinners:
            mx, my = (sp.a.x + sp.b.x) / 2, (sp.a.y + sp.b.y) / 2
            half = (sp.b.x - sp.a.x) / 2
            w = abs(math.cos(sp.angle)) * half
            col = mix(pal["accent"], (255, 255, 255), sp.flash * 4)
            pygame.draw.line(s, pal["wall_dim"], P((sp.a.x, my)), P((sp.b.x, my)), 1)
            pygame.draw.line(s, col, P((mx - w, my)), P((mx + w, my)), 5)
            pygame.draw.circle(s, pal["wall"], P((mx, my)), 3)

        # Kickbacks
        for k in t.kickbacks:
            col = mix(pal["light_on"] if k.lit else pal["wall_dim"], (255, 255, 255), k.flash * 2)
            pygame.draw.rect(s, col, pygame.Rect(k.x - 7 + ox, k.y + oy, 14, 40))
            pygame.draw.line(s, pal["wall"], P((k.x - 11, k.y + 40)), P((k.x + 11, k.y + 40)), 3)

        # Postes y bumpers
        for p in t.posts:
            if p.name == "bumper":
                col = mix(pal["bumper"], pal["bumper_lit"], p.flash * 8)
                rr = p.r + (6 * p.flash / 0.12 if p.flash > 0 else 0)
                pygame.draw.circle(s, mix(pal["bumper"], pal["field"], 0.6), P(p.c), rr + 4)
                pygame.draw.circle(s, col, P(p.c), p.r)
                pygame.draw.circle(s, pal["bumper_lit"], P(p.c), p.r * 0.45)
            elif p.name == "rubber":
                pygame.draw.circle(s, mix(pal["sling"], (255, 255, 255), p.flash * 6), P(p.c), p.r)
                pygame.draw.circle(s, pal["wall"], P(p.c), p.r, 2)
            else:
                pygame.draw.circle(s, pal["wall"], P(p.c), p.r)
                if p.r >= 5:
                    pygame.draw.circle(s, pal["wall_dim"], P(p.c), p.r, 1)

        # Flippers
        for f in t.flippers:
            col = pal["flipper"] if (not g.tilt and g.state is State.PLAYING) else pal["flipper_dim"]
            a, b = P(f.pivot), P(f.tip())
            pygame.draw.circle(s, col, a, f.r_base)
            pygame.draw.circle(s, col, b, f.r_tip)
            d = (b[0] - a[0], b[1] - a[1])
            ln = math.hypot(*d) or 1.0
            n = (-d[1] / ln, d[0] / ln)
            pygame.draw.polygon(s, col, [
                (a[0] + n[0] * f.r_base, a[1] + n[1] * f.r_base), (b[0] + n[0] * f.r_tip, b[1] + n[1] * f.r_tip),
                (b[0] - n[0] * f.r_tip, b[1] - n[1] * f.r_tip), (a[0] - n[0] * f.r_base, a[1] - n[1] * f.r_base)])
            pygame.draw.circle(s, C_BG, a, 3)

        # Plunger
        ch = g.plunger_charge
        py0 = LANE_FLOOR_Y + 4 + 18 * ch
        pygame.draw.rect(s, mix(pal["wall"], pal["flipper"], ch), pygame.Rect(PLUNGER_X - 8 + ox, py0, 16, H - py0))
        pygame.draw.line(s, pal["wall_dim"], (LANE_X + ox, LANE_FLOOR_Y + oy), (520 + ox, LANE_FLOOR_Y + oy), 3)

        # Partículas
        for p in g.particles:
            k = clamp(p.life / 0.5, 0.0, 1.0)
            pygame.draw.circle(s, mix(pal["field"], p.color, k), P((p.x, p.y)), 2 + 2 * k)

        # Bola
        b = g.ball
        if b.alive and b.held:
            pygame.draw.circle(s, mix(C_BALL_SHADE, pal["field"], 0.5), P((b.x, b.y)), b.r - 2)
        elif b.alive:
            n = len(b.trail)
            for i, (tx, ty) in enumerate(b.trail):
                k = (i + 1) / (n + 1)
                pygame.draw.circle(s, mix(pal["field"], C_BALL_SHADE, k * 0.6), P((tx, ty)), b.r * k)
            pygame.draw.circle(s, C_BALL_SHADE, P((b.x, b.y)), b.r)
            pygame.draw.circle(s, C_BALL, P((b.x - 2, b.y - 2)), b.r - 3)
            if g.dragging:
                pygame.draw.circle(s, pal["accent"], P((b.x, b.y)), b.r + 6, 2)

        if g.debug:
            self.draw_debug(g, P)
        self.draw_hud(g, blink)

        if g.flash > 0:
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((255, 255, 255, int(120 * g.flash)))
            s.blit(ov, (0, 0))
        return s

    def draw_debug(self, g: Game, P) -> None:
        s = self.surf
        t = g.table
        for w in t.walls:
            pygame.draw.line(s, C_DEBUG, P(w.a), P(w.b), 1)
            mx, my = (w.a.x + w.b.x) / 2, (w.a.y + w.b.y) / 2
            pygame.draw.line(s, C_DEBUG_N, P((mx, my)), P((mx + w.nx * 12, my + w.ny * 12)), 1)
        for p in t.posts:
            pygame.draw.circle(s, C_DEBUG, P(p.c), p.r, 1)
        for a in t.arcs:
            pygame.draw.circle(s, C_DEBUG, P(a.c), a.R, 1)
        for se in t.sensors:
            pygame.draw.line(s, (0, 200, 255), P(se.a), P(se.b), 1)
        for sp in t.spinners:
            pygame.draw.line(s, (0, 200, 255), P(sp.a), P(sp.b), 1)
        for f in t.flippers:
            pygame.draw.line(s, C_DEBUG, P(f.pivot), P(f.tip()), 1)
            pygame.draw.circle(s, C_DEBUG, P(f.pivot), f.r_base, 1)
            pygame.draw.circle(s, C_DEBUG, P(f.tip()), f.r_tip, 1)
        b = g.ball
        if b.alive:
            pygame.draw.circle(s, C_DEBUG, P((b.x, b.y)), b.r, 1)
            pygame.draw.line(s, C_DEBUG_V, P((b.x, b.y)), P((b.x + b.vx * 0.08, b.y + b.vy * 0.08)), 2)
            self.text(f"v={b.speed():.0f} px/s  pos=({b.x:.0f},{b.y:.0f})  heat={g.tilt_heat:.2f}  g={g.gravity_scale:.2f}",
                      self.font_small, C_DEBUG, (24, H - 22))

    def draw_hud(self, g: Game, blink: bool) -> None:
        s = self.surf
        pal = g.pal
        pygame.draw.rect(s, C_BG, pygame.Rect(0, 0, W, HUD_H))
        pygame.draw.line(s, pal["wall_dim"], (0, HUD_H - 1), (W, HUD_H - 1), 1)
        self.text(f"{g.score:,}".replace(",", "."), self.font_big, C_TEXT, (18, 8))
        rec = f"RÉCORD {g.hiscore:,}".replace(",", ".") + ("   TRAMPA" if g.cheat else "")
        self.text(rec, self.font_small, pal["accent"] if g.cheat else C_TEXT_DIM, (18, 44))
        if g.state in (State.PLAYING, State.BALL_LOST):
            self.text(f"BOLA {g.ball_number}", self.font, C_TEXT, (W - 18, 10), "topright")
            self.text(f"x{g.multiplier}  BONUS {g.bonus:,}".replace(",", "."), self.font_small, C_TEXT_DIM, (W - 18, 42), "topright")
            for i in range(g.balls_left):
                pygame.draw.circle(s, C_BALL, (W - 150 - i * 14, 20), 5)
            if g.time < g.ball_save_until and g.ball.alive and not g.ball.in_lane() and blink:
                self.text("BALL SAVE", self.font_small, pal["light_on"], (W / 2, 50), "center")
            if g.tilt:
                self.text("TILT", self.font_big, pal["bumper"], (W / 2, HUD_H / 2 - 4), "center")
        if g.state in (State.ATTRACT, State.GAME_OVER):
            m = g.machine
            panel = pygame.Surface((W, 320), pygame.SRCALPHA)
            panel.fill((*C_BG, 215))
            s.blit(panel, (0, 440))
            pygame.draw.line(s, pal["wall_dim"], (20, 440), (520, 440), 1)
            pygame.draw.line(s, pal["wall_dim"], (20, 760), (520, 760), 1)
            if g.state is State.GAME_OVER:
                self.text("GAME OVER", self.font_title, pal["bumper"], (PF_MID, 500), "center")
                self.text(m.name, self.font, C_TEXT_DIM, (PF_MID, 560), "center")
            else:
                self.text(m.name, self.font_title, pal["flipper"], (PF_MID, 500), "center")
                self.text(m.subtitle, self.font, C_TEXT_DIM, (PF_MID, 560), "center")
            self.text(f"<  máquina {g.machine_index + 1} de {len(MACHINES)}  >   (flippers para cambiar)",
                      self.font_small, C_TEXT_DIM, (PF_MID, 592), "center")
            if blink:
                self.text("PULSA R O ENTER PARA JUGAR", self.font, C_TEXT, (PF_MID, 650), "center")
            self.text("Shift / Z / flechas = flippers   Espacio = lanzar   X C V = golpes",
                      self.font_small, C_TEXT_DIM, (PF_MID, 712), "center")
            self.text("M = música   F1 = debug   P = pausa", self.font_small, C_TEXT_DIM, (PF_MID, 736), "center")
        if g.message and g.time < g.message_until and g.state in (State.PLAYING, State.BALL_LOST):
            self.text(g.message, self.font_big, pal["light_on"], (PF_MID, 760), "center")
        elif g.message and g.time < g.message_until and g.cheat:
            self.text(g.message, self.font, pal["accent"], (PF_MID, 790), "center")
        if g.cheat and g.state is State.PLAYING:
            self.text("F2 gravedad  F3 plomada  F4 bola  F5 puntos  F6 lento  F7 bola al ratón  ratón arrastra",
                      self.font_small, pal["accent"], (PF_MID, H - 10), "center")
        if g.paused:
            self.text("PAUSA", self.font_big, C_TEXT, (PF_MID, 400), "center")


# =============================================================================
# 10. MAIN
# =============================================================================

def main(argv: list[str]) -> int:
    if "--dump-sounds" in argv:
        i = argv.index("--dump-sounds")
        folder = argv[i + 1] if len(argv) > i + 1 else "sounds"
        n = Sounds(enabled=False).dump(folder)
        print(f"{n} ficheros WAV en {folder}/")
        return 0
    headless = "--headless" in argv
    if headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    machine = 0
    if "--machine" in argv:
        try:
            machine = int(argv[argv.index("--machine") + 1]) % len(MACHINES)
        except (IndexError, ValueError):
            machine = 0
    pygame.init()
    pygame.display.set_caption("pynball")
    flags = pygame.SCALED | pygame.RESIZABLE
    try:
        screen = pygame.display.set_mode((W, H), flags)
    except pygame.error:
        screen = pygame.display.set_mode((W, H))
    sounds = Sounds(enabled="--mute" not in argv)
    game = Game(sounds, machine)
    renderer = Renderer()
    clock = pygame.time.Clock()
    acc = 0.0
    frames = 0
    max_frames = 0
    if headless:
        try:
            max_frames = int(argv[argv.index("--headless") + 1])
        except (IndexError, ValueError):
            max_frames = 600
        game.new_game()
        game.plunger_charge = 1.0
        game.launch()

    try:
        while True:
            dt = min(clock.tick(120) / 1000.0, MAX_FRAME_DT)
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    return 0
                if ev.type == pygame.KEYDOWN:
                    game.text_input(getattr(ev, "unicode", ""))
                    game.key_down(ev.key)
                elif ev.type == pygame.KEYUP:
                    game.key_up(ev.key)
                elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    game.mouse_down(ev.pos)
                elif ev.type == pygame.MOUSEMOTION:
                    game.mouse_move(ev.pos)
                elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                    game.mouse_up(ev.pos)
            if headless:
                dt = FIXED_DT
                frames += 1
                b = game.ball
                for f in game.table.flippers:
                    f.pressed = b.alive and b.y > 760 and b.vy > 0 and abs(b.x - f.pivot.x) < 120
                if frames >= max_frames:
                    print(f"headless ok: machine={game.machine.key} frames={frames} score={game.score} "
                          f"state={game.state.name} ball=({b.x:.0f},{b.y:.0f}) v={b.speed():.0f}")
                    return 0
            if not game.paused:
                acc += dt
                while acc >= FIXED_DT:
                    game.update(FIXED_DT)
                    acc -= FIXED_DT
            blink = (pygame.time.get_ticks() // 400) % 2 == 0
            screen.blit(renderer.draw(game, blink), (0, 0))
            pygame.display.flip()
    except SystemExit:
        return 0
    finally:
        game.save_hiscore()
        pygame.quit()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
