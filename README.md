# pynball

Un pinball completo en **un solo fichero**: `pynball.py`. Python moderno, `pygame-ce`, cero motor de física externo. Pensado para leerse de arriba abajo en una tarde, como `winball` en C, pero con las lecciones de los pinballs grandes.

Este README es el documento de diseño y la guía de lectura del código: qué se construye, por qué así, y qué se toma de cada referencia. Se escribió antes del código, y se ha ajustado después en los puntos donde el código enseñó algo.

```bash
pip install pygame-ce numpy
python pynball.py
```

---

## 1. Qué es y qué no es

**Es**

- Un juego jugable: plunger, dos flippers, tres bumpers pop, dos slingshots, banco de dianas, banco de standups P-Y-N, tres carriles superiores con rollovers, agujero kickout, órbita, inlanes y outlanes, desagüe, 3 bolas, bola extra, skill shot, combos, ball save, tilt, puntuación y récord.
- Un único `pynball.py` de ~1.300 líneas, sin assets externos. Sonido generado por síntesis, mesa definida como datos dentro del propio fichero.
- Física propia: bola circular contra segmentos, arcos y flippers rotatorios con velocidad de superficie. Legible, determinista, sin dependencias.
- Rápido: la simulación completa a 960 Hz consume ~1 % de un núcleo. Un minuto de juego se simula en medio segundo sin ventana.

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

Modo sin ventana para pruebas y CI:

```bash
python pynball.py --headless 3000     # simula 3000 frames con un bot tonto y sale
python pynball.py --dump-sounds out/  # vuelca los 78 sonidos sintetizados a WAV
python pynball.py --mute              # sin audio
```

---

## 3. Controles

| Tecla | Acción |
|---|---|
| `Shift Izq` / `Z` | Flipper izquierdo |
| `Shift Der` / `/` | Flipper derecho |
| `Espacio` (mantener) | Cargar plunger; soltar lanza |
| `X`, `C`, `V` | Nudge izquierda, abajo, derecha. Tres seguidos: **TILT** |
| Flipper (al pulsar) | Rota las luces de los carriles superiores (lane change) |
| `M` | Música sí / no |
| `←` / `→`, `Ctrl` | Alternativas para los flippers |
| `R` | Nueva partida |
| `F1` | Mostrar colisionadores y vectores de velocidad |
| `P` | Pausa |
| `Esc` | Salir |

---

## 4. Arquitectura del fichero

Un fichero, pero con secciones numeradas y separadas por banners de comentario. El orden importa: cada sección solo depende de las anteriores.

```
pynball.py
├── 0. Constantes de afinado (gravedad, dt, restituciones, velocidades, paleta)
├── 1. Vec2 (inmutable, para construir y dibujar) y utilidades clamp/lerp/mix
├── 2. Geometría de colisión: funciones puras sobre floats
│      circle_vs_segment, circle_vs_circle, circle_vs_arc_inside
├── 3. Elementos de mesa (dataclasses con slots)
│      Wall, Post, Arc, Sensor, Hole, Flipper
├── 4. Ball: posición, velocidad, bounce() con corrección posicional y reflexión
├── 5. Table + build_table(): la mesa escrita a mano, lado izquierdo y su espejo
├── 6. Physics.substep(): integración, broad-phase por AABB, contactos, eventos
├── 7. Synth + Sounds: síntesis modal y de solenoide, rodadura, música, paneo
├── 8. Game: reglas, puntuación, timers, tilt, máquina de estados, entrada
├── 9. Renderer: mesa, luces, bola con estela, partículas, HUD, modo debug
└── 10. main(): bucle con acumulador de tiempo fijo, modo --headless
```

**Por qué un solo fichero.** Es la restricción que impone claridad. `winball` cabe en pocos ficheros de C porque no tiene abstracciones que no necesite. Un pinball no es un proyecto grande: es un problema de física 2D con reglas encima. Si no cabe en un fichero, hay abstracción sobrante.

**Por qué floats sueltos en la física.** `Vec2` es cómodo, pero cada operación asigna un objeto. A 960 Hz con ~40 colisionadores son cientos de miles de asignaciones por segundo. Las funciones de la sección 2 reciben y devuelven floats; `Ball` guarda `x, y, vx, vy`. Es el único sitio donde se sacrifica elegancia por velocidad, y se dice explícitamente.

