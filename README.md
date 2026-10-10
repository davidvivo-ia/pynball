# pynball

Un pinball completo en **un solo fichero**: `pynball.py`. Python moderno, `pygame-ce`, cero motor de física externo. Pensado para leerse de arriba abajo en una tarde, como `winball` en C, pero con las lecciones de los pinballs grandes.

Este README es el documento de diseño: qué se construye, por qué así, y qué se roba de cada referencia. Se escribe **antes** del código para que el código no se desvíe.

---

## 1. Qué es y qué no es

**Es**

- Un juego jugable: plunger, dos flippers, bumpers, slingshots, dianas, rampa de lanzamiento, desagüe, 3 bolas, puntuación y récord.
- Un único `pynball.py` de ~800-1200 líneas, sin assets externos. Sonido generado por síntesis, mesa definida como datos dentro del propio fichero.
- Física propia: bola circular contra segmentos, arcos y flippers rotatorios. Legible, determinista, sin dependencias.

**No es**

- Un port de Space Cadet. Ese código es la referencia de *cómo* se hizo un pinball comercial, no un objetivo de fidelidad.
- Un framework tipo Visual Pinball o FreeWPC. Nada de scripting de mesas, nada de emular hardware.
- Un escaparate de Box2D. Delegar la física es legítimo (Pokemon-Pinball, CITM-UPC, BBC BASIC lo hacen), pero aquí la física **es** el juego, y el objetivo didáctico es verla entera.

---

## 2. Requisitos

| Componente | Versión | Nota |
|---|---|---|
| Python | 3.12 o superior | `match`, `dataclass(slots=True)`, type hints modernos sin `typing` legacy |
| pygame-ce | 2.5 o superior | Fork comunitario activo de pygame. `pip install pygame-ce` |
| numpy | opcional | Solo para sintetizar sonido. Si falta, el juego corre mudo |

```bash
python -m venv .venv
source .venv/bin/activate
pip install pygame-ce numpy
python pynball.py
```

Linux, macOS y Windows. En Linux con Wayland, si la ventana no abre, exporta `SDL_VIDEODRIVER=x11`.

---

## 3. Controles

| Tecla | Acción |
|---|---|
| `Shift Izq` / `Z` | Flipper izquierdo |
| `Shift Der` / `/` | Flipper derecho |
| `Espacio` (mantener) | Cargar plunger; soltar lanza |
| `X`, `C`, `V` | Nudge izquierda, abajo, derecha. Tres nudges en 2 s: **TILT** |
| `R` | Nueva partida |
| `F1` | Mostrar colisionadores y vectores de velocidad |
| `P` | Pausa |
| `Esc` | Salir |

---

## 4. Arquitectura del fichero

Un fichero, pero con secciones numeradas y separadas por banners de comentario. El orden importa: cada sección solo depende de las anteriores.

```
pynball.py
├── 0. Cabecera: docstring, imports, constantes (tamaño, gravedad, dt)
├── 1. Vec2: dataclass mínima con +, -, *, dot, cross, length, normalized, rotated
├── 2. Geometría de colisión: funciones puras
│      closest_point_on_segment, circle_vs_segment, circle_vs_circle, circle_vs_arc
├── 3. Elementos de mesa (dataclasses)
│      Wall, Arc, Bumper, Slingshot, Target, Flipper, Plunger, Drain, Lane
├── 4. Ball: posición, velocidad, radio, integración, respuesta a colisión
├── 5. Table: lista de elementos + rutina build_table() que la construye a mano
├── 6. Física: step(dt) con substeps, resolución de colisiones, fricción, restitución
├── 7. Reglas: puntuación, bolas, bonus, tilt, multiplicador, extra ball
├── 8. Sonido: síntesis de ondas en memoria (numpy) con fallback silencioso
├── 9. Render: dibujar mesa, bola, HUD, modo debug
├── 10. Game: máquina de estados (ATTRACT, PLAYING, BALL_LOST, GAME_OVER) y bucle principal
└── 11. if __name__ == "__main__": main()
```

**Por qué un solo fichero.** Es la restricción que impone claridad. `winball` cabe en pocos ficheros de C porque no tiene abstracciones que no necesite. Un pinball no es un proyecto grande: es un problema de física 2D con reglas encima. Si no cabe en un fichero, hay abstracción sobrante.

---

## 5. Física: decisiones y por qué

