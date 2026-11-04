# Catálogo TDT Spain y auto-mute

Estado: implementado. Este documento fija el diseño y lo que ya está hecho, para poder retomarlo.

## Lo que se pide

1. La lista de canales del visor deja de ser la de TDTChannels: se usa la de la app
   **TDT Spain** (`canalesv4.json`, 263 canales, 248 visibles), con sus grupos, logos y `epgid`.
2. La detección de publicidad no es la de `scte_watch.py` (mirar la playlist al servirla): se usa el
   motor de **`~/Proyectos/tdt-datos`** (`vigilancia.py`: sondeo del master y de su primera variante,
   `EXT-X-DATERANGE`/`CUE-OUT`/`CUE-IN`, SCTE-35 decodificado, ID3 `admode`, `EventStream` del MPD,
   estado `programa`/`publicidad` y segundos restantes).
3. La reacción es el **auto-mute de `~/Proyectos/tdtspain-admute/build/src/com/tv/viewer/probe/AdMute.java`**:
   silenciar al empezar el corte y devolver el sonido al terminar, con su lag, su duración efectiva,
   sus márgenes de cierre y su red de seguridad. `AdMute` lee el master y se queda con
   `firstVariant()`; **no** usa el SCTE-35 in-band del reproductor. El visor hace lo mismo.

## Decisiones

- **Fuente por defecto**: `https://www.tdtspain.com/canales/canalesv4.json`. Se mantiene
  `--source` y la constante de TDTChannels para volver atrás. La radio sigue en `RADIO.md`.
- **Canales ocultos** (`ocultar: 1`, 15 en el catálogo) no se listan.
- **Resolución perezosa**: los streams con token o scraping (`stream10` Mediaset, `stream12`
  Dailymotion, `geturl`, `stream14`) no se resuelven al arrancar; se resuelven al sintonizar, como
  ya hace `u7d` (`/api/u7d/play`). Así no caducan en la lista.
- Los canales que solo son un `.mpd` (5 de ETB) quedan como «solo web»: hls.js no reproduce DASH.
- El `epgid` del catálogo es el de TDTChannels, salvo en Atresmedia, que esa guía ya no publica: `epg.py` traduce sus
  identificadores a los de la guía extra (`Antena3.TV` → `Antena 3 HD`…).
- **Primera variante, no la de más calidad**: `AdMute` no busca las marcas en el stream que reproduce,
  sino en la lista. Lee el **master** y se queda con la **primera variante** que declara
  (`firstVariant()`): en Atresmedia es la de 405000, la que lleva `SCTE35-OUT`/`SCTE35-IN`; la de
  6 Mbps, comprobada, no tenía ni un tag. Elegir la de más bitrate (el antiguo `_best_variant`, ya
  retirado) dejaba todos los cortes sin detectar.
- **Mediaset por `dai.google.com`**: es el mismo DAI de Google que `pubads.g.doubleclick.net`, pero
  las DNS de filtrado de anuncios resuelven el nombre antiguo a `0.0.0.0` y el guardián de red lo
  rechaza. `dai.google.com` devuelve el manifiesto de Telecinco, Cuatro, FDF y el resto.

## Resolvedores (probados con curl el 3-nov-2026)

