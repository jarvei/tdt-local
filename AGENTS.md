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
- `tdtlocal/adwatch/` — motor de publicidad portado de `tdt-datos`: HLS, DASH, SCTE-35, ID3 y
  `engine.Vigilance`, que sigue una variante y mantiene el estado con el lag del directo.
- `tdtlocal/static/js/tile.js` — una casilla: `<video>`, hls.js, cadena de fuentes y subtítulos.
- `tdtlocal/static/js/adbreak.js` — la reacción a una pausa; `lib/adbreak.js` son las decisiones puras.
- `tdtlocal/static/js/lib/inband.js` — lector del SCTE-35 in-band (ID3 `PRIV`).
- `tdtlocal/static/js/lib/` — lógica pura, probada en `tests/js/`.
- `tests/` — pruebas de Python; `tests/js/` — pruebas del navegador.

## Publicidad: cómo funciona

Hay dos caminos y el in-band manda cuando existe:

1. **In-band (Atresmedia, exacto).** El `splice_insert`/`time_signal` viaja en ID3 `PRIV` dentro del
   PID 0x23 del segmento. hls.js lo entrega en `FRAG_PARSING_METADATA`; cada muestra trae `pts`, que
   ya está en la línea de tiempo del reproductor (hls.js lo usa tal cual como inicio de la cue).
   `lib/inband.js` parsea el ID3 y el `splice_info_section`; `tile.js` arma un `setTimeout` con
   `pts - currentTime` y `adbreak.js` aplica el cambio al llegar. Si el reproductor se pausa o se
   desvía, al disparar se vuelve a medir la distancia en vez de silenciar antes. `Break End 0x23` y
   `splice_insert` con `out_of_network=0` cierran en su muestra exacta.
2. **Playlist (respaldo y Mediaset).** `adwatch.engine.Vigilance` sondea el master y su **primera**
   variante cada 2 s (1 s en Mediaset), como `firstVariant` de AdMute —las marcas de Atresmedia van
   ahí, no en las de más calidad—, decodifica los tags y publica `cue-out`/`cue-in` ya sincronizados con la imagen:
   `now - lag`, con `LAG_S = 18` en Atresmedia y 0 en Mediaset. La duración declarada es la red de
   seguridad (margen 18 s / 5 s); el `CUE-IN` es el cierre bueno y también espera al lag. Mediaset
   añade el `admode` de su DAI y la regla de `earlyMediasetIn`.

Reglas al tocar esto:

- No vuelvas al detector por firma de audio: se midió y daba falsos positivos (7 de 15 minutos en
  ficción). El proyecto `tdtspain-admute` lo descartó por eso.
- Cualquier cambio de comportamiento lleva prueba; el parser in-band tiene sus fixtures reales en
  `tests/js/inband.test.mjs`.
- El mute lo aplica la página (`Tile.setDuck`), no el servidor. El servidor solo decide.
- Un canal sin ID3 (Mediaset, RTVE, U7D, YouTube, embeds) no puede usar el camino in-band.

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
- `docs/TDTSPAIN-Y-ADMUTE.md` — catálogo, resolvedores y las dos vías del auto-mute.
- `CHANGELOG.md` y `ROADMAP.md` — cambios y pendientes.
- `CONTRIBUTING.md` — entorno, estilo y envío de cambios.