---

## 5. Física: decisiones y por qué

Esta es la sección que justifica el proyecto. Lo que sigue es lo que enseña leer Space Cadet, winball y los proyectos con Box2D.

### 5.1 Paso de tiempo fijo con substeps

```
FIXED_DT = 1 / 120          # física a 120 Hz
SUBSTEPS = 8                # 8 subpasos por frame de física = 960 Hz efectivos
```

Una bola de pinball a velocidad alta recorre varios píxeles por frame. Si se integra a 60 Hz sin más, atraviesa paredes finas y flippers. Space Cadet resuelve esto con detección continua: calcula el tiempo exacto de impacto dentro del frame. Es más correcto pero el código se dispara. Los substeps son la solución barata que mantiene todo legible: ocho integraciones pequeñas por frame y una comprobación de solapamiento tras cada una. Con bola de radio 9 px y velocidad máxima 2400 px/s, el desplazamiento por subpaso es 2,5 px, muy por debajo del radio. En doce partidas simuladas de dos minutos la bola no salió de la mesa ni una vez.

Acumulador de tiempo clásico: el render va a lo que dé la pantalla, la física avanza en pasos fijos. Determinismo gratis.

### 5.2 Primitivas de colisión

Solo tres: **bola contra segmento**, **bola contra círculo**, **bola contra arco**. Todo lo demás se compone de ellas.

- Paredes: segmentos.
- Bumpers, postes: círculos.
- Curvas superiores de la mesa, guías de rampa: arcos (círculo con rango angular).
- Flippers: un segmento que rota alrededor de su pivote, con un círculo en cada extremo. Es exactamente lo que hace Space Cadet (`TFlipperEdge`): la cápsula es un segmento gordo.
- Slingshots: segmento con restitución alta y kick fijo en la normal.

Respuesta a colisión en `Ball.bounce()`, en dos pasos y siempre en este orden:

1. **Corrección posicional**: sacar la bola fuera del sólido a lo largo de la normal, exactamente la penetración más un epsilon. Sin esto la bola "tiembla" dentro de las paredes.
2. **Reflexión de velocidad** relativa a la superficie: descomponer en normal y tangencial. Si la velocidad normal es pequeña (`REST_VN`) no hay rebote, hay apoyo: se anula la normal y la tangencial pierde una fricción de rodadura mínima. Si es un impacto, la normal se invierte y escala por la restitución del elemento, la tangencial por `(1 - fricción)`, y se suma el `kick` del elemento si lo tiene.

```python
vn = v.dot(n)
if vn < 0:                       # solo si se acerca
    vt = v - n * vn
    v = vt * (1 - mu) - n * vn * e + n * kick
```

Distinguir apoyo de impacto es lo que permite que la bola ruede por un inlane sin perder energía en 960 microrrebotes por segundo.

### 5.3 Flippers: lo único difícil de verdad

Un flipper no es una pared que se mueve. Golpea. La bola tiene que salir con la velocidad del punto del flipper que la toca, no solo rebotar.

Modelo (`Flipper.collide`):

- El flipper es una cápsula cónica: segmento desde el pivote hasta la punta, radio 10 px en la base y 7 px en la punta, con ángulo entre `rest` y `up`.
- Velocidad angular fija cuando sube (26 rad/s) y menor cuando baja (16 rad/s). La velocidad angular real de cada subpaso se mide como `(ángulo nuevo - viejo) / dt`, como hace winball.
- Al colisionar, la velocidad del punto de contacto es `omega × r` (perpendicular al radio desde el pivote). Se resta de la velocidad de la bola antes de reflejar y se suma después. Eso da el tiro fuerte en la punta y flojo en la base, como en una máquina real, y es el equivalente del `boost` proporcional a la distancia al pivote que usa Space Cadet.
- Mientras el flipper está arriba y quieto, es una pared inclinada normal: la bola rueda por él. Si llega por el inlane con el flipper levantado, se queda atrapada en la cuna del pivote hasta que lo sueltas. Es un *trap* de verdad, no un bug: el detector de bola atascada no actúa mientras haya un flipper pulsado.

