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

Controles: Shift izq / Z = flipper izquierdo; Shift der / "/" = flipper
derecho; Espacio = plunger (mantener y soltar); X C V = nudge; R = nueva
partida; F1 = debug; P = pausa; Esc = salir.
"""

from __future__ import annotations

import math
import os
import random
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

import pygame

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
NUDGE_VX = 150.0
NUDGE_VY = -80.0

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
# 5. TABLE
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
    sling_polys: list[list[Vec2]] = field(default_factory=list)

    def polyline(self, pts: list[Vec2], **kw) -> list[Wall]:
        out = [Wall(pts[i], pts[i + 1], **kw) for i in range(len(pts) - 1)]
        self.walls.extend(out)
        return out


def build_table() -> Table:
    t = Table()
    V = Vec2

    # --- Arco superior y paredes exteriores
    t.arcs.append(Arc(ARC_C, ARC_R))
    t.polyline([V(20, ARC_C.y), V(20, DRAIN_Y)])            # pared izquierda
    t.polyline([V(520, ARC_C.y), V(520, LANE_FLOOR_Y)])     # pared derecha exterior
    t.polyline([V(LANE_X, LANE_FLOOR_Y), V(520, LANE_FLOOR_Y)], e=0.2)   # suelo del carril
    t.polyline([V(LANE_X, 345), V(LANE_X, LANE_FLOOR_Y)])   # pared del carril
    # Puerta de un solo sentido a la salida del carril: pasa si sube
    t.walls.append(Wall(V(LANE_X, 345), V(515, 305), one_way=V(0, -1), name="gate", e=0.3))

    # --- Carriles superiores (rollovers)
    xs = [163.0, 219.0, 275.0, 331.0]
    for x in xs:
        top = arc_y(x) + 2 * BALL_RADIUS + 6      # hueco: la órbita rápida pasa por encima
        t.polyline([V(x, top), V(x, 180)], name="lane_divider")
        t.posts.append(Post(V(x, 180), 3.0))
        t.posts.append(Post(V(x, top), 3.0))
    for i in range(3):
        s = Sensor(V(xs[i] + 6, 150), V(xs[i + 1] - 6, 150), name="rollover", index=i)
        t.sensors.append(s)
        t.rollovers.append(s)

    # --- Bumpers
    for c in (V(177, 330), V(317, 330), V(247, 425)):
        b = Post(c, 24.0, e=BUMPER_E, kick=BUMPER_KICK, threshold=40.0, name="bumper")
        t.posts.append(b)
        t.bumpers.append(b)

    # --- Agujero kickout bajo el arco, a la izquierda
    t.holes.append(Hole(V(105, 262)))

    # --- Lado izquierdo y su espejo
    def side(m: bool) -> None:
        M = mirror if m else (lambda p: p)
        # diagonal desde la pared hasta el banco de dianas
        t.polyline([M(V(20, 345)), M(V(80, 455))])
        # banco de 3 dianas
        for i in range(3):
            y0, y1 = 455 + i * 52, 507 + i * 52
            w = Wall(M(V(80, y0)), M(V(80, y1)), e=0.45, threshold=120.0,
                     name="standup" if m else "target")
            t.walls.append(w)
            (t.standups if m else t.drop_targets).append(w)
        t.polyline([M(V(80, 611)), M(V(80, 640)), M(V(20, 640))])   # techo: cierra el hueco tras el banco
        t.posts.append(Post(M(V(80, 640)), 2.0))
        # poste y guía del inlane
        t.posts.append(Post(M(V(65, 685)), 7.0))
        # Guía: termina tangente a la cara superior del flipper en reposo
        piv = M(V(152, 850))
        ang = (math.pi - FLIPPER_REST) if m else FLIPPER_REST
        d = V(math.cos(ang), math.sin(ang))
        n = V(d.y, -d.x) if not m else V(-d.y, d.x)   # normal hacia arriba
        if n.y > 0:
            n = n * -1
        guide_end = piv + n * FLIPPER_R_BASE
        t.polyline([M(V(65, 690)), M(V(65, 792)), guide_end], e=0.3)
        # slingshot: triángulo; la hipotenusa dispara
        A, B, C = M(V(105, 690)), M(V(105, 780)), M(V(150, 790))
        t.sling_polys.append([A, B, C])
        t.polyline([A, B, C], e=0.4)
        face = Wall(C, A, e=SLING_E, kick=SLING_KICK, threshold=SLING_THRESHOLD, name="sling") \
            if not m else Wall(A, C, e=SLING_E, kick=SLING_KICK, threshold=SLING_THRESHOLD, name="sling")
        # la normal debe apuntar hacia el centro del campo
        if (face.nx > 0) != (not m):
            face = Wall(face.b, face.a, e=SLING_E, kick=SLING_KICK, threshold=SLING_THRESHOLD, name="sling")
        t.walls.append(face)
        t.slings.append(face)
        # flipper
        f = Flipper(piv, ang, (math.pi - FLIPPER_UP) if m else FLIPPER_UP)
        t.flippers.append(f)

    side(False)
    side(True)
    return t


# =============================================================================
# 6. FÍSICA
# =============================================================================

class Physics:
    """Avanza la bola un subpaso y resuelve contactos. Las reacciones de
    juego (puntos, luces, sonido) salen por `events`."""

    def __init__(self, table: Table) -> None:
        self.t = table
        self.events: list[tuple[str, object, float]] = []

    def substep(self, ball: Ball, dt: float, flippers_enabled: bool) -> None:
        t = self.t
        for f in t.flippers:
            f.step(dt, flippers_enabled)

        if not ball.alive or ball.held:
            return

        # Integración semi-implícita
        ball.vy += GRAVITY * dt
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
                    ball.bounce(hit[0], hit[1], hit[2], a.e, a.mu)
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
                if w.name and impact > w.threshold:
                    self.events.append((w.name, w, impact))
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
                if p.name and (impact > p.threshold or kick > 0):
                    self.events.append((p.name, p, max(impact, 1.0)))
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

        # Desagüe
        if ball.y > DRAIN_Y:
            ball.alive = False
            self.events.append(("drain", None, 0.0))


def impact_ok(ball: Ball, nx: float, ny: float, threshold: float) -> bool:
    """¿La bola se acerca con suficiente velocidad normal para disparar un kick?"""
    return -(ball.vx * nx + ball.vy * ny) > threshold


# =============================================================================
# 7. SONIDO
# =============================================================================

class Sounds:
    """Sintetiza unos pocos buffers al arrancar. Si no hay numpy o el mixer
    falla, play() no hace nada: el juego nunca muere por el audio."""

    RATE = 22050

    def __init__(self) -> None:
        self.bank: dict[str, pygame.mixer.Sound] = {}
        self.ok = False
        try:
            import numpy as np
            pygame.mixer.quit()
            pygame.mixer.init(self.RATE, -16, 1, 512)
            init = pygame.mixer.get_init()
            if not init:
                return
            self.np = np
            self.channels = init[2]
            self._build()
            self.ok = True
        except Exception:
            self.ok = False

    def _tone(self, dur: float, f0: float, f1: float | None = None, wave: str = "sine",
              decay: float = 8.0, vol: float = 0.5):
        np = self.np
        n = int(self.RATE * dur)
        tt = np.linspace(0.0, dur, n, endpoint=False)
        if f1 is None:
            f1 = f0
        freq = f0 + (f1 - f0) * (tt / dur)
        phase = 2 * np.pi * np.cumsum(freq) / self.RATE
        match wave:
            case "sine":
                y = np.sin(phase)
            case "square":
                y = np.sign(np.sin(phase))
            case "tri":
                y = 2.0 * np.abs(2.0 * ((phase / (2 * np.pi)) % 1.0) - 1.0) - 1.0
            case _:
                y = np.random.uniform(-1.0, 1.0, n)
        env = np.exp(-decay * tt)
        return y * env * vol

    def _add(self, name: str, *parts) -> None:
        np = self.np
        n = max(len(p) for p in parts)
        buf = np.zeros(n)
        for p in parts:
            buf[: len(p)] += p
        buf = np.clip(buf, -1.0, 1.0)
        arr = (buf * 32767).astype(np.int16)
        if self.channels > 1:
            arr = np.repeat(arr[:, None], self.channels, axis=1)
        self.bank[name] = pygame.sndarray.make_sound(np.ascontiguousarray(arr))

    def _build(self) -> None:
        T = self._tone
        self._add("flipper", T(0.03, 0, wave="noise", decay=90, vol=0.35))
        self._add("bumper", T(0.09, 330, 120, decay=28, vol=0.6), T(0.03, 0, wave="noise", decay=120, vol=0.2))
        self._add("sling", T(0.07, 520, 400, wave="tri", decay=35, vol=0.45))
        self._add("target", T(0.14, 660, decay=18, vol=0.35), T(0.14, 990, decay=18, vol=0.25))
        self._add("rollover", T(0.12, 880, 1320, decay=20, vol=0.3))
        self._add("drain", T(0.45, 800, 90, wave="tri", decay=5, vol=0.5))
        self._add("tilt", T(0.6, 110, wave="square", decay=4, vol=0.4) * self._gate(0.6, 12))
        self._add("launch", T(0.2, 200, 900, decay=10, vol=0.4))
        self._add("bonus", T(0.1, 523, decay=10, vol=0.3), T(0.1, 659, decay=10, vol=0.3), T(0.1, 784, decay=10, vol=0.3))
        self._add("extra", T(0.5, 440, 880, decay=4, vol=0.4), T(0.5, 660, 1320, decay=4, vol=0.3))
        self._add("mult", T(0.25, 392, 784, wave="tri", decay=8, vol=0.4))
        self._add("warn", T(0.15, 220, 180, wave="square", decay=12, vol=0.3))

    def _gate(self, dur: float, hz: float):
        np = self.np
        n = int(self.RATE * dur)
        tt = np.linspace(0.0, dur, n, endpoint=False)
        return (np.sin(2 * np.pi * hz * tt) > 0).astype(float)

    def play(self, name: str) -> None:
        if self.ok and name in self.bank:
            try:
                self.bank[name].play()
            except Exception:
                pass


# =============================================================================
# 8. REGLAS Y MÁQUINA DE ESTADOS
# =============================================================================

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
    def __init__(self, sounds: Sounds) -> None:
        self.table = build_table()
        self.phys = Physics(self.table)
        self.snd = sounds
        self.state = State.ATTRACT
        self.time = 0.0
        self.timers: list[tuple[float, object]] = []
        self.ball = Ball(PLUNGER_X, 900.0)
        self.ball.alive = False
        self.score = 0
        self.hiscore = self.load_hiscore()
        self.balls_left = 0
        self.ball_number = 0
        self.bonus = 0
        self.multiplier = 1
        self.extra_ball_given = False
        self.tilt = False
        self.tilt_heat = 0.0
        self.tilt_warned = False
        self.ball_save_until = 0.0
        self.launch_time = -1.0
        self.skill_lane = -1
        self.skill_active = False
        self.plunger_charge = 0.0
        self.plunger_held = False
        self.combo = 0
        self.combo_time = 0.0
        self.message = ""
        self.message_until = 0.0
        self.shake = 0.0
        self.shake_x = 0.0
        self.particles: list[Particle] = []
        self.debug = False
        self.paused = False
        self.flash = 0.0

    # --- utilidades -------------------------------------------------------
    @staticmethod
    def load_hiscore() -> int:
        try:
            return int(HISCORE_FILE.read_text().strip() or "0")
        except Exception:
            return 0

    def save_hiscore(self) -> None:
        try:
            HISCORE_FILE.write_text(str(self.hiscore))
        except Exception:
            pass

    def after(self, delay: float, fn) -> None:
        self.timers.append((self.time + delay, fn))

    def say(self, text: str, dur: float = 1.6) -> None:
        self.message = text
        self.message_until = self.time + dur

    def add_score(self, pts: int) -> None:
        if self.tilt or self.state is not State.PLAYING:
            return
        self.score += pts * self.multiplier
        if self.score > self.hiscore:
            self.hiscore = self.score
        if not self.extra_ball_given and self.score >= EXTRA_BALL_SCORE:
            self.extra_ball_given = True
            self.balls_left += 1
            self.say("EXTRA BALL", 2.5)
            self.snd.play("extra")
            self.flash = 0.4

    def spawn_particles(self, x: float, y: float, n: int, color: tuple[int, int, int], speed: float = 260.0) -> None:
        for _ in range(n):
            a = random.uniform(0, math.tau)
            s = random.uniform(0.3, 1.0) * speed
            self.particles.append(Particle(x, y, math.cos(a) * s, math.sin(a) * s, random.uniform(0.25, 0.55), color))

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
            w.active, w.lit = True, False
        for w in self.table.standups:
            w.lit = False
        for s in self.table.rollovers:
            s.lit = False
        for h in self.table.holes:
            h.visits = 0
        self.state = State.PLAYING
        self.next_ball()

    def next_ball(self) -> None:
        if self.balls_left <= 0:
            self.state = State.GAME_OVER
            self.save_hiscore()
            self.say("GAME OVER", 4.0)
            return
        self.balls_left -= 1
        self.ball_number += 1
        self.bonus = 0
        self.tilt = False
        self.tilt_heat = 0.0
        self.tilt_warned = False
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
        self.snd.play("launch")
        if self.launch_time < 0:
            self.launch_time = self.time
            self.ball_save_until = self.time + BALL_SAVE_S
            self.skill_lane = random.randrange(3)
            self.skill_active = True
            self.say("SKILL SHOT", 1.2)

    def nudge(self, dx: float) -> None:
        if self.state is not State.PLAYING or self.tilt or not self.ball.alive or self.ball.in_lane():
            return
        self.tilt_heat += TILT_HEAT_PER_NUDGE
        self.ball.vx += dx * NUDGE_VX
        self.ball.vy += NUDGE_VY
        self.shake = 0.18
        self.shake_x = dx
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

    def lane_change(self) -> None:
        """Pulsar un flipper rota las luces de los carriles superiores."""
        r = self.table.rollovers
        if any(s.inside for s in r):
            return
        lits = [s.lit for s in r]
        lits = [lits[-1]] + lits[:-1]
        for s, v in zip(r, lits):
            s.lit = v

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
                    self.snd.play("bumper")
                    self.spawn_particles(obj.c.x, obj.c.y, 10, C_BUMPER_LIT)
                    if self.combo >= 5 and self.combo % 5 == 0:
                        self.say(f"COMBO x{self.combo}", 0.8)
                case "sling":
                    assert isinstance(obj, Wall)
                    obj.flash = 0.1
                    self.add_score(50)
                    self.snd.play("sling")
                    mx, my = (obj.a.x + obj.b.x) / 2, (obj.a.y + obj.b.y) / 2
                    self.spawn_particles(mx, my, 6, C_SLING_LIT, 180)
                case "target":
                    assert isinstance(obj, Wall)
                    if obj.active and not obj.lit:
                        obj.lit = True
                        obj.flash = 0.2
                        self.add_score(500)
                        self.bonus += 1000
                        self.snd.play("target")
                        if all(w.lit for w in self.table.drop_targets):
                            self.add_score(5000)
                            self.say("BANCO 5000", 1.5)
                            self.snd.play("bonus")
                            self.after(1.0, self.reset_drop_targets)
                case "standup":
                    assert isinstance(obj, Wall)
                    obj.flash = 0.2
                    if not obj.lit:
                        obj.lit = True
                        self.add_score(300)
                        self.bonus += 500
                        self.snd.play("target")
                        if all(w.lit for w in self.table.standups):
                            self.add_score(3000)
                            self.say("P-Y-N 3000", 1.5)
                            self.snd.play("bonus")
                            self.after(1.0, self.reset_standups)
                    else:
                        self.add_score(50)
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
                            self.snd.play("bonus")
                            self.flash = 0.3
                    self.add_score(pts)
                    self.snd.play("rollover")
                    obj.lit = True
                    if all(s.lit for s in self.table.rollovers):
                        for s in self.table.rollovers:
                            s.lit = False
                        if self.multiplier < 5:
                            self.multiplier += 1
                            self.say(f"MULTIPLICADOR x{self.multiplier}", 1.5)
                        else:
                            self.add_score(10000)
                            self.say("CARRILES 10000", 1.5)
                        self.snd.play("mult")
                case "hole":
                    assert isinstance(obj, Hole)
                    obj.visits += 1
                    obj.flash = 1.2
                    award = min(obj.visits, 5) * 1000
                    self.add_score(award)
                    self.bonus += 2000
                    self.say(f"AGUJERO {award}", 1.2)
                    self.snd.play("bonus")
                    self.flash = 0.2
                    self.after(1.2, lambda h=obj: self.eject(h))
                case "flipper_hit":
                    self.combo = 0
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
        self.snd.play("launch")
        self.spawn_particles(h.c.x, h.c.y, 8, C_LIGHT_ON, 200)

    def reset_drop_targets(self) -> None:
        for w in self.table.drop_targets:
            w.lit = False
            w.flash = 0.3

    def reset_standups(self) -> None:
        for w in self.table.standups:
            w.lit = False
            w.flash = 0.3

    def on_drain(self) -> None:
        self.snd.play("drain")
        if self.state is not State.PLAYING:
            return
        if self.time < self.ball_save_until and not self.tilt:
            self.say("BALL SAVE", 1.5)
            self.after(0.8, self.ball_saved)
            return
        self.state = State.BALL_LOST
        earned = 0 if self.tilt else self.bonus * self.multiplier
        if earned:
            self.score += earned
            if self.score > self.hiscore:
                self.hiscore = self.score
            self.say(f"BONUS {self.bonus} x{self.multiplier}", 2.0)
            self.snd.play("bonus")
        elif self.tilt:
            self.say("TILT: SIN BONUS", 2.0)
        self.after(2.2, self.next_ball)

    def ball_saved(self) -> None:
        if self.state is State.PLAYING:
            self.reset_ball_to_plunger()
            self.ball_save_until = self.time + 3.0
            self.launch_time = self.time   # no reactivar skill shot

    # --- entrada ----------------------------------------------------------
    def key_down(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            raise SystemExit
        if key == pygame.K_F1:
            self.debug = not self.debug
            return
        if key == pygame.K_p:
            self.paused = not self.paused
            return
        if key == pygame.K_r:
            self.new_game()
            return
        if self.state is not State.PLAYING:
            return
        enabled = not self.tilt
        if key in (pygame.K_LSHIFT, pygame.K_z):
            self.table.flippers[0].pressed = True
            if enabled:
                self.snd.play("flipper")
                self.lane_change()
        elif key in (pygame.K_RSHIFT, pygame.K_SLASH):
            self.table.flippers[1].pressed = True
            if enabled:
                self.snd.play("flipper")
                self.lane_change()
        elif key == pygame.K_SPACE:
            self.plunger_held = True
            self.plunger_charge = 0.0
        elif key == pygame.K_x:
            self.nudge(-1.0)
        elif key == pygame.K_v:
            self.nudge(1.0)
        elif key == pygame.K_c:
            self.nudge(0.0)

    def key_up(self, key: int) -> None:
        if key in (pygame.K_LSHIFT, pygame.K_z):
            self.table.flippers[0].pressed = False
        elif key in (pygame.K_RSHIFT, pygame.K_SLASH):
            self.table.flippers[1].pressed = False
        elif key == pygame.K_SPACE:
            if self.plunger_held:
                self.plunger_held = False
                self.launch()
                self.plunger_charge = 0.0

    # --- actualización ----------------------------------------------------
    def update(self, dt: float) -> None:
        """Un paso fijo de física + reglas."""
        self.time += dt
        # Timers
        due = [tm for tm in self.timers if tm[0] <= self.time]
        if due:
            self.timers = [tm for tm in self.timers if tm[0] > self.time]
            for _, fn in due:
                fn()

        if self.plunger_held:
            self.plunger_charge = clamp(self.plunger_charge + dt / PLUNGER_CHARGE_S, 0.0, 1.0)

        self.tilt_heat = max(0.0, self.tilt_heat - TILT_HEAT_DECAY * dt)
        if self.skill_active and self.time - self.launch_time > SKILL_SHOT_S:
            self.skill_active = False

        playing = self.state in (State.PLAYING, State.BALL_LOST)
        sub = dt / SUBSTEPS
        flippers_on = not self.tilt and self.state is State.PLAYING
        for _ in range(SUBSTEPS):
            self.phys.substep(self.ball, sub, flippers_on)
        self.handle_events()

        # Bola atascada: pequeño empujón aleatorio
        b = self.ball
        held = any(f.pressed for f in self.table.flippers)
        if playing and b.alive and not b.held and not b.in_lane() and not held:
            if b.speed() < 12.0:
                b.still_time += dt
                if b.still_time > STUCK_S:
                    b.vx += random.choice((-1, 1)) * 90.0
                    b.vy -= 160.0
                    b.still_time = 0.0
            else:
                b.still_time = 0.0

        # Estela
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


# =============================================================================
# 9. RENDER
# =============================================================================

class Renderer:
    def __init__(self) -> None:
        self.surf = pygame.Surface((W, H))
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
        s.fill(C_BG)
        ox = 0.0
        if g.shake > 0:
            ox = math.sin(g.shake * 60) * 6 * g.shake_x if g.shake_x else math.sin(g.shake * 60) * 3
        oy = 0.0 if g.shake_x else (math.sin(g.shake * 50) * 5 if g.shake > 0 else 0.0)

        def P(v: Vec2 | tuple[float, float]) -> tuple[float, float]:
            if isinstance(v, Vec2):
                return (v.x + ox, v.y + oy)
            return (v[0] + ox, v[1] + oy)

        # Campo de juego: relleno del arco y rectángulo
        pygame.draw.circle(s, C_FIELD, P(ARC_C), ARC_R)
        pygame.draw.rect(s, C_FIELD, pygame.Rect(20 + ox, ARC_C.y + oy, 500, H - ARC_C.y))
        pygame.draw.rect(s, C_BG, pygame.Rect(0, 0, W, HUD_H))

        # Arco
        pygame.draw.circle(s, C_WALL, P(ARC_C), ARC_R, 3)
        pygame.draw.rect(s, C_FIELD, pygame.Rect(0 + ox, ARC_C.y + oy, W, H))
        pygame.draw.rect(s, C_BG, pygame.Rect(0, ARC_C.y + oy, 20 + ox, H))
        pygame.draw.rect(s, C_BG, pygame.Rect(520 + ox, ARC_C.y + oy, W, H))

        # Slingshots (relleno)
        for poly, face in zip(t.sling_polys, t.slings):
            col = mix(C_SLING, C_SLING_LIT, face.flash * 8)
            pygame.draw.polygon(s, col, [P(v) for v in poly])

        # Paredes
        for w in t.walls:
            if w.name in ("target", "standup"):
                continue
            if w.name == "sling":
                continue
            col = C_WALL
            width = 3
            if w.name == "gate":
                col = C_WALL_DIM
                width = 2
            if w.name == "lane_divider":
                width = 4
            pygame.draw.line(s, col, P(w.a), P(w.b), width)

        # Dianas
        for w in t.drop_targets:
            col = C_TARGET_DIM if w.lit else C_TARGET
            col = mix(col, (255, 255, 255), w.flash * 4)
            a, b = P(w.a), P(w.b)
            pygame.draw.line(s, col, a, b, 6)
            if not w.lit:
                pygame.draw.line(s, (255, 255, 255), (a[0] - 1, a[1] + 6), (b[0] - 1, b[1] - 6), 1)
        for i, w in enumerate(t.standups):
            col = C_LIGHT_ON if w.lit else C_TARGET
            col = mix(col, (255, 255, 255), w.flash * 4)
            a, b = P(w.a), P(w.b)
            pygame.draw.line(s, col, a, b, 6)
            self.text("PYN"[i], self.font_small, C_LIGHT_ON if w.lit else C_TEXT_DIM,
                      ((a[0] + b[0]) / 2 - 14, (a[1] + b[1]) / 2), "center")

        # Rollovers
        for i, r in enumerate(t.rollovers):
            mx, my = (r.a.x + r.b.x) / 2, (r.a.y + r.b.y) / 2
            lit = r.lit or (g.skill_active and g.skill_lane == i and blink)
            col = C_LIGHT_ON if lit else C_LIGHT_OFF
            col = mix(col, (255, 255, 255), r.flash * 3)
            pygame.draw.circle(s, col, P((mx, my)), 9)
            pygame.draw.circle(s, C_WALL_DIM, P((mx, my)), 9, 1)

        # Agujeros
        for h in t.holes:
            col = mix((40, 36, 60), C_LIGHT_ON, h.flash * 0.8)
            pygame.draw.circle(s, col, P(h.c), h.r)
            pygame.draw.circle(s, C_WALL_DIM, P(h.c), h.r, 2)
            pygame.draw.circle(s, mix(C_LIGHT_OFF, C_LIGHT_ON, min(1.0, h.visits / 4)), P(h.c), 4)

        # Postes y bumpers
        for p in t.posts:
            if p.name == "bumper":
                col = mix(C_BUMPER, C_BUMPER_LIT, p.flash * 8)
                rr = p.r + (6 * p.flash / 0.12 if p.flash > 0 else 0)
                pygame.draw.circle(s, mix(C_BUMPER, C_FIELD, 0.6), P(p.c), rr + 4)
                pygame.draw.circle(s, col, P(p.c), p.r)
                pygame.draw.circle(s, C_BUMPER_LIT, P(p.c), p.r * 0.45)
            else:
                pygame.draw.circle(s, C_WALL, P(p.c), p.r)

        # Flippers
        for f in t.flippers:
            col = C_FLIPPER if (not g.tilt and g.state is State.PLAYING) else C_FLIPPER_DIM
            a, b = P(f.pivot), P(f.tip())
            pygame.draw.circle(s, col, a, f.r_base)
            pygame.draw.circle(s, col, b, f.r_tip)
            d = (b[0] - a[0], b[1] - a[1])
            ln = math.hypot(*d) or 1.0
            n = (-d[1] / ln, d[0] / ln)
            poly = [(a[0] + n[0] * f.r_base, a[1] + n[1] * f.r_base),
                    (b[0] + n[0] * f.r_tip, b[1] + n[1] * f.r_tip),
                    (b[0] - n[0] * f.r_tip, b[1] - n[1] * f.r_tip),
                    (a[0] - n[0] * f.r_base, a[1] - n[1] * f.r_base)]
            pygame.draw.polygon(s, col, poly)
            pygame.draw.circle(s, C_BG, a, 3)

        # Plunger
        ch = g.plunger_charge
        py0 = LANE_FLOOR_Y + 4 + 18 * ch
        pygame.draw.rect(s, mix(C_WALL, C_FLIPPER, ch), pygame.Rect(PLUNGER_X - 8 + ox, py0, 16, H - py0))
        pygame.draw.line(s, C_WALL_DIM, (LANE_X + ox, LANE_FLOOR_Y + oy), (520 + ox, LANE_FLOOR_Y + oy), 3)

        # Partículas
        for p in g.particles:
            k = clamp(p.life / 0.5, 0.0, 1.0)
            pygame.draw.circle(s, mix(C_FIELD, p.color, k), P((p.x, p.y)), 2 + 2 * k)

        # Bola con estela
        b = g.ball
        if b.alive:
            n = len(b.trail)
            for i, (tx, ty) in enumerate(b.trail):
                k = (i + 1) / (n + 1)
                pygame.draw.circle(s, mix(C_FIELD, C_BALL_SHADE, k * 0.6), P((tx, ty)), b.r * k)
            pygame.draw.circle(s, C_BALL_SHADE, P((b.x, b.y)), b.r)
            pygame.draw.circle(s, C_BALL, P((b.x - 2, b.y - 2)), b.r - 3)

        # Debug
        if g.debug:
            self.draw_debug(g, P)

        # HUD
        self.draw_hud(g, blink)

        # Flash
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
        for f in t.flippers:
            pygame.draw.line(s, C_DEBUG, P(f.pivot), P(f.tip()), 1)
            pygame.draw.circle(s, C_DEBUG, P(f.pivot), f.r_base, 1)
            pygame.draw.circle(s, C_DEBUG, P(f.tip()), f.r_tip, 1)
        b = g.ball
        if b.alive:
            pygame.draw.circle(s, C_DEBUG, P((b.x, b.y)), b.r, 1)
            pygame.draw.line(s, C_DEBUG_V, P((b.x, b.y)), P((b.x + b.vx * 0.08, b.y + b.vy * 0.08)), 2)
            self.text(f"v={b.speed():.0f} px/s  pos=({b.x:.0f},{b.y:.0f})  heat={g.tilt_heat:.2f}",
                      self.font_small, C_DEBUG, (24, H - 22))

    def draw_hud(self, g: Game, blink: bool) -> None:
        s = self.surf
        pygame.draw.rect(s, C_BG, pygame.Rect(0, 0, W, HUD_H))
        pygame.draw.line(s, C_WALL_DIM, (0, HUD_H - 1), (W, HUD_H - 1), 1)
        self.text(f"{g.score:,}".replace(",", "."), self.font_big, C_TEXT, (18, 8))
        self.text(f"RÉCORD {g.hiscore:,}".replace(",", "."), self.font_small, C_TEXT_DIM, (18, 44))
        if g.state in (State.PLAYING, State.BALL_LOST):
            self.text(f"BOLA {g.ball_number}", self.font, C_TEXT, (W - 18, 10), "topright")
            self.text(f"x{g.multiplier}  BONUS {g.bonus:,}".replace(",", "."), self.font_small, C_TEXT_DIM, (W - 18, 42), "topright")
            if g.balls_left:
                for i in range(g.balls_left):
                    pygame.draw.circle(s, C_BALL, (W - 150 - i * 14, 20), 5)
            if g.time < g.ball_save_until and g.ball.alive and not g.ball.in_lane() and blink:
                self.text("BALL SAVE", self.font_small, C_LIGHT_ON, (W / 2, 50), "center")
            if g.tilt:
                self.text("TILT", self.font_big, C_BUMPER, (W / 2, HUD_H / 2 - 4), "center")
        if g.state is State.ATTRACT:
            self.text("PYNBALL", self.font_title, C_FLIPPER, (W / 2, 520), "center")
            self.text("un pinball en un fichero", self.font, C_TEXT_DIM, (W / 2, 580), "center")
            if blink:
                self.text("PULSA R PARA JUGAR", self.font, C_TEXT, (W / 2, 660), "center")
            self.text("Shift / Z /  = flippers   Espacio = lanzar   X C V = nudge",
                      self.font_small, C_TEXT_DIM, (W / 2, 720), "center")
        if g.state is State.GAME_OVER:
            self.text("GAME OVER", self.font_title, C_BUMPER, (W / 2, 520), "center")
            if blink:
                self.text("PULSA R", self.font, C_TEXT, (W / 2, 600), "center")
        if g.message and g.time < g.message_until and g.state in (State.PLAYING, State.BALL_LOST):
            self.text(g.message, self.font_big, C_LIGHT_ON, (W / 2 - 23, 760), "center")
        if g.paused:
            self.text("PAUSA", self.font_big, C_TEXT, (W / 2, 400), "center")


# =============================================================================
# 10. MAIN
# =============================================================================

def main(argv: list[str]) -> int:
    headless = "--headless" in argv
    if headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.init()
    pygame.display.set_caption("pynball")
    flags = pygame.SCALED | pygame.RESIZABLE
    try:
        screen = pygame.display.set_mode((W, H), flags)
    except pygame.error:
        screen = pygame.display.set_mode((W, H))
    sounds = Sounds()
    game = Game(sounds)
    renderer = Renderer()
    clock = pygame.time.Clock()
    acc = 0.0
    frames = 0
    max_frames = 0
    if headless:
        # --headless [N]: simula N frames sin ventana y sale
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
                    game.key_down(ev.key)
                elif ev.type == pygame.KEYUP:
                    game.key_up(ev.key)
            if headless:
                dt = FIXED_DT
                frames += 1
                # bot tonto: pulsa los flippers cuando la bola baja cerca
                b = game.ball
                for f in game.table.flippers:
                    f.pressed = b.alive and b.y > 760 and b.vy > 0 and abs(b.x - f.pivot.x) < 120
                if frames >= max_frames:
                    print(f"headless ok: frames={frames} score={game.score} state={game.state.name} "
                          f"ball=({b.x:.0f},{b.y:.0f}) v={b.speed():.0f}")
                    return 0
            if not game.paused:
                acc += dt
                while acc >= FIXED_DT:
                    game.update(FIXED_DT)
                    acc -= FIXED_DT
            blink = (pygame.time.get_ticks() // 400) % 2 == 0
            frame = renderer.draw(game, blink)
            screen.blit(frame, (0, 0))
            pygame.display.flip()
    except SystemExit:
        return 0
    finally:
        pygame.quit()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
