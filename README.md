# TDT local — fork con el catálogo de TDT Spain

Visor web **local** para ver la TDT española y escuchar la radio en el PC, sin abrir las apps de cada cadena.

Este repositorio es un **fork de [cibernicola/tdt-local](https://github.com/cibernicola/tdt-local)** (Apache-2.0).
La tele ya no sale de la lista de TDTChannels: sale del catálogo de la aplicación **TDT Spain**
(`canalesv4.json`, 248 canales con sus grupos, logos y guía, y los directos de RTVE, Atresmedia, Mediaset y las
autonómicas resueltos como los resuelve esa app). La radio sigue viniendo de las listas públicas de
[TDTChannels](https://github.com/LaQuay/TDTChannels). La publicidad se detecta con el motor de
[`tdt-datos`](https://github.com/cibernicola/tdt-datos) y se silencia sola, con las reglas de la app TDT Spain.

Solo biblioteca estándar de Python: no hay nada que instalar con `pip`. El asistente de IA y los subtítulos son
opcionales y usan programas que ya tengas (un servidor compatible con OpenAI, ffmpeg y whisper.cpp); sin ellos, todo
lo demás funciona igual.

> **In English:** a fork of [cibernicola/tdt-local](https://github.com/cibernicola/tdt-local), a local web viewer for
> Spanish digital TV and radio. TV comes from the **TDT Spain** app's catalogue (`canalesv4.json`) and radio from the
> public [TDTChannels](https://github.com/LaQuay/TDTChannels) lists. Python standard library only; HLS playback through
> a local proxy, programme guide (XMLTV), 4-channel multi-view, optional local AI assistant and live subtitles. Ad
> breaks are detected with the `tdt-datos` engine and muted automatically, the way TDT Spain's app does it. The
> interface is in Spanish.

## Qué cambia respecto al proyecto original

- **Catálogo de canales de TDT Spain** en lugar de `TELEVISION.md`: 248 canales visibles, con grupos, logos y
  `epgid`, y canales que la lista anterior no traía (Antena 3, laSexta, Neox, Nova, Mega, Atreseries, todo Mediaset,
  los temáticos de Atresplayer y las autonómicas y locales).
- **Resolvedores de los directos**: RTVE por su HLS público sin DRM, los 17 canales de Atresplayer por su propio HLS,
  Mediaset por su DAI (que devuelve HLS en claro), Dailymotion, páginas con el reproductor dentro y el GraphQL de
  Aragón TV. Los enlaces con tokens se resuelven al sintonizar, no al arrancar.
- **Auto-mute de la publicidad**, activo por defecto, con el motor de `tdt-datos` y la lógica de `AdMute` de la app
  TDT Spain: sondeo de la variante, retardo del directo (18 s; 0 en Mediaset), duración con margen y cierre por
  `CUE-IN`.
- **Sin puente de Atresplayer**: los canales de Atresmedia se reproducen por su HLS y los silencia el servidor, así
  que el userscript/extensión y su protocolo se retiraron. Tampoco está el botón «Añadir Atresmedia», porque el
  catálogo ya los trae.
- **Iconos**: SVG y AVIF además de PNG/JPEG/GIF/WEBP; en tema oscuro se muestran en monocromo claro (las fotos de
  Facebook, invertidas para que el fondo blanco desaparezca).
- **`iniciar.sh` para una sola instancia**: antes de arrancar, para la que ya esté en marcha.

Lo demás —proxy local, guía, U7D, multivisión, canales propios, asistente, subtítulos, sonido— es el trabajo del
proyecto original y sigue igual.

## Capturas

La interfaz es la misma que la del proyecto original (las capturas están hechas con su aplicación de escritorio):

| | |
|---|---|
| ![Vista principal con la lista de canales](docs/img/principal.png) | ![U7D: canales con grabaciones de la última semana](docs/img/u7d-canales.png) |
| ![Ajustes ▸ Plugins](docs/img/plugins.png) | ![Ajustes ▸ Registro](docs/img/registro.png) |

## Estado

**✅ Hecho**: implementado y con pruebas automáticas. **🧪 Probado poco**: funciona, pero solo se ha comprobado en
condiciones simuladas o con pocos casos reales. **📋 Pendiente**: aún no existe (la [hoja de ruta](ROADMAP.md) tiene
más ideas).

| Característica | Estado |
|---|---|
| Catálogo de TDT Spain para la tele (248 canales, cada 6 h, con ETag) y radio de TDTChannels | ✅ Hecho |
| Reproducción de directos HLS con proxy local | ✅ Hecho |
| Radio (unas 980 emisoras, con su logo en lugar de vídeo) | ✅ Hecho |
| Comprobar qué canales y emisoras responden | ✅ Hecho |
| Multivisión de cuatro canales con audio rotativo | ✅ Hecho |
| Guía de programación (EPG) | ✅ Hecho · cubre unos 223 canales de tele y unas 50 emisoras |
| Directos de YouTube incrustados | ✅ Hecho |
| Canales propios, con cabeceras `Origin` y `Referer` | ✅ Hecho |
| U7D de RTVE: lo emitido en los últimos 7 días por La 1, La 2, 24 Horas, Teledeporte y Clan | 🧪 Probado poco · con la red real; los programas con DRM se avisan y no se reproducen |
| U7D de Atresmedia (solo en la aplicación de escritorio, plugin `atresplayer-u7d`) | 🧪 Probado poco · del proyecto original |
| Favoritos, último canal, volumen y filtros (en el navegador) | ✅ Hecho |
| Ajustes desde la interfaz, sin reiniciar | ✅ Hecho |
| Modo navegador, con el botón «Salir» (`--quit-button`) | ✅ Hecho |
| Aplicación de escritorio con ventana propia (Electron), bandeja, plugins y Registro | 🧪 Probado poco · del proyecto original; este fork no la empaqueta |
| Asistente de IA local (órdenes y preguntas) | ✅ Hecho · necesita un servidor compatible con OpenAI |
| Subtítulos en directo con Whisper | 🧪 Probado poco · alucina con música y voces solapadas |
| Alertas por voz (palabras vigiladas en las cuatro casillas) | 🧪 Probado poco · misma limitación |
| Marcas de anuncios SCTE-35: detección con el motor de `tdt-datos`, registro y exportación (JSON y CSV) | ✅ Hecho · comprobado con streams reales |
| Aviso en pantalla cuando aparece una marca nueva | ✅ Hecho |
| Marcas SCTE-35 dentro del vídeo (PID del MPEG-TS, eventos `emsg`) | 🧪 Probado poco · solo con datos sintéticos |
| **Auto-mute de la publicidad**, como la app TDT Spain | ✅ Hecho · probado con un corte simulado y con un corte real de Mediaset |
| Durante la publicidad: cambiar a otro canal y volver | 🧪 Probado poco · con un directo simulado |
| Durante la publicidad: mostrar una web sobre la imagen, con comprobación previa | 🧪 Probado poco · con un directo simulado |
| Durante la publicidad: respetar o deshacer lo que toques | 🧪 Probado poco · con un directo simulado |
| Igualador de volumen y ecualizador de diez bandas, globales o por canal | 🧪 Probado poco · con señales sintéticas |
| Aviso fijo «PUBLICIDAD» sobre la imagen mientras dura una pausa | ✅ Hecho |
| Mediaset dentro del visor (Telecinco, Cuatro, FDF, Divinity, Energy, Be Mad, Boing y los de Infinity) | ✅ Hecho · sesión DAI y HLS en claro |
| Iconos SVG y AVIF, y en monocromo claro sobre el tema oscuro | ✅ Hecho |
| Sacar el enlace de un directo desde tu navegador (`tools/capturar-directo.js`) | 🧪 Probado poco · con una página de ejemplo |
| Controles propios del reproductor: calidad, pistas de audio, estadísticas, «volver al directo», retroceso | 📋 Pendiente |
| Reproductor ampliable con plugins | 📋 Pendiente |
| Sustituir los anuncios por otro contenido | 📋 Pendiente |

## Arrancar

Hace falta **Python 3.9 o superior**. Este fork **no publica instaladores ni releases**: se arranca desde el código.
Los instaladores del proyecto original (sin estos cambios) están en
[su página de Releases](https://github.com/cibernicola/tdt-local/releases/latest).

- **Linux / macOS:** `./iniciar.sh` — antes de arrancar para la instancia que ya esté en marcha.
- **Windows:** doble clic en `iniciar.bat`.
- **A mano:** `python -m tdtlocal --open`
- **Con pip:** `pip install .` y luego `tdt-local --open` (o `tdt-local-app`, la versión de escritorio).

El servidor escucha en `http://127.0.0.1:8788/`. Para cerrarlo: `Ctrl+C` en la terminal, el botón **Salir** si lo
arrancaste con `--quit-button`, o `kill` al proceso. La primera vez hace falta conexión para descargar el catálogo;
después se usa la última copia guardada en `data/`.

**Pruebas:**

```
python -m unittest discover -s tests -t .            # Python (sin dependencias)
node --test                                           # código del navegador (Node 20+)
python scripts/check.py                               # todo lo anterior y el lint, si está instalado
pip install ruff pytest && ruff check . && ruff format --check .
```

## Qué hace

- **Catálogo al día:** descarga `canalesv4.json` al arrancar y lo revisa cada 6 h (con ETag, así que no baja nada si
  no ha cambiado). Botón «Actualizar lista» para forzarlo.
- **Reproducción:** directos HLS con hls.js (incluido en `tdtlocal/static/vendor/`, sin CDN). Todo pasa por un proxy
  local (`/proxy`) porque casi ningún stream envía cabeceras CORS.
- **Radio:** selector «Televisión / Radio» (o `T`) con unas 980 emisoras. mp3/aac/«stream» van directos al
  reproductor del navegador y las de m3u8 con hls.js. Se muestra el logo en lugar de vídeo.
- **Comprobar canales:** pide a cada stream su lista, una variante y el último segmento (en radio, los primeros bytes
  de audio) y marca cada uno en verde o rojo. «Ocultar caídos» los filtra.
- **Multivisión** (`V`): cuatro canales a la vez; solo suena la casilla activa. Clic en una casilla (o `← →`) le pasa
  el audio. «Cuadrícula» reparte 2×2 y «Foco» agranda la activa. «Rotar» pasa el audio a la siguiente casilla cada
  10–60 s.
- **Guía de programación (EPG):** descarga cada hora los XMLTV de TDTChannels y los casa con la columna «EPG ID».
  Muestra el programa en emisión y el siguiente; `G` abre la programación completa del canal activo. Cubre unos 223
  canales de tele y unas 50 emisoras; `--no-epg` la desactiva.
- **YouTube:** los canales cuyo directo es una página de YouTube se reproducen con el reproductor oficial incrustado,
  también en el mosaico. No pasan por el proxy.
- **U7D** (botón junto a Televisión y Radio): los canales con «últimos 7 días» (hoy, los de RTVE). Al entrar en uno
  salen sus programas de la última semana, del más reciente al más antiguo, con su miniatura y un buscador; un programa
  se reproduce como un canal. RTVE publica la lista y cada grabación por su API pública; los programas con DRM no se
  reproducen y la casilla lo dice.
- **Canales propios:** «Añadir canal» guarda un enlace que pegues tú (m3u8, mp3/aac o YouTube) en `data/custom.json`,
  con cabeceras `Origin`/`Referer` opcionales que el proxy envía con la lista y con cada segmento. No se salta DRM ni
  geobloqueos: solo cabeceras. Para no copiar el enlace a mano hay un
  [fragmento para tu navegador](#sacar-el-enlace-de-un-directo-desde-tu-navegador).
- **Marcas de anuncios (SCTE-35) y auto-mute**, activado por defecto: sigue la lista de reproducción del canal que
  estás viendo, detecta la marca, silencia el reproductor durante la pausa y lo devuelve al terminar. Avisa en
  pantalla y guarda cada hallazgo en `data/scte.jsonl` (se puede descargar en JSON o CSV). Ver
  [más abajo](#marcas-de-anuncios-y-auto-mute).
- **Sonido** (botón *Sonido*): **igualador de volumen** (sube lo que suena bajo y baja lo que suena alto hasta un
  nivel que eliges) y **ecualizador** de 10 bandas con preajustes. Ambos se pueden fijar para todos los canales o solo
  para uno. Ver [más abajo](#sonido).
- **Asistente de IA** (`Ctrl+K`): órdenes como «pon La 2» o «pon La 2, TRECE y El Toro en multivisión rotando cada
  30 s» se resuelven al instante en el navegador; el resto («hay fútbol ahora») va a tu modelo local, que solo recibe
  unas decenas de canales preseleccionados y no puede ejecutar nada que no esté en esa lista. Usa la API de chat de
  OpenAI (llama.cpp u otro servidor compatible).
- **Subtítulos en directo** (`U`) y **alertas por voz**: ffmpeg saca el audio y `whisper-server` (whisper.cpp) lo
  transcribe en tu equipo; el audio no sale de él. Las alertas vigilan palabras en las cuatro casillas y, al oír una,
  resaltan la casilla y le pasan el audio.
- Favoritos, último canal, volumen, filtros, mosaico y rotación se guardan en el navegador. Los logos se descargan una
  vez a `data/logos/`.

## Teclas

| Tecla | Acción |
|---|---|
| 0–9, Intro | ir al número de canal (el de la lista visible) |
| Re Pág / Av Pág, ↑ ↓ | canal anterior / siguiente |
| S · F · M · P · / | favorito · pantalla completa · silencio · ventana flotante · buscar |
| T · V · C · G | televisión/radio · multivisión · ocultar/mostrar la lista · guía |
| ← → · R | multivisión: cambiar de casilla activa · activar/parar la rotación |
| U · Ctrl+K | subtítulos en directo · asistente de IA |

## Ajustes

El botón **Ajustes** (abajo, en el panel de canales) abre la configuración. Todo se guarda en `settings.json`, dentro
de la carpeta de datos, y se aplica al momento, sin reiniciar.

- **Asistente de IA:** activarlo, dirección del servidor (API compatible con OpenAI), modelo (con «Probar conexión» se
  listan los que ofrece el servidor), clave de API opcional, temperatura, máximo de tokens, tiempo de espera, pedir al
  modelo que no «piense» y parámetros extra en JSON (`top_p`, `top_k`…).
- **Subtítulos y alertas:** activarlos, idioma (o detección automática), modelo, carpeta de modelos, programa
  `whisper-server`, dirección del servidor, arranque automático, hilos y minutos de inactividad. Incluye un catálogo
  para **descargar modelos** de Whisper con progreso, cancelación y verificación SHA-256, y un botón «Comprobar» que
  dice qué falta (ffmpeg, el programa, el modelo, el servidor).
- **Listas y guía:** cada cuántas horas se actualizan, comprobar los streams tras actualizar, guía de programación, la
  **guía extra** para los canales sin guía en las listas (desactivada por defecto; hace falta para la guía de
  Atresmedia y para canales propios) y, en «avanzado», las direcciones de origen (catálogo, guías y guía extra).
- **Marcas de anuncios:** activar la vigilancia, qué vigilar (lo que ves, una lista de canales o todos), cada cuántos
  segundos explorar, mirar también dentro del vídeo, avisar en pantalla y cuántas marcas conservar.
- **Durante la publicidad:** qué hacer con el sonido y con la imagen cuando empieza una pausa, y qué pasa si tocas algo.

Los ajustes solo se pueden cambiar desde el propio equipo (aunque uses `--lan`): `whisper-server` es un programa que se
ejecuta y no debe poder elegirlo cualquiera de tu red.

### Línea de comandos y variables de entorno

Lo que fijes por línea de comandos o con una variable `TDT_<AJUSTE>` manda sobre lo guardado: en Ajustes aparece
bloqueado y no se escribe en `settings.json`. Orden de prioridad: valores por defecto, lo guardado en Ajustes, y por
último flag o variable.

```
--port 8788           puerto
--host ADDR           dirección donde escuchar (127.0.0.1)
--lan                 escuchar en toda la red local (tablet, móvil...), SIN autenticación
--open                abrir el navegador
--verbose             registrar cada petición HTTP
--quit-button         mostrar el botón «Salir», que cierra el servidor
--data-dir DIR        carpeta de datos (por defecto ./data, o la carpeta de usuario si está instalado)
--interval HORAS      horas entre actualizaciones (6)
--auto-check          comprobar los streams de la tele tras cada actualización
--source URL          otro catálogo (JSON de TDT Spain o TELEVISION.md de TDTChannels)
--radio-source URL    otra copia de RADIO.md
--epg-source URL      otro XMLTV (.xml o .xml.gz) para la tele
--radio-epg-source URL  otro XMLTV para la radio
--no-epg              no descargar la guía de programación
--llm-url URL         servidor de IA compatible con OpenAI (http://127.0.0.1:8080/v1)
--llm-model NAME      nombre del modelo, si tu servidor lo exige
--no-ai               desactivar el asistente
--models-dir DIR      carpeta de los modelos de Whisper
--whisper-url URL     whisper-server (por defecto :8178); si no responde, se lanza uno
--whisper-bin PATH    whisper-server (por defecto, el del PATH)
--whisper-model PATH  modelo ggml (por defecto, el primero de la carpeta de modelos)
--whisper-lang es     idioma del audio (o auto)
--no-stt              desactivar subtítulos y alertas
--version
```

Cualquier ajuste tiene su variable: el nombre del ajuste en mayúsculas con prefijo `TDT_` (`TDT_LLM_URL`,
`TDT_LLM_API_KEY`, `TDT_WHISPER_BIN`, `TDT_WHISPER_THREADS`, `TDT_STT_ENABLED=0`…). Los nombres están en
[`tdtlocal/settings.py`](tdtlocal/settings.py). `TDT_DATA_DIR` fija la carpeta de datos. En los lanzadores puedes
ponerlas en `iniciar.local.bat` / `iniciar.local.sh`, que no se suben a git.

### Subtítulos con Whisper

1. Instala [ffmpeg](https://ffmpeg.org/) y deja `ffmpeg` en el `PATH`.
2. Compila o descarga [whisper.cpp](https://github.com/ggml-org/whisper.cpp) y pon `whisper-server` en el `PATH`, o
   indica su ruta en Ajustes ➜ Subtítulos (o con `--whisper-bin`).
3. En Ajustes ➜ Subtítulos, elige un modelo del catálogo y pulsa **Descargar** (por ejemplo `large-v3-turbo-q5_0`,
   574 MB, el recomendado con GPU). Pulsa **Comprobar** para confirmar que todo está en su sitio.

Si no hay un `whisper-server` en marcha, el visor lanza uno y lo apaga tras unos minutos sin uso.

## Canales: el catálogo de TDT Spain

La lista de la tele es la de la aplicación [TDT Spain](https://www.tdtspain.com/) (`canalesv4.json`, 248 canales
visibles). Trae Antena 3, laSexta, Neox, Nova, Mega, Atreseries, todos los canales de Mediaset, los temáticos de
Atresplayer y las autonómicas y locales de cada comunidad, con su grupo, su logo y su `epgid` (el mismo que usa la
guía).

Cada canal se reproduce como la app resuelve su directo:

| Tipo | Canales | Cómo |
|---|---|---|
| Directo HLS | la mayoría | la URL `m3u8` del catálogo, por el proxy local |
| YouTube | informativos internacionales | el reproductor incrustado de YouTube |
| Atresplayer | los 17 canales: Antena 3, laSexta, Neox, Nova, Mega, Atreseries y los temáticos (Multicine, Clásicos, Comedia, Kidz, Flooxer…) | su propio HLS, resuelto al sintonizar y servido por el proxy: el SCTE-35 viene dentro y el motor lo silencia |
| RTVE | La 1, La 2, 24h, Teledeporte, Clan y las versiones de Canarias y Cataluña | HLS público de `rtvelivestream.rtve.es`, sin DRM |
| Mediaset | Telecinco, Cuatro, FDF y los de Infinity | sesión anónima → `playback/check` → DAI de Google, que da un HLS en claro |
| Dailymotion | Melilla TV, Navarra TV, Bolivia TV, 7 Rioja… | el JSON del reproductor |
| Páginas con el directo dentro | La 7 y las La 8 de Castilla y León, TAC12, Televisión Canaria… | se lee la página y se saca el `m3u8` |
| Solo DASH (`.mpd`) | los cinco canales de ETB que solo emiten así | quedan como «solo web»: el navegador no reproduce DASH |

Los que necesitan una receta (Mediaset, Atresplayer, Dailymotion, las páginas) **no se resuelven al arrancar**: se
resuelven al sintonizar, porque sus enlaces llevan tokens que caducan en minutos. Se puede volver a la lista de
TDTChannels con `--source` y la URL de su `TELEVISION.md`.

## Marcas de anuncios y auto-mute

SCTE-35 es el estándar con el que una emisión marca dónde empieza y acaba una pausa publicitaria. El visor lo busca
con el mismo motor de [`tdt-datos`](https://github.com/cibernicola/tdt-datos), lo apunta y, por defecto, **silencia la
publicidad** como hace la app TDT Spain. Se puede apagar en **Ajustes ▸ Marcas de anuncios**; el botón **Marcas**
abre el registro.

Dónde mira:

- **El canal que estás viendo:** al sintonizar, el servidor empieza a seguir su lista de reproducción de medios (la
  variante de más calidad, no el maestro) cada 2 s, o cada 1 s en Mediaset. Recorre los tags en orden y se queda con el
  estado: `#EXT-X-DATERANGE` con `SCTE35-OUT`/`SCTE35-IN` (que se **decodifican**: `splice_insert`, `time_signal` y
  sus descriptores de segmentación), `#EXT-X-CUE-OUT`/`CUE-OUT-CONT`/`CUE-IN`, los anuncios intercalados de HLS
  (`X-ASSET-URI`), el ID3 `admode` que Mediaset deja en `X-MESSAGE-DATA` y el `EventStream` SCTE-35 del MPD.
- **El retardo del directo:** la lista va por delante de la imagen (unos 18 s en Atresmedia), así que el estado se
  evalúa como era hace ese retardo; en Mediaset es 0 porque la sesión DAI ya entrega el tiempo restante. Al empezar la
  pausa, el servidor escribe en el registro un `cue-out` ya sincronizado con lo que ves y la página silencia el
  reproductor; al terminar (por el `CUE-IN` o al cumplirse la duración, con 18 s de margen y 5 s en Mediaset) escribe
  el `cue-in` y devuelve el sonido. Si el `CUE-IN` del DAI llega antes que la emisión, no se hace caso hasta que se
  cumple la duración prevista.
- **Exploración activa:** cada `scte_interval` segundos (60 por defecto) se descarga la lista de los canales que
  indiques, o de todos los que tienen directo, y se analiza igual. No se tocan los marcados como caídos.
- **Dentro del vídeo** (opcional): se baja el principio (256 KB) de un segmento por canal y se busca el PID de datos
  de SCTE-35 en la tabla PMT del MPEG-TS (`stream_type 0x86` o registro `CUEI`) o eventos `emsg` en fMP4.

Qué guarda: cada hallazgo (fecha, canal, tipo, detalle, etiqueta original y mensaje decodificado) en `scte.jsonl`, una
línea por marca y sin repetir la misma marca mientras siga en la lista. Se conservan las últimas 2000 (ajustable). La
URL se guarda **sin los parámetros de la consulta**. El botón **Marcas** abre el registro, permite explorar al momento,
descargarlo (JSON o CSV) o vaciarlo.

Lo que no hace: no corta la publicidad ni la sustituye; solo silencia el reproductor o cambia lo que ves mientras dura
la pausa, y no analiza la señal de radiofrecuencia de la TDT (solo los streams que enlazan las listas). Una marca solo
vive unos segundos en la lista de reproducción, así que la exploración cada minuto puede perder pausas cortas; el canal
que tienes puesto se sigue cada segundo. Que un canal no muestre marcas no prueba que no las use: muchas cadenas
insertan los anuncios en el servidor y no dejan rastro.

## Durante la publicidad

**Ajustes ▸ Durante la publicidad** permite que la página reaccione cuando el canal que estás viendo entra en una
pausa de anuncios. El servidor sigue su playlist, detecta la marca SCTE-35 y avisa a la página ya sincronizado con la
imagen (descuenta el retardo del directo: 18 s en Atresmedia, 0 en Mediaset).

- **Aviso fijo:** un rótulo rojo «PUBLICIDAD» sobre la imagen mientras dura la pausa (se puede desactivar).
- **Con el sonido:** no tocarlo, silenciarlo o bajarlo a un porcentaje del volumen que tengas.
- **Con la imagen:** dejarla, cambiar a otro canal (por nombre o identificador) o mostrar una web sobre la imagen.
  Algunas webs no permiten que se muestren dentro de otra página: el botón **Comprobar** de los ajustes lo avisa. Solo
  se permite esa dirección en la política de seguridad de la página.
- **Cuándo termina:** al llegar la marca de fin de pausa, o cuando pasa la duración que dice la marca más el margen
  (18 s en Atresmedia, 5 s en Mediaset). Si la marca no dice cuánto dura, se supone el tiempo que indiques (120 s por
  defecto). Al cambiar de canal, en vista simple el canal original **sigue en segundo plano** (en otra casilla, fuera
  de la vista y silenciado): así se sigue viendo cuándo acaba la pausa y se vuelve a él al instante. En multivisión se
  cambia la propia casilla y se vuelve por tiempo.
- **Pausas cortas:** se ignoran las que dicen durar menos de unos segundos (10 por defecto).
- **Si tocas algo durante la pausa** (el volumen o el canal): puedes elegir *respetar lo que hagas*, de modo que no se
  deshaga nada más, o *dejarlo todo como estaba* al terminar la pausa.

## Sacar el enlace de un directo desde tu navegador

Para un canal propio hay que pegar el enlace `.m3u8` del directo. `tools/capturar-directo.js` lo saca por ti **de lo
que tu navegador ya está reproduciendo**:

1. Abre la página del directo (por ejemplo la de laSexta en atresplayer.com), acepta o rechaza las cookies como
   prefieras y deja que el vídeo empiece.
2. Pulsa F12, abre la pestaña **Consola**, pega todo el contenido de `tools/capturar-directo.js` y pulsa Intro (si el
   navegador lo pide, escribe antes `allow pasting`). Si el visor no está en el puerto 8788, cambia `PUERTO` al
   principio.
3. Se abre una pestaña del visor con el diálogo **Añadir canal** relleno (nombre, enlace y, en Atresplayer, las
   cabeceras). Revísalo y pulsa **Añadir**. El enlace también queda copiado al portapapeles.

Qué hace y qué no: lee las listas `.m3u8` que la propia página ya ha pedido y elige la más probable; no hace ninguna
petición a la web, no se salta registros, DRM ni geobloqueos, y no funciona con lo que el navegador no esté
reproduciendo. Los enlaces suelen llevar un permiso temporal y **caducan**. El enlace con el que se abre el visor lleva
todo en el fragmento (`#…`), que el navegador no envía a ningún servidor, y la página lo borra de la barra de
direcciones al leerlo. Solo rellena el formulario: no añade nada hasta que pulsas el botón.

## Sonido

El botón **Sonido** abre un diálogo con dos herramientas, que se pueden usar a la vez:

- **Igualar el volumen:** mide cuánto suena el canal (en una escala tipo LUFS, ponderada como ITU-R BS.1770) y ajusta
  la ganancia hasta el **nivel al que igualar**. Es un control automático en los dos sentidos: sube los canales que
  suenan bajos (hasta el máximo que indiques, 12 dB por defecto) y baja los que suenan altos (hasta 24 dB), rápido
  cuando llega algo fuerte —un anuncio— y despacio al subir. Un limitador evita que lo que sube llegue a saturar.
- **Ecualizador:** diez bandas (31 Hz a 16 kHz, ±12 dB) y preajustes (plano, voz clara, graves, agudos, en V, noche).

Cada ajuste vale **para todos los canales** o **solo para el canal que ves**: un canal con ajuste propio manda sobre el
global, y cada parte (igualador, ecualizador) se puede dejar propia o volver a la global por separado. Se guardan en el
navegador. El diálogo muestra en vivo cuánto llega el canal y cuánto lo corrige.

El volumen y el silencio de siempre siguen funcionando igual: el igualador descuenta el volumen que tengas puesto, así
que bajarlo no hace que el igualador lo suba otra vez. Funciona también con la radio. Límites: el sonido de YouTube no
se puede procesar (va en un iframe ajeno); el navegador solo permite procesar audio tras un primer clic o tecla en la
página; y como mide en directo y sin mirar adelante, es un control aproximado, no una normalización EBU R128 estricta.
El sonido de un reproductor incrustado (YouTube, o un embed de Atresplayer) lo controla él mismo.

## Seguridad

El visor es una herramienta **personal para tu propio equipo**:

- Por defecto solo escucha en `127.0.0.1` y rechaza peticiones cuyo `Host` no sea local (protege frente a DNS
  rebinding), de otro origen (`Sec-Fetch-Site`, `Origin`) o con rutas fuera de `tdtlocal/static/`.
- El proxy solo acepta hosts que salen de las listas (o de redirecciones de streams ya validados), solo `http(s)`, y
  rechaza siempre direcciones no públicas (loopback, red local, metadatos de la nube).
- Cabecera `Content-Security-Policy` estricta: sin scripts ni estilos en línea. Solo se puede incrustar YouTube, los
  embeds de Atresplayer y, si lo configuras, la web de las pausas de anuncios.
- El modelo de IA no es de fiar: sus respuestas se validan y solo se aceptan acciones y canales de la lista que se le
  ofreció.
- `--lan` abre la app a toda tu red local **sin autenticación**: úsalo solo en redes de confianza.

Para avisar de un problema de seguridad, mira [SECURITY.md](SECURITY.md).

## Límites

- Hay canales sin directo que el navegador pueda abrir (los cinco de ETB que solo emiten DASH, o DMAX, que ya no emite
  por internet): salen como «solo web» y se ofrece el enlace a su página. No se intenta saltar ningún DRM ni
  geobloqueo.
- Los streams cambian de URL a menudo. Si uno falla, prueba la otra fuente (botones 1, 2…) o actualiza el catálogo.
- Whisper falla con música y con emisiones fMP4/CMAF; cada casilla transcrita gasta algo de GPU.
- Si cierras el servidor a la fuerza (sin Ctrl+C) puede quedar un `whisper-server` huérfano.
- Si la lista descargada trae menos de 50 canales (30 en radio) se descarta y se conserva la anterior, por si el
  formato del origen cambia.
- Este fork no publica instaladores: para la app de escritorio empaquetada hay que usar la del proyecto original o
  construirla con `packaging/build.py`.

## Desarrollo

La arquitectura está en [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) y la guía para contribuir en
[CONTRIBUTING.md](CONTRIBUTING.md). Los cambios están en [CHANGELOG.md](CHANGELOG.md) y lo que falta por hacer, en
[ROADMAP.md](ROADMAP.md).

```
tdtlocal/            paquete de Python (servidor, proxy, EPG, IA, transcripción)
tdtlocal/tdtspain.py catálogo de TDT Spain y sus resolvedores
tdtlocal/adwatch/    motor de publicidad portado de tdt-datos
tdtlocal/static/     interfaz: index.html, style.css y módulos ES en js/ (lib/ = lógica pura)
tdtlocal/static/vendor/  hls.js (Apache-2.0, con su licencia)
packaging/           PyInstaller, Inno Setup, .dmg, .deb y el script de construcción de instaladores
tests/               pruebas de Python (tests/*.py) y del navegador (tests/js/)
docs/                documentación técnica
data/                (se crea solo, no se versiona) catálogo, estado, logos y canales propios
models/              (no se versiona) modelo de Whisper
```

## Créditos, licencia y aviso

- **El proyecto original** es [cibernicola/tdt-local](https://github.com/cibernicola/tdt-local) (Apache-2.0): el
  servidor, el proxy, la interfaz, la guía, U7D, el sonido y casi todo lo demás es su trabajo. Este fork solo cambia
  el catálogo, los resolvedores, el motor de publicidad y los iconos.
- **Las listas de radio y las guías** son de [TDTChannels](https://github.com/LaQuay/TDTChannels) (Apache-2.0; las
  guías las sirven tdtchannels.com y [Onda Channels](https://www.ondachannels.com/) y están sujetas a su
  [aviso legal](https://www.tdtchannels.com/aviso-legal/)).
- **El catálogo de canales de la tele** es el de la aplicación [TDT Spain](https://www.tdtspain.com/)
  (`canalesv4.json`): se descarga al arrancar y no se redistribuye. Sus directos son los que publican las cadenas.
- **La guía extra** de los canales de Atresmedia viene de [EPG_dobleM](https://github.com/davidmuma/EPG_dobleM) y
  [picons_dobleM](https://github.com/davidmuma/picons_dobleM), de David Muma: archivos de terceros que se descargan
  solo si activas esa guía.
- [hls.js](https://github.com/video-dev/hls.js) (Apache-2.0, © Dailymotion) reproduce el HLS en el navegador.
- Opcionales y no incluidos: [llama.cpp](https://github.com/ggml-org/llama.cpp) (MIT),
  [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (MIT) y [FFmpeg](https://ffmpeg.org) (LGPL/GPL).
- El visor no aloja ni redistribuye ningún contenido: enlaza los directos que las propias cadenas publican
  abiertamente. Los canales y su contenido pertenecen a sus titulares.

### Licencia

[Apache License 2.0](LICENSE). Es libre: puedes usarlo, modificarlo y redistribuirlo, incluso con fines comerciales,
**a condición de citar su origen**: conserva el aviso de copyright, el fichero [NOTICE](NOTICE) (con la cita del
proyecto original, TDTChannels, TDT Spain, hls.js y el resto) y marca los cambios que hagas.