Lo que **no** se hace: barrido angular continuo del flipper por subpaso. A 960 Hz el salto angular por subpaso es 0.027 rad y la punta se mueve 2,3 px. Space Cadet lo hace mejor; nosotros lo hacemos legible.

### 5.4 Gravedad e inclinación

La mesa está inclinada ~6.5°. En 2D vista cenital eso es simplemente una gravedad hacia abajo de `g * sin(6.5°)`. Con `g = 9.81 m/s²` y una escala de ~1 m = 860 px, sale `GRAVITY ≈ 950 px/s²`. Se dejó en 1150 porque así la bola "pesa" bien. Es un parámetro de diseño, no de física.

### 5.5 Lo que se roba de cada referencia

| Referencia | Qué aporta a pynball |
|---|---|
| **k4zmu2a/SpaceCadetPinball** | El modelo de flipper como cápsula rotatoria, la separación corrección-posicional / reflexión, y la idea de que cada elemento tiene su propia restitución y su propio *kick*. También la máquina de estados de la partida. |
| **gusruben/winball** | La escala. Un pinball cabe en poco código si no se inventan abstracciones. Bucle único, estructuras planas, mesa en datos. |
| **Pokemon-Pinball / CITM-UPC (Box2D)** | Confirman por contraste la decisión: con Box2D el 60 % del código es glue entre el motor y el juego. Sin Box2D, ese 60 % es la física misma, y esa es la parte interesante de leer. Se copia de ellos el tratamiento de los sensores (zonas que puntúan sin colisión física). |
| **BBC BASIC pinball** | El sonido sintetizado con envolventes en vez de samples. Y la prueba de que una mesa parcial pero bien afinada es más divertida que una completa y floja. |
| **FreeWPC** | Vocabulario y reglas de máquina real: *trough* (depósito de bolas), *ball save*, *tilt warnings*, *bonus* al perder bola. pynball implementa un subconjunto pequeño y correcto. |
| **flipper-zero-pinball** | La disciplina de un target con pocos recursos. Nada de allocar por frame: todas las listas de elementos se construyen una vez. De ahí vienen también el `nudge_heat` con decaimiento y el combo de bumpers. |

---

## 6. La mesa

Definida en `build_table()` como lista de elementos, coordenadas en píxeles sobre un lienzo lógico de **540 × 960** (escalado a la ventana con `pygame.SCALED`). Nada de cargar ficheros: cambiar la mesa es editar Python. El lado derecho es el espejo del izquierdo respecto al eje del campo de juego (`mirror()`), así que cada elemento lateral se escribe una vez.

```
   ┌──────────────────────────────┐
   │   ╭────┬──┬──┬────╮       ║  │  ← arco, 3 carriles con rollovers, carril de lanzamiento (║)
   │   │ ○                │  ⟋  ║  │  ← agujero kickout; puerta de un sentido a la salida del carril
   │   │   (B)   (B)      │     ║  │  ← 3 bumpers pop
   │   │      (B)         │     ║  │
   │   │ ▐              ▌ │     ║  │  ← banco de 3 dianas (izq) y 3 standups P-Y-N (der)
   │   │ ▔              ▔ │     ║  │  ← techo que cierra el hueco tras cada banco
   │   │ •  ◢        ◣  • │     ║  │  ← postes, slingshots; fuera de ellos, los outlanes
   │   │  ╲            ╱  │     ║  │  ← guías de inlane, tangentes al flipper en reposo
   │   │   ◣ ╱      ╲ ◢   │  ●  │  ← flippers; plunger
   │   │      ▽▽▽▽▽▽      │     │  ← desagüe
   └──────────────────────────────┘
```

Elementos y puntuación:

