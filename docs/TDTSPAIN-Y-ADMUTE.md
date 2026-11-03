# Catálogo TDT Spain y auto-mute

Estado: implementado. Este documento fija el diseño y lo que ya está hecho, para poder retomarlo.

## Lo que se pide

1. La lista de canales del visor deja de ser la de TDTChannels: se usa la de la app
   **TDT Spain** (`canalesv4.json`, 263 canales, 248 visibles), con sus grupos, logos y `epgid`.
2. La detección de publicidad no es la de `scte_watch.py` (mirar la playlist al servirla): se usa el
   motor de **`~/Proyectos/tdt-datos`** (`vigilancia.py`: sondeo de la variante cada segundo,
   `EXT-X-DATERANGE`/`CUE-OUT`/`CUE-IN`, SCTE-35 decodificado, ID3 `admode`, `EventStream` del MPD,
   estado `programa`/`publicidad` y segundos restantes).
3. La reacción es el **auto-mute de `~/Proyectos/tdtspain-admute/build/src/com/tv/viewer/probe/AdMute.java`**:
   silenciar al empezar el corte y devolver el sonido al terminar, con su lag, su duración efectiva,
   sus márgenes de cierre y su red de seguridad. Sin intervención del usuario. La vía exacta es la
   misma que la de la app: el SCTE-35 **in-band** del segmento, aplicado en el `pts` de la muestra.

## Decisiones

- **Fuente por defecto**: `https://www.tdtspain.com/canales/canalesv4.json`. Se mantiene
  `--source` y la constante de TDTChannels para volver atrás. La radio sigue en `RADIO.md`.
- **Canales ocultos** (`ocultar: 1`, 15 en el catálogo) no se listan.
- **Resolución perezosa**: los streams con token o scraping (`stream10` Mediaset, `stream12`
  Dailymotion, `geturl`, `stream14`) no se resuelven al arrancar; se resuelven al sintonizar, como
  ya hace `u7d` (`/api/u7d/play`). Así no caducan en la lista.
- Los canales que solo son un `.mpd` (5 de ETB) quedan como «solo web»: hls.js no reproduce DASH.
- El `epgid` del catálogo es el mismo que el de TDTChannels: la EPG actual sigue valiendo.

## Resolvedores (probados con curl el 3-nov-2026)