Esta es la sección que justifica el proyecto. Lo que sigue es lo que enseña leer Space Cadet, winball y los proyectos con Box2D.

### 5.1 Paso de tiempo fijo con substeps

```
FIXED_DT = 1 / 120          # física a 120 Hz
SUBSTEPS = 4                # 4 subpasos por frame de física = 480 Hz efectivos
```

Una bola de pinball a velocidad alta recorre varios píxeles por frame. Si se integra a 60 Hz sin más, atraviesa paredes finas y flippers. Space Cadet resuelve esto con detección continua: calcula el tiempo exacto de impacto dentro del frame. Es más correcto pero el código se dispara. Los substeps son la solución barata que mantiene todo legible: cuatro integraciones pequeñas por frame y una comprobación de solapamiento tras cada una. Para una bola de radio 8 px y velocidad máxima 1500 px/s, cuatro substeps a 120 Hz dan un desplazamiento de ~3 px por subpaso, menor que el radio. Suficiente.

Acumulador de tiempo clásico: el render va a lo que dé la pantalla, la física avanza en pasos fijos. Determinismo gratis.

### 5.2 Primitivas de colisión

Solo tres: **bola contra segmento**, **bola contra círculo**, **bola contra arco**. Todo lo demás se compone de ellas.

- Paredes: segmentos.
- Bumpers, postes: círculos.
- Curvas superiores de la mesa, guías de rampa: arcos (círculo con rango angular).
- Flippers: un segmento que rota alrededor de su pivote, con un círculo en cada extremo. Es exactamente lo que hace Space Cadet (`TFlipperEdge`): la cápsula es un segmento gordo.
- Slingshots: segmento con restitución alta y kick fijo en la normal.

Respuesta a colisión en dos pasos y siempre en este orden:

1. **Corrección posicional**: sacar la bola fuera del sólido a lo largo de la normal, exactamente la penetración más un epsilon. Sin esto la bola "tiembla" dentro de las paredes.
2. **Reflexión de velocidad**: descomponer en normal y tangencial. Normal invertida y escalada por restitución del elemento. Tangencial escalada por `(1 - fricción)`.

```python
vn = v.dot(n)
if vn < 0:                       # solo si se acerca
    vt = v - n * vn
    v = vt * (1 - mu) - n * vn * e
```

### 5.3 Flippers: lo único difícil de verdad

Un flipper no es una pared que se mueve. Golpea. La bola tiene que salir con la velocidad del punto del flipper que la toca, no solo rebotar.

Modelo:

- El flipper es un segmento desde el pivote hasta la punta, con ángulo `theta` entre `rest` y `up`.
- Velocidad angular `omega` fija cuando sube (~30 rad/s) y algo menor cuando baja.
- Al colisionar, la velocidad del punto de contacto es `omega × r` (perpendicular al radio desde el pivote, módulo `omega * |r|`). Se resta de la velocidad de la bola antes de reflejar y se suma después. Eso es lo que da el tiro fuerte en la punta y flojo en la base, como en una máquina real.
- Mientras el flipper está arriba y quieto, es una pared inclinada normal: la bola rueda por él. Es lo que permite "cazar" la bola.

Lo que **no** se hace: barrido angular continuo del flipper por subpaso. Con 480 Hz efectivos y un flipper que tarda ~50 ms en subir, el salto angular por subpaso es ~0.06 rad. Tolerable. Space Cadet lo hace mejor; nosotros lo hacemos legible.

### 5.4 Gravedad e inclinación

La mesa está inclinada ~6.5°. En 2D vista cenital eso es simplemente una gravedad hacia abajo de `g * sin(6.5°)`. Con `g = 9.81 m/s²` y una escala de ~1 m = 1000 px, sale `GRAVITY ≈ 1100 px/s²`. Se ajusta a ojo hasta que la bola "pese" bien. Es un parámetro de diseño, no de física.

### 5.5 Lo que se roba de cada referencia