| Elemento | Puntos | Comportamiento |
|---|---|---|
| Bumper pop | 100 × combo | Kick radial de 650 px/s. Golpes seguidos sin tocar flipper suben el combo hasta x10 |
| Slingshot | 50 | Kick de 520 px/s solo por la cara frontal y solo si la bola llega con velocidad |
| Diana (banco izq.) | 500 | Se apaga al golpearla; las tres: 5000 y se rearman en 1 s |
| Standup P-Y-N (der.) | 300 | Enciende su letra; las tres: 3000 |
| Rollover superior | 250 | Enciende su luz; las tres: multiplicador +1 (máx. x5), después 10 000 |
| Skill shot | 5000 | Al lanzar parpadea un carril; si la bola entra por él en 4 s |
| Agujero kickout | 1000 × visita | Captura la bola si pasa despacio, la retiene 1,2 s y la expulsa al inlane izquierdo |
| Desagüe | — | Pierde bola, suma bonus × multiplicador |

Cada carril, diana y agujero suma además al **bonus** de la bola (1000, 1000, 500 y 2000 respectivamente).

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
- **Ball save**: los primeros 6 s tras lanzar, perder la bola la devuelve al plunger sin coste.
- **Tilt**: cada nudge suma 1 de calor, que decae a 0,55/s. Por encima de 1,6 avisa; por encima de 2,5 apaga los flippers y el bonus hasta que la bola se pierde. Es el esquema de Space Cadet (`nudge_count`) con los números de flipper-zero-pinball.
- **Bonus**: al perder bola, `bonus × multiplicador` se añade y el bonus se reinicia. El multiplicador (1x-5x) se mantiene durante la partida.
- **Extra ball** a los 50 000 puntos. Una por partida.
- **Bola atascada**: si lleva 2,5 s casi quieta fuera del carril y sin ningún flipper pulsado, recibe un empujón aleatorio.
- Récord guardado en `~/.pynball_hiscore` como un entero en texto plano. Si no se puede escribir, se ignora en silencio.

---

## 8. Sonido

Sin ficheros. Todo se sintetiza al arrancar con numpy en menos de 0,2 s: 78 buffers, tres variantes desafinadas de cada golpe para que no canse, paneo estéreo según la posición en la mesa, y volumen que sigue la velocidad de impacto.

**El modelo.** Un pinball suena a mecánica más electrónica, y las dos capas se sintetizan distinto:

| Capa | Técnica | Dónde |
|---|---|---|
| Solenoide | Ráfaga de ruido filtrada por FFT (12 ms) + golpe grave 90→55 Hz + click metálico | Flippers, bumpers, slingshots, expulsión del agujero, lanzamiento |
| Acero contra metal | Síntesis modal: parciales inarmónicos (1 : 1,71 : 2,63 : 4,11) que decaen en 30 ms | Toques contra paredes y postes, con volumen según la velocidad y uno cada 35 ms como máximo |
| Rodadura | Ruido marrón filtrado en bucle de 1 s; al filtrar por FFT la señal es periódica y el bucle no tiene costura | Canal reservado; el volumen sigue la velocidad de la bola con suavizado |
| Campanas | Modal con parciales de campana (1 : 2 : 2,76 : 5,4 : 8,9) sobre pentatónica de La menor | Dianas, standups, rollovers, cuenta del bonus |
| Combo | El bumper suena a solenoide y además a una nota: cada golpe seguido sube un grado de la escala | Bumpers |
| Jingles | Acordes arpegiados con triángulo y octava | Inicio, banco completo, multiplicador, skill shot, extra ball, game over |
| Música | Bucle de 4 compases a 112 BPM (Am F C G): bajo de sierra filtrado, arpegio de triángulo, bombo, caja y charles. Lo que sobresale del final se pliega al principio | Canal reservado, `M` para apagar |

**Detalles que se oyen.** El plunger hace clic de trinquete cada octavo de carga. Al perder bola, el bonus se cuenta a golpes con campanas ascendentes, como una electromecánica. El tilt es un zumbido troceado. Si el flipper golpea la bola, suena un "whack" distinto del clack del solenoide, más fuerte cuanto más rápido.

**Por qué FFT para filtrar.** No hay scipy y un filtro IIR en bucle de Python sobre 22 050 muestras es lento. Multiplicar el espectro por una máscara con bordes suaves es vectorizado, exacto y además deja los bucles sin costura. Es el truco que hace viable todo el sistema en numpy puro.