| Tipo | Canales | Cómo |
|---|---|---|
| directo | 160 | URL `m3u8` tal cual |
| `youtube` | 15 | URL de YouTube; el servidor resuelve el id actual del directo (canal o `/live/<id>` fijo) y la casilla incrusta `/embed/<id>` |
| `stream11` | 17 | HLS de Atresplayer (`tdtspain.py`), con su SCTE-35 en la primera variante; sin iframe ni puente |
| `stream1` RTVE | 11 | HLS fijo `rtvelivestream.rtve.es/rtvesec/...` (la1, la2, 24h, tdp, clan, can/*, cat/*) |
| `stream10` Mediaset | 14 | login anónimo → `playback/check` → `dai.google.com` → `stream_manifest` (HLS en claro) |
| `stream12` Dailymotion | 4 | `geo.dailymotion.com/video/<id>.json` → `qualities.auto[0].url` |
| `geturl` | 20 | GET de la web + regex `patron` + `baseurl` (probados los 20) |
| `stream14` Aragón | 1 | GraphQL `eu.next.mediastream.co` → `accessToken` → `mdstrm` |
| `stream2` DMAX | 1 | no emite (`msgerror` en el catálogo): solo web |

## Auto-mute (el algoritmo)

Una sola vía, la de `AdMute`:

1. **Resolver al sintonizar.** La página pide `/api/tdtspain/play?c=<id>`; el servidor sigue la receta
   (master de Atresplayer, DAI de Mediaset, etc.) y `ScteWatcher.watch` crea un `Vigilance` con la
   URL, sus cabeceras, el `lag`, el `margin` y el `interval`.
2. **Primera variante.** `Vigilance._step` lee el master y `_first_variant()` devuelve la primera URI
   de `variants`; si la URL ya es una lista de medios (Mediaset), se usa tal cual. En la vuelta
   siguiente se lee esa variante. Las variantes de más calidad no llevan marcas: no se elige por
   bitrate.
3. **Sondeo.** Cada 2 s (1 s en Mediaset) se lee la lista y se decodifican los tags:
   - `#EXT-X-DATERANGE` con `SCTE35-OUT`/`SCTE35-IN` o `SCTE35-CMD`: el `splice_info_section`
     completo (`splice_insert`, `time_signal`, descriptores de segmentación). Un Program Start
     (`0x10`) o un `SCTE35-PROGRAM` no abre publicidad.
   - `#EXT-X-CUE-OUT`, `CUE-OUT-CONT` y `CUE-IN`.
   - Anuncios intercalados de HLS (`X-ASSET-URI`), el `admode` que Mediaset deja en `X-MESSAGE-DATA`
     y el `EventStream` SCTE-35 del MPD (si el canal tiene DASH).
4. **Estado para la imagen.** `Vigilance._update` llama a `state_of(events, now - lag)`: recorre las
   marcas en orden y se queda con la última que ya ha llegado a la pantalla. `LAG_S = 18` en
   Atresmedia (el playhead va unos 18 s por detrás del directo); `0` en Mediaset (la sesión DAI ya
   entrega el tiempo restante). Una marca con `START-DATE` futuro todavía no se aplica; un `CUE-IN`
   recién visto tampoco cierra hasta que su hora entra en `now - lag`.
5. **Duración y cierre.** La duración efectiva es `DURATION` / `CUE-OUT:<s>` / `out_<ms>` menos el
   retraso ya consumido. El cierre bueno es el `CUE-IN`; si no llega, se cierra al cumplirse la
   duración más el margen (18 s en Atresmedia, 5 s en Mediaset). Si el `CUE-IN` del DAI llega antes
   que la emisión (`earlyMediasetIn`), no se devuelve el sonido hasta cumplir la duración prevista.
   `MAX_BREAK_S = 900` es la red de seguridad.
6. **Publicación y reacción.** `ScteWatcher._on_change` escribe `cue-out` o `cue-in` en el registro
   con `apply_now: True` en cuanto el estado cambia para la imagen. La página sondea ese registro cada
   2 s (`scte.js`); `adbreak.js` aplica la reacción y `Tile.setDuck` silencia o baja el volumen. Al
   `cue-in` devuelve el sonido. El servidor solo decide; el mute lo aplica la página.

### Lo que se retiró y por qué

- **Lector in-band (ID3 `PRIV`)**: se probó con los `PRIV` reales de Antena 3, pero no es lo que hace
  `AdMute` y no sirve con el reproductor: hls.js elige la variante de más calidad (6 Mbps), que en
  Atresmedia va sin marcas. Se eliminaron `lib/inband.js`, sus pruebas y los enganches de `tile.js`,
  `adbreak.js` y `app.js`. La primera variante es la fuente correcta; no hay que reintroducirlo.
- **`_best_variant`**: elegía la variante de más bitrate, donde no hay marcas. Sustituido por
  `_first_variant`.
- **Detector por firma de audio**: descartado por falsos positivos (7 de 15 minutos en ficción).

## Fases

- [x] Investigación y pruebas de los resolvedores (curl).
- [x] `tdtlocal/tdtspain.py` + integración en `feeds`/`store`/`server`.
- [x] `tdtlocal/adwatch/` (porte de `hls.py`, `dash.py`, `scte35.py`, `id3.admode` y el motor).
- [x] Sustituir el motor de `scte_watch.py` y publicar estado por canal.
- [x] Frontend: resolver `tdtspain://` al sintonizar y auto-mute desde el estado.
- [x] Tests y arranque real.

## Verificado

- `python3 -m unittest discover -s tests -t .` y `node --test`, en verde.
- Servidor real con el catálogo: 248 canales de TDT Spain, La 1 por su HLS de RTVE, Antena 3 por su HLS de
  Atresplayer y Cuatro por el DAI de Mediaset (playlist y proxy, 200).
- **Corte real de Atresmedia** (3-nov-2026, Antena 3): el vigilante sobre la primera variante (405000) vio el
  `SCTE35-OUT` de las 22:31:58, pasó a `publicidad` al cumplirse el lag y publicó el `cue-out`.
- **Corte real de Mediaset** (3-nov-2026, Cuatro): el motor detectó la publicidad por el `admode` del DAI (la sesión
  arrancó a mitad del corte), publicó el `cue-out` con el tiempo restante y el `left` fue bajando segundo a segundo.
- Auto-mute de punta a punta en el navegador con una playlist de prueba: a los ~18 s del `SCTE35-OUT` el estado pasa a
  `publicidad` y el `<video>` queda `muted`; al llegar el `cue-in` el sonido vuelve a los ~3 s.

## Pendiente / limitaciones

- Los cinco canales de ETB que solo emiten DASH (`.mpd`) no se reproducen: hls.js no soporta DASH.
- DMAX ya no emite por internet según el propio catálogo (`msgerror`): queda sin fuente.
- Los once canales temáticos de Atresplayer ya van por su HLS como los seis principales: sus ids se sacaron del bundle
  del reproductor y la publicidad se silencia desde el servidor. El puente se retiró del proyecto.
- `stream2` (DMAX) y `stream14`/`posturl` de canales ocultos no tienen resolvedor porque no hay ninguno visible que
  los use.
- No hay una segunda vía para los canales que no publican SCTE-35 en su lista (RTVE, U7D, YouTube y los embeds de
  Atresplayer): no se pueden silenciar desde aquí. La primera variante del master es obligatoria; las de más calidad
  no llevan marcas.