| Referencia | Qué aporta a pynball |
|---|---|
| **k4zmu2a/SpaceCadetPinball** | El modelo de flipper como cápsula rotatoria, la separación corrección-posicional / reflexión, y la idea de que cada elemento tiene su propia restitución y su propio *kick*. También la máquina de estados de la partida. |
| **gusruben/winball** | La escala. Un pinball cabe en poco código si no se inventan abstracciones. Bucle único, estructuras planas, mesa en datos. |
| **Pokemon-Pinball / CITM-UPC (Box2D)** | Confirman por contraste la decisión: con Box2D el 60 % del código es glue entre el motor y el juego. Sin Box2D, ese 60 % es la física misma, y esa es la parte interesante de leer. Se copia de ellos el tratamiento de los sensores (zonas que puntúan sin colisión física). |
| **BBC BASIC pinball** | El sonido sintetizado con envolventes en vez de samples. Y la prueba de que una mesa parcial pero bien afinada es más divertida que una completa y floja. |
| **FreeWPC** | Vocabulario y reglas de máquina real: *trough* (depósito de bolas), *ball save*, *tilt warnings*, *bonus* al perder bola. pynball implementa un subconjunto pequeño y correcto. |
| **flipper-zero-pinball** | La disciplina de un target con pocos recursos. Nada de allocar por frame: todas las listas de elementos se construyen una vez. |

---

## 6. La mesa

Definida en `build_table()` como lista de elementos, coordenadas en píxeles sobre un lienzo lógico de **540 × 960** (escalado a la ventana). Nada de cargar ficheros: cambiar la mesa es editar Python.

```
   ┌─────────────────────────────┐
   │   ╭───────────────────╮  ║  │  ← arco superior, carril de lanzamiento (║)
   │   │   (B)   (B)       │  ║  │  ← 3 bumpers pop
   │   │      (B)          │  ║  │
   │   │  ▐▌ ▐▌ ▐▌         │  ║  │  ← dianas (drop targets)
   │   │                   │  ║  │
   │   │  ◢           ◣    │  ║  │  ← slingshots
   │   │   ╲         ╱     │     │
   │   │    ╲  ___  ╱      │  ●  │  ← plunger
   │   │    ◣ ╱   ╲ ◢      │     │  ← flippers
   │   │      ▽▽▽▽▽        │     │  ← desagüe
   └─────────────────────────────┘
```

Elementos y puntuación:

| Elemento | Puntos | Comportamiento |
|---|---|---|
| Bumper pop | 100 | Kick radial de 600 px/s, parpadea 100 ms |
| Slingshot | 50 | Kick en la normal, 400 px/s |
| Drop target | 500 | Se hunde al golpearlo; los tres caídos: 5000 y se levantan |
| Carril lateral | 1000 | Sensor; enciende luz de carril |
| Rollover superior | 250 | Tres luces; completar el banco sube el multiplicador |
| Desagüe | — | Pierde bola, suma bonus × multiplicador |

---

## 7. Reglas de partida

Máquina de estados mínima, con `enum.Enum` y `match`:

```
ATTRACT ──(R)──► PLAYING ──(bola al desagüe)──► BALL_LOST
   ▲                 ▲                               │
   │                 └──(quedan bolas)───────────────┤
   └──────────────────(0 bolas)──► GAME_OVER ◄───────┘
```

- 3 bolas por partida.
- **Ball save**: los primeros 5 s tras lanzar, perder la bola la devuelve al plunger sin coste.
- **Tilt**: tres nudges en menos de 2 s apagan los flippers hasta que la bola se pierde. Warning visual al segundo nudge.
- **Bonus**: cada carril y rollover suma 1000 al bonus; al perder bola, `bonus × multiplicador` se añade y el bonus se reinicia. El multiplicador (1x-5x) se mantiene durante la partida.
- **Extra ball** a los 50 000 puntos. Una por partida.
- Récord guardado en `~/.pynball_hiscore` como un entero en texto plano. Si no se puede escribir, se ignora en silencio.

---

## 8. Sonido

Sin ficheros. Al arrancar se sintetizan en memoria con numpy unos pocos buffers de 16 bits:

| Sonido | Síntesis |
|---|---|
| Flipper | Click: ruido blanco, 15 ms, envolvente exponencial |
| Bumper | Seno 220 Hz con caída rápida de pitch, 80 ms |
| Slingshot | Triángulo 440 Hz, 60 ms |
| Diana | Dos senos en quinta, 120 ms |
| Desagüe | Barrido descendente 800→100 Hz, 400 ms |
| Tilt | Onda cuadrada 110 Hz entrecortada, 600 ms |

Si `numpy` no está disponible o `pygame.mixer` falla al inicializar, `play()` es una función vacía. El juego nunca debe morir por el audio.

---

