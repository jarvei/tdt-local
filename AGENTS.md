# AGENTS.md

Guía para quien (persona o agente) toque este repositorio. El proyecto está en español: interfaz,
mensajes y documentación en español; identificadores, comentarios y docstrings en inglés.

## Qué es

Visor web local de la TDT española. Un servidor Python hace de proxy de los streams (casi ninguno
manda CORS), resuelve el catálogo de TDT Spain, sirve la EPG, la transcripción y la reacción a la
publicidad. La interfaz reproduce con hls.js y decide qué hacer durante una pausa.

- Python 3.9+, **solo biblioteca estándar**: no hay dependencias de ejecución.
- Navegador: módulos ES sin *bundler*; `hls.js` es un script clásico en `tdtlocal/static/vendor/`.
- Sin base de datos: estado en memoria y JSON bajo `data/` (no versionado).
- La red solo sale por `tdtlocal/net.py` (`Net`), que valida hosts y bloquea direcciones privadas.

## Arrancar y probar

```
./iniciar.sh                 # Linux/macOS; para la instancia anterior
iniciar.bat                  # Windows
python -m tdtlocal --open    # a mano; escucha en http://127.0.0.1:8788/
```

```
python -m unittest discover -s tests -t .   # Python
node --test                                  # navegador (Node 20+)
python scripts/check.py                      # todo + lint
ruff check . && ruff format --check .        # si ruff está instalado
```

Las pruebas no tocan internet, ni ffmpeg, ni llama.cpp ni whisper.cpp: `tests/helpers.py` trae un
origen HTTP falso y una aplicación sobre un directorio temporal.

## Dónde está cada cosa

- `tdtlocal/app.py` — construye `App`, los servicios y los hilos de fondo.
- `tdtlocal/server.py` — rutas HTTP, proxy y estáticos.
- `tdtlocal/store.py` — canales, EPG, salud y canales propios (un `RLock`).
- `tdtlocal/tdtspain.py` — catálogo y resolvedores de los directos.
- `tdtlocal/scte.py`, `scte_watch.py`, `scte_routes.py` — marcas SCTE-35, registro y API.
- `tdtlocal/adwatch/` — motor de publicidad portado de `tdt-datos`: HLS, DASH, SCTE-35, `admode` y
  `engine.Vigilance`, que sigue el master y su primera variante, y mantiene el estado con el lag del directo.
- `tdtlocal/static/js/tile.js` — una casilla: `<video>`, hls.js, cadena de fuentes y subtítulos.
- `tdtlocal/static/js/adbreak.js` — la reacción a una pausa; `lib/adbreak.js` son las decisiones puras.
- `tdtlocal/static/js/lib/` — lógica pura, probada en `tests/js/`.
- `tests/` — pruebas de Python; `tests/js/` — pruebas del navegador.

## Publicidad: cómo funciona

Solo hay un camino, el de la app (`AdMute`):

1. Al sintonizar, `app.watch_ads` resuelve el canal y `adwatch.engine.Vigilance` lee el **master**.
2. Se queda con su **primera variante** (`firstVariant()` de AdMute), no con la de más calidad: en
   Atresmedia las marcas van en la 405000 y las variantes altas van limpias. Después sondea esa lista
   cada 2 s (1 s en Mediaset).
3. Decodifica los tags (`#EXT-X-DATERANGE` con `SCTE35-OUT`/`SCTE35-IN`, `SCTE35-CMD`,
   `#EXT-X-CUE-OUT`/`CUE-IN`, `X-ASSET-URI` y el `admode` de Mediaset) y decide el estado como lo ve
   el espectador: `state_of()` evalúa `ahora - lag`, con `LAG_S = 18` en Atresmedia y 0 en Mediaset.
4. Cuando el estado cambia para la imagen, publica `cue-out`/`cue-in` con `apply_now`; la página
   silencia o devuelve el sonido en ese instante. El cierre bueno es el `CUE-IN`; la duración con
   margen (18 s / 5 s) es la red de seguridad. El `CUE-IN` temprano del DAI de Mediaset se ignora
   (`earlyMediasetIn`).

Reglas al tocar esto:

- No vuelvas a `_best_variant()` ni a un lector in-band: las variantes de más calidad de Atresmedia no
  llevan marcas y el navegador elige una de ellas. La primera variante es la única que sirve.
- No vuelvas al detector por firma de audio: se midió y daba falsos positivos (7 de 15 minutos en
  ficción). El proyecto `tdtspain-admute` lo descartó por eso.
- Cualquier cambio de comportamiento lleva prueba en `tests/test_adwatch.py` (y en `tests/js/` si
  toca la reacción).
- El mute lo aplica la página (`Tile.setDuck`), no el servidor. El servidor solo decide.
- Los canales que no publican SCTE-35 en su lista (RTVE, U7D, YouTube y los embeds de Atresplayer)
  no se pueden silenciar; no hay una segunda vía que inventar.

## Convenciones

- Español para interfaz, mensajes, documentación y nombres de ajustes; inglés para el código.
- Líneas de hasta 120 caracteres; `ruff` con las reglas de `pyproject.toml`.
- Lo que se pueda probar sin red ni hilos va a funciones puras (`tdtlocal/*.py` o `static/js/lib/`).
- Nada de dependencias nuevas en ejecución, ni *bundler*, ni scripts o estilos en línea (la CSP los
  bloquea). `tdtlocal/static/vendor/` es de terceros: no se edita.
- Las peticiones salientes pasan por `Net`; lo que venga de la red o del modelo se valida.
- Commits en imperativo y en español; un cambio por commit, con su prueba.

## Documentación

- `README.md` — qué hace y cómo se usa.
- `docs/ARCHITECTURE.md` — módulos, flujo del proxy y de la interfaz.
- `docs/TDTSPAIN-Y-ADMUTE.md` — catálogo, resolvedores y el algoritmo del auto-mute.
- `CHANGELOG.md` y `ROADMAP.md` — cambios y pendientes.
- `CONTRIBUTING.md` — entorno, estilo y envío de cambios.