Si `numpy` no está disponible o `pygame.mixer` falla al inicializar, `play()` es una función vacía. El juego nunca debe morir por el audio.

## 9. Render

- `pygame.Surface` lógica de 540×960; `pygame.SCALED | RESIZABLE` deja a SDL el escalado a la ventana.
- Mesa dibujada con primitivas (`draw.line`, `draw.circle`, `draw.polygon`). Sin sprites.
- Paleta de constantes al principio del fichero. Un pinball vive de las luces: los elementos "encendidos" cambian de color con `mix()` según su `flash`, nada más.
- Estela de la bola, partículas en bumpers y slingshots, sacudida de pantalla al hacer nudge, destello en skill shot y extra ball.
- HUD en la banda superior: puntuación, bolas restantes, multiplicador, bonus, récord, estado de ball save y tilt. Fuente por defecto de pygame.
- Modo `F1`: dibuja cada colisionador en verde, las normales en amarillo, el vector velocidad de la bola en rojo, y velocidad, posición y calor de tilt en texto. Imprescindible para afinar la mesa.

---

## 10. Cómo se probó

Sin ventana, con un bot tonto que flipa cuando la bola baja cerca y relanza cuando vuelve al plunger. Doce semillas, dos minutos simulados cada una, comprobando en cada frame:

- la bola nunca sale del rectángulo de la mesa ni entra en un slingshot;
- la bola nunca queda detrás de un banco de dianas ni se cuela en el carril por la puerta;
- la partida avanza: bolas 1, 2, 3, game over, multiplicador, bola extra.

Tres bugs reales salieron de ahí y se arreglaron antes del primer commit del juego: la bola se quedaba en la cuna del pivote cuando el bot sujetaba el flipper (era un *trap* legítimo, se corrigió el detector de atasco, no la física); un bolsillo muerto tras cada banco de dianas al que la bola entraba desde abajo (se cerró con un techo); y un bucle agujero-bumper-agujero que daba 800 000 puntos en dos minutos (la expulsión ahora va al inlane y el agujero tiene 1,5 s sin capturar).

Una segunda revisión del código completo añadió: el récord se guarda también al salir con la partida a medias, la bola extra se concede aunque el umbral se cruce con el bonus, el nombre de cada sonido que pide el juego se comprueba contra el banco, y las teclas de flipper admiten flechas y Ctrl además de Shift.

---

## 11. Constantes de afinado

Todas juntas al principio del fichero, con el valor inicial y el rango razonable en comentario. Son las que se tocan jugando con `F1` activado:

```python
GRAVITY           = 1150.0   # px/s²      [800 .. 1600]
BALL_RADIUS       = 9.0      # px
BALL_MAX_SPEED    = 2400.0   # px/s       limitar evita túneles en picos
WALL_E            = 0.55     # restitución [0.3 .. 0.8]
WALL_MU           = 0.03     # fricción tangencial en impacto
ROLL_MU           = 0.004    # fricción tangencial en apoyo
REST_VN           = 70.0     # px/s, por debajo no hay rebote, hay apoyo
BUMPER_KICK       = 650.0    # px/s
SLING_KICK        = 520.0    # px/s
FLIPPER_OMEGA_UP  = 26.0     # rad/s      [20 .. 40]
FLIPPER_REST      = +30°     # ángulo en reposo respecto a la horizontal
FLIPPER_UP        = -28°
PLUNGER_MIN/MAX   = 500 / 1650   # px/s, toque / carga completa (1 s)
BALL_SAVE_S       = 6.0
TILT_LIMIT        = 2.5      # calor acumulado; cada nudge suma 1, decae 0,55/s
```

---

## 12. Lo que queda fuera a propósito

- Multibola. Dobla la complejidad de reglas por poco beneficio didáctico.
- Rampas con cambio de plano (bola "por encima" de otros elementos). Space Cadet lo hace con capas; aquí sería ruido.
- Mesas cargables desde fichero. Si la mesa es Python, el editor de mesas es tu editor de texto.
- Física continua con tiempo exacto de impacto. Los substeps bastan y se leen.
- Sprites y texturas. Las luces y las primitivas hacen el trabajo, y el fichero sigue siendo el único artefacto.

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