| Tipo | Canales | Cómo |
|---|---|---|
| directo | 160 | URL `m3u8` tal cual |
| `youtube` | 15 | URL de YouTube (el visor la convierte a embed) |
| `stream11` | 17 | HLS de Atresplayer (`tdtspain.py`), con su SCTE-35 dentro; sin iframe ni puente |
| `stream1` RTVE | 11 | HLS fijo `rtvelivestream.rtve.es/rtvesec/...` (la1, la2, 24h, tdp, clan, can/*, cat/*) |
| `stream10` Mediaset | 14 | login anónimo → `playback/check` → `pubads` → `stream_manifest` (HLS en claro) |
| `stream12` Dailymotion | 4 | `geo.dailymotion.com/video/<id>.json` → `qualities.auto[0].url` |
| `geturl` | 20 | GET de la web + regex `patron` + `baseurl` (probados los 20) |
| `stream14` Aragón | 1 | GraphQL `eu.next.mediastream.co` → `accessToken` → `mdstrm` |
| `stream2` DMAX | 1 | no emite (`msgerror` en el catálogo): solo web |

## Auto-mute

Hay dos caminos. El in-band manda cuando el canal lo trae; la playlist es el respaldo y la única vía
en Mediaset, RTVE, U7D y los reproductores incrustados.

### 1. SCTE-35 in-band (Atresmedia, el mismo segundo)

- El `splice_insert`/`time_signal` viaja en **ID3 `PRIV`** dentro del PID de metadatos 0x23 del
  segmento, con el `pts` de la muestra. La app Media3 lo recibe en `Player.Listener.onMetadata`; en
  el navegador lo entrega hls.js en `FRAG_PARSING_METADATA`.
- `tdtlocal/static/js/lib/inband.js` lee los marcos ID3, se queda con los `PRIV` cuyo propietario
  contiene `scte35` y decodifica el `splice_info_section`: `splice_insert` (con
  `out_of_network`) y `time_signal` (por su `segmentation_type`: `0x22` Break Start, `0x23`
  Break End, y los `0x30`-`0x37`/`0x40`-`0x45`). Un Program Start (`0x10`) no es publicidad.
- El `pts` de la muestra ya está en la línea de tiempo del reproductor (hls.js lo usa tal cual como
  inicio de la cue). `tile.js` arma un `setTimeout` con `pts - currentTime`, como el `Handler` de
  AdMute; `adbreak.js` aplica el cambio al llegar. Si el reproductor está pausado o el reloj se
  desvía, al disparar se vuelve a medir la distancia en vez de silenciar antes.
- Los dos `PRIV` del mismo corte (splice_insert y time_signal) se deduplican. Una marca que ya pasó
  más de 5 s se deja al respaldo de la playlist.
- El `Break End` cierra en su muestra exacta. Si no llega, la playlist sigue siendo la red de
  seguridad y el `cue-out` le presta su duración.

### 2. Playlist (respaldo y Mediaset)

- Sondeo de la playlist de la **variante** (no el master), cada 2 s; Mediaset cada 1 s sobre la
  playlist DAI.
- El estado se decide **recorriendo los tags en orden** y quedándose con el último:
  `SCTE35-OUT`/`CUE-OUT`/`CUE-OUT-CONT` → publicidad; `SCTE35-IN`/`CUE-IN`/`admode=in` → programa.
- Un corte cerrado recuerda el `ID` de su `DATERANGE`: si el tag se queda atrás, no vuelve a silenciar.
- `LAG_MS = 18000` en Atresmedia (el playhead va 18 s por detrás del directo); `0` en Mediaset (la
  sesión DAI ya entrega el tiempo restante).
- Duración efectiva = `DURATION`/`CUE-OUT:<s>`/`out_<ms>` menos el retraso ya consumido.
- Cierre por duración: margen 18 s (Atresmedia) / 5 s (Mediaset). El cierre bueno es el `CUE-IN`.
- El `CUE-IN` **también espera al lag**: la lista va por delante de la imagen, así que un `CUE-IN`
  recién visto todavía no ha llegado a pantalla. `state_of()` solo cierra cuando su hora
  (`START-DATE` o el instante en que se vio) ha entrado en el reloj de la imagen (`now - lag`).
- Si el `CUE-IN` del DAI llega antes que la emisión (`earlyMediasetIn`), no se devuelve el sonido
  hasta que se cumpla la duración prevista.
- `MAX_MUTE_MS = 900000` como red de seguridad.
- En el navegador el volumen se aplica al `<video>` de la baldosa (hls.js), no a un ExoPlayer.

## Fases

- [x] Investigación y pruebas de los resolvedores (curl).
- [x] `tdtlocal/tdtspain.py` + integración en `feeds`/`store`/`server`.
- [x] `tdtlocal/adwatch/` (porte de `hls.py`, `dash.py`, `parse_scte35`, `analizar_id3`, motor).
- [x] Sustituir el motor de `scte_watch.py` y publicar estado por canal.
- [x] Frontend: resolver `tdtspain://` al sintonizar y auto-mute desde el estado.
- [x] Tests y arranque real.

## Verificado

- `python3 -m unittest discover -s tests -t .` → 444 pruebas.
- `node --test` → 118.
- Servidor real con el catálogo: 248 canales de TDT Spain, La 1 por su HLS de RTVE, Antena 3 por el embed de
  Atresplayer, Cuatro por el DAI de Mediaset (playlist y proxy, 200) y Real Betis TV por la página del club.
- In-band: parser probado con los `PRIV` reales del segmento `448407824.ts` de Antena 3 (los mismos fixtures de
  `AdMuteTest`) y con el `time_signal` `0x22`/`0x23`. Falta verlo en un corte real de Atresmedia en el navegador.
- Auto-mute de punta a punta en el navegador con una playlist de prueba (`tests`/scratch): a los ~18 s del `SCTE35-OUT`
  el estado pasa a `publicidad` y el `<video>` queda `muted`; al cumplirse la duración (30 s + 18 de margen) llega el
  `cue-in` y el sonido vuelve a los ~3 s (`END_MARGIN`).
- **Corte real de Mediaset** (3-nov-2026, Cuatro): el motor detectó la publicidad por el `admode` del DAI (la sesión
  arrancó a mitad del corte), publicó el `cue-out` con el tiempo restante y el `left` fue bajando segundo a segundo.

## Pendiente / limitaciones

- Los cinco canales de ETB que solo emiten DASH (`.mpd`) no se reproducen: hls.js no soporta DASH.
- DMAX ya no emite por internet según el propio catálogo (`msgerror`): queda sin fuente.
- Los once canales temáticos de Atresplayer ya van por su HLS como los seis principales: sus ids se sacaron del bundle
  del reproductor y la publicidad se silencia desde el servidor. El puente se retiró del proyecto.
- `stream2` (DMAX) y `stream14`/`posturl` de canales ocultos no tienen resolvedor porque no hay ninguno visible que
  los use.
- El camino in-band solo existe donde el canal mete ID3 `PRIV` en el segmento (Atresmedia). Mediaset, RTVE, U7D y
  los embeds siguen por playlist; YouTube y el iframe de Atresplayer no se pueden silenciar desde aquí.