## 9. Render

- `pygame.Surface` lógica de 540×960, escalada al tamaño de la ventana con `smoothscale` solo si el factor no es entero; si lo es, `scale` para mantener los píxeles duros.
- Mesa dibujada con primitivas (`draw.line`, `draw.circle`, `draw.arc`, `draw.polygon`). Sin sprites.
- Paleta de 6 colores definida como constantes. Un pinball vive de las luces: los elementos "encendidos" cambian de color, nada más.
- HUD en la banda superior: puntuación, bola actual, multiplicador, bonus, récord. Fuente del sistema vía `pygame.font.SysFont`.
- Modo `F1`: dibuja cada colisionador en verde, las normales en amarillo, el vector velocidad de la bola en rojo. Imprescindible para afinar la mesa.

---

## 10. Plan de construcción

Orden de implementación con un criterio de "jugable al final de cada paso":

1. **Bola y paredes.** Vec2, geometría, Ball, cuatro paredes, gravedad. La bola cae y rebota. Modo debug desde el primer día.
2. **Flippers.** El paso que más se itera. Hasta que no se sienten bien, nada más.
3. **Plunger y carril.** Carga proporcional al tiempo pulsado, arco superior que lleva la bola a la mesa.
4. **Bumpers y slingshots.** Primeros puntos. Primer sonido.
5. **Dianas, carriles, rollovers.** Sensores y banco de dianas.
6. **Reglas.** Estados, bolas, tilt, bonus, récord.
7. **Pulido.** Luces, attract mode, afinado de restituciones y gravedad.

Cada paso se cierra con un commit. El fichero se mantiene ejecutable en todo momento.

---

## 11. Constantes de afinado

Todas juntas al principio del fichero, con el valor inicial y el rango razonable en comentario. Son las que se tocan jugando con `F1` activado:

```python
GRAVITY        = 1100.0   # px/s²      [800 .. 1600]
BALL_RADIUS    = 8.0      # px
BALL_MAX_SPEED = 1800.0   # px/s       limitar evita túneles en picos
WALL_E         = 0.55     # restitución [0.3 .. 0.8]
WALL_MU        = 0.02     # fricción tangencial
BUMPER_KICK    = 600.0    # px/s
SLING_KICK     = 400.0    # px/s
FLIPPER_OMEGA  = 30.0     # rad/s      [20 .. 40]
FLIPPER_REST   = -0.45    # rad, ángulo en reposo respecto a la horizontal
FLIPPER_UP     =  0.55    # rad
PLUNGER_MAX    = 1400.0   # px/s, a carga completa
BALL_SAVE_S    = 5.0
TILT_WINDOW_S  = 2.0
```

---

## 12. Lo que queda fuera a propósito

- Multibola. Dobla la complejidad de reglas por poco beneficio didáctico.
- Rampas con cambio de plano (bola "por encima" de otros elementos). Space Cadet lo hace con capas; aquí sería ruido.
- Mesas cargables desde fichero. Si la mesa es Python, el editor de mesas es tu editor de texto.
- Física continua con tiempo exacto de impacto. Los substeps bastan y se leen.

Si alguna de estas se vuelve necesaria, probablemente el proyecto ya no debería ser un solo fichero, y ese es el momento de partirlo. No antes.

---

## Referencias

- [k4zmu2a/SpaceCadetPinball](https://github.com/k4zmu2a/SpaceCadetPinball) — descompilación MIT del 3D Pinball de Windows. Física real de un pinball comercial.
- [gusruben/winball](https://github.com/gusruben/winball) — pinball pequeño en C con Allegro.
- [xGauss05/Pokemon-Pinball](https://github.com/xGauss05/Pokemon-Pinball) y [AdriaPm/Pinball-Game](https://github.com/AdriaPm/Pinball-Game) — física delegada a Box2D.
- [frnkptrln/flipper-zero-pinball](https://github.com/frnkptrln/flipper-zero-pinball) — pinball embebido.
- [bcd/freewpc](https://github.com/bcd/freewpc) — framework para máquinas reales WPC.
- [rtrussell/BBCSDL](https://github.com/rtrussell/BBCSDL) — `pinball.bbc`, sonido por envolventes.
- [GitHub topic pinball en C](https://github.com/topics/pinball?l=c)

---

## Licencia

[MIT](LICENSE). Úsalo, cópialo, rómpelo.
