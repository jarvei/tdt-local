"""The channel list of the TDT Spain app (``canalesv4.json``) and the resolution of its streams.

The app publishes a JSON catalogue with 263 channels and, for each one, either a plain HLS URL or the
recipe its player uses to get one (RTVE's token, Atresplayer's API, Mediaset's DAI session, a web page
where the stream is embedded...). :func:`parse_catalog` turns that into the channel dicts this project
stores; :func:`resolve` follows the recipe when the channel is tuned, because most of those URLs carry
tokens that expire in a couple of hours.

Channels that need a recipe keep ``tdtspain://<id>`` as their only source. The page asks the server
for the real URL at play time (``/api/tdtspain/play``), the same way it already does for U7D.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .net import USER_AGENT, Net
from .util import slugify

CATALOG_URL = "https://www.tdtspain.com/canales/canalesv4.json"
SCHEME = "tdtspain://"
CATEGORY = "Televisión"

# RTVE's public HLS feeds. The app resolves a token and plays the MPD (DASH + Widevine), which the
# page cannot play; these are the same channels in HLS, without DRM. Keys are the catalogue's ids.
RTVE_HLS = {
    "La1.TV": "https://rtvelivestream.rtve.es/rtvesec/la1/la1_main_dvr.m3u8",
    "La2.TV": "https://rtvelivestream.rtve.es/rtvesec/la2/la2_main_dvr.m3u8",
    "24Horas.TV": "https://rtvelivestream.rtve.es/rtvesec/24h/24h_main_dvr.m3u8",
    "TDP.TV": "https://rtvelivestream.rtve.es/rtvesec/tdp/tdp_main.m3u8",
    "Clan.TV": "https://rtvelivestream.rtve.es/rtvesec/clan/clan_main_dvr.m3u8",
    "La1_CAN.TV": "https://rtvelivestream.rtve.es/rtvesec/can/la1_can_main_dvr.m3u8",
    "La2_CAN.TV": "https://rtvelivestream.rtve.es/rtvesec/can/la2_can_main_dvr.m3u8",
    "24H_CAN.TV": "https://rtvelivestream.rtve.es/rtvesec/can/24h_can_main_dvr.m3u8",
    "La1_CAT.TV": "https://rtvelivestream.rtve.es/rtvesec/cat/la1_cat_main_dvr.m3u8",
    "La2_CAT.TV": "https://rtvelivestream.rtve.es/rtvesec/cat/la2_cat_main_dvr.m3u8",
    "24H_CAT": "https://rtvelivestream.rtve.es/rtvesec/cat/24h_cat_main_dvr.m3u8",
}

# Mediaset: channel code for its playback API and the page the DAI session is opened against.
# The catalogue's ``url`` is that same page, so the slug is enough.
MEDIASET = {
    "Cuatro.TV": ("CT", "https://www.mediasetinfinity.es/directo/cuatro/"),
    "Telecinco.TV": ("T5", "https://www.mediasetinfinity.es/directo/telecinco/"),
    "FDF.TV": ("FD", "https://www.mediasetinfinity.es/directo/fdf/"),
    "Divinity.TV": ("DV", "https://www.mediasetinfinity.es/directo/divinity/"),
    "Energy.TV": ("EN", "https://www.mediasetinfinity.es/directo/energy/"),
    "Bemad.TV": ("BM", "https://www.mediasetinfinity.es/directo/bemad/"),
    "Boing.TV": ("BO", "https://www.mediasetinfinity.es/directo/boing/"),
    "Mitele_Comedia.TV": ("MC", "https://www.mediasetinfinity.es/directo/mitele-comedia/"),
    "Mitele_Viajes.TV": ("MV", "https://www.mediasetinfinity.es/directo/mitele-viajes/"),
    "Mitele_Calle.TV": ("ME", "https://www.mediasetinfinity.es/directo/mitele-en-la-calle/"),
    "Mitele_Series.TV": ("MS", "https://www.mediasetinfinity.es/directo/mitele-top-series/"),
    "mtmad24h.TV": ("MT", "https://www.mediasetinfinity.es/directo/mtmad-24h/"),
}

MEDIASET_LOGIN = "https://services-ott-prod-fe.mediaset.net/esp/idm/v3.0/anonymous/login"
MEDIASET_CHECK = "https://services-ott-prod-fe.mediaset.net/esp/playback/v3.0/check"
# Google DAI. Same service as pubads.g.doubleclick.net, whose name ad-blocking DNS sinks to 0.0.0.0.
MEDIASET_DAI = "https://dai.google.com/ssai/event/{}/streams"
MEDIASET_APP = "web//mediasetplay-web/1.5.5-46652b4"
MEDIASET_HEADERS = {"Origin": "https://www.mediasetinfinity.es", "Referer": "https://www.mediasetinfinity.es/"}
MEDIASET_PAGE = "https://www.mediasetinfinity.es/"

DAILYMOTION = "https://geo.dailymotion.com/video/{}.json?legacy=true"
YOUTUBE = re.compile(r"^https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|live/|channel/|@)|youtu\.be/)", re.I)
DMPLAYER = re.compile(r"/video(?:s)?/([a-zA-Z0-9]+)")

# A recipe entry only needs what the resolver reads; the rest of the catalogue entry is dropped.
RECIPE = ("id", "streamtype", "url", "baseurl", "patron", "postdata", "atributtes", "headers", "referer")

EMBED_ATRES = re.compile(r"^https?://(?:www\.)?atresplayer\.com/directos/([a-z0-9-]+)/?", re.I)

# Atresplayer's live channels by slug. Its player API gives the HLS in the clear, with the SCTE-35 tags
# inside, so the server can follow it and mute the break the same way it does for Mediaset. The rest of
# the catalogue's Atresmedia channels (the thematic ones) stay as the embedded player: no id here.
ATRES_PLAYER = "https://api.atresplayer.com/player/v1/live/{}"
ATRES_HEADERS = {"Origin": "https://www.atresplayer.com", "Referer": "https://www.atresplayer.com/"}
ATRES_IDS = {
    "antena3": "5a6a165a7ed1a834493ebf6a",
    "lasexta": "5a6a172c7ed1a834493ebf6b",
    "neox": "5a6a17da7ed1a834493ebf6d",
    "nova": "5a6a180b7ed1a834493ebf6e",
    "mega": "5a6a18357ed1a834493ebf6f",
    "atreseries": "5a6a189a7ed1a834493ebf70",
    # the thematic channels (taken from the ids the embed uses, in its own player bundle)
    "canal-kidz": "648c271d2bfab0e4177a0d61",
    "canal-flooxer": "5c1285e47ed1a861f8125285",
    "multicine": "648ef18c1756b0e41daf83cc",
    "clasicos": "648ef12c2bfab0e4507e0d61",
    "comedia": "648ef23d2bfab0e4557e0d61",
    "mentes-inquietas": "648ef3162bfab0e4587e0d61",
    "el-hormiguero": "648ef5882bfab0e4627e0d61",
    "el-club-de-la-comedia": "648f47f7a2ffb0e40aeff3ad",
    "equipo-de-investigacion": "648ef5551756b0e429af83cc",
    "aqui-no-hay-quien-viva": "648ef3951756b0e425af83cc",
    "foq": "648ef50a2bfab0e4607e0d61",
}


def looks_like(data: bytes | str) -> bool:
    """Is this body the app's JSON catalogue (and not a TDTChannels Markdown table)?"""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    return text.lstrip("\ufeff \r\n\t").startswith("{")


def parse_catalog(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Channels of ``canalesv4.json`` ready for the store, in catalogue order, hidden ones dropped."""
    raw = payload.get("canales") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    channels: list[dict[str, Any]] = []
    used: set[str] = set()
    streamtypes = payload.get("streamstype") if isinstance(payload, dict) else None
    streamtypes = streamtypes if isinstance(streamtypes, dict) else {}
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("ocultar"):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        cid = _unique(slugify(str(entry.get("id") or name)), used)
        group, extra = _groups(entry.get("group"))
        streamtype = str(entry.get("streamtype") or "")
        url = str(entry.get("url") or "").strip()
        logo = str(entry.get("logo") or entry.get("logocolor") or "")
        source, web, recipe = _source(entry, streamtypes, streamtype, url)
        channels.append(
            {
                "id": cid,
                "name": name,
                "kind": "tv",
                "category": CATEGORY,
                "group": group,
                "sources": [source] if source else [],
                "fmts": ["m3u8" if source.lower().split("?")[0].endswith(".m3u8") else ""] if source else [],
                "web": web,
                "logo": logo,
                # RTVE's and 3Cat's logos are white on transparent; the raster ones are dark on white.
                # The page dresses them differently on the dark theme (see style.css).
                "logo_svg": logo.lower().split("?")[0].endswith(".svg"),
                "epg": "" if entry.get("epgid") in (None, "-") else str(entry.get("epgid")),
                "tags": extra,
                **({"tdtspain": recipe} if recipe else {}),
            }
        )
    return channels


def _unique(base: str, used: set[str]) -> str:
    candidate, n = base, 2
    while candidate in used:
        candidate = f"{base}-{n}"
        n += 1
    used.add(candidate)
    return candidate


def _groups(value: Any) -> tuple[str, list[str]]:
    parts = [p.strip() for p in str(value or "").split(";") if p.strip()]
    return (parts[0] if parts else "Otros"), parts[1:]


def _source(
    entry: dict[str, Any], streamtypes: dict[str, Any], streamtype: str, url: str
) -> tuple[str, str, dict[str, Any] | None]:
    """``(source, web, recipe)`` for one catalogue entry: a direct URL, ``tdtspain://`` or nothing."""
    ident = str(entry.get("id") or "")
    if streamtype == "stream1":
        return RTVE_HLS.get(ident, ""), "", None
    if streamtype == "stream11":
        match = EMBED_ATRES.match(url)
        slug = match.group(1).lower() if match else ""
        if slug in ATRES_IDS:
            # its own HLS, resolved at play time: the SCTE-35 comes inside and the server mutes it
            return SCHEME + slugify(ident), url, _recipe(entry, streamtypes)
        # no id: the embedded player, whose sound and ads are its own
        return (f"https://www.atresplayer.com/embed/live/{slug}/" if slug else ""), url, None
    if streamtype == "stream10":
        return SCHEME + slugify(ident), url, _recipe(entry, streamtypes)
    if streamtype in ("stream12", "geturl", "stream14"):
        return SCHEME + slugify(ident), "", _recipe(entry, streamtypes)
    if streamtype == "stream2":
        return "", "", None  # DMAX: the catalogue itself says it no longer broadcasts online
    if entry.get("msgerror"):
        return "", "", None
    if not url:
        return "", "", None
    if url.lower().split("?")[0].endswith(".mpd"):
        return "", "", None  # hls.js cannot play DASH: the channel stays as "web only"
    return url, "", None


def _recipe(entry: dict[str, Any], streamtypes: dict[str, Any]) -> dict[str, Any]:
    """The catalogue entry plus its stream type's defaults, keeping only what the resolver reads."""
    base = streamtypes.get(str(entry.get("streamtype") or ""))
    base = base if isinstance(base, dict) else {}
    data = {key: base[key] for key in ("baseurl", "patron", "postdata") if key in base}
    data.update({key: entry[key] for key in RECIPE if key in entry})
    for key in ("atributtes", "headers"):
        merged = dict(base.get(key) or {}) if isinstance(base.get(key), dict) else {}
        merged.update(entry.get(key) or {} if isinstance(entry.get(key), dict) else {})
        data[key] = merged
    data.setdefault("baseurl", "")
    data.setdefault("patron", "")
    return data


# ---- resolution at play time


def is_tdtspain(url: str | None) -> bool:
    return bool(url) and str(url).startswith(SCHEME)


def channel_id(source: str) -> str:
    """The id inside ``tdtspain://<id>``."""
    return str(source)[len(SCHEME) :]


def resolve(channel: dict[str, Any], net: Net) -> dict[str, Any]:
    """Follow the channel's recipe and return ``{url, headers, fmt}``. Raises :class:`ValueError`."""
    recipe = channel.get("tdtspain") or {}
    streamtype = str(recipe.get("streamtype") or "")
    if streamtype == "stream10":
        url, headers = _mediaset(recipe, net)
        return {"url": url, "headers": headers, "fmt": "m3u8"}
    if streamtype == "stream11":
        return {"url": _atres(recipe, net), "headers": dict(ATRES_HEADERS), "fmt": "m3u8"}
    if streamtype == "stream12":
        url = _dailymotion(recipe, net)
        return {"url": url, "headers": _headers(recipe), "fmt": "m3u8"}
    if streamtype == "geturl":
        url = _geturl(recipe, net)
        return {"url": url, "headers": _headers(recipe), "fmt": "m3u8"}
    if streamtype == "stream14":
        url, headers = _aragon(recipe, net)
        return {"url": url, "headers": headers, "fmt": "m3u8"}
    raise ValueError("este canal no tiene un directo que resolver")


def _headers(recipe: dict[str, Any]) -> dict[str, str]:
    headers = {str(k): str(v) for k, v in (recipe.get("headers") or {}).items() if v}
    if recipe.get("referer") and "Referer" not in headers:
        headers["Referer"] = str(recipe["referer"])
    return headers


def _fetch(
    net: Net,
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 25,
) -> bytes:
    """GET (or POST when ``data`` is given) after validating the URL; returns the body."""
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    request = urllib.request.Request(url, data=data, headers=merged)
    with net.open_request(request, require_allowed=False, timeout=timeout) as response:
        return response.read(4_000_000)


def _text(
    net: Net, url: str, headers: dict[str, str] | None = None, data: bytes | None = None, timeout: float = 25
) -> str:
    return _fetch(net, url, headers, data, timeout).decode("utf-8", "replace")


def _json(
    net: Net, url: str, data: bytes | None = None, headers: dict[str, str] | None = None, timeout: float = 25
) -> Any:
    return json.loads(_fetch(net, url, headers, data, timeout))


def _mediaset(recipe: dict[str, Any], net: Net) -> tuple[str, dict[str, str]]:
    ident = str(recipe.get("id") or "")
    code, page = MEDIASET.get(ident, (None, MEDIASET_PAGE))
    if code is None:
        raise ValueError("canal de Mediaset sin código de emisión")
    login = json.dumps({"appName": MEDIASET_APP, "client_id": str(uuid.uuid4())}).encode()
    data = _json(net, MEDIASET_LOGIN, login, {"Content-Type": "application/json", **MEDIASET_HEADERS})
    session = data.get("response") or {}
    sid, token = session.get("sid"), session.get("beToken")
    if not sid or not token:
        raise ValueError("Mediaset no devolvió sesión anónima")
    check = json.dumps(
        {
            "channelCode": code,
            "streamType": "LIVE",
            "delivery": "Streaming",
            "createDevice": True,
            "overrideAppName": MEDIASET_APP,
        }
    ).encode()
    data = _json(
        net,
        MEDIASET_CHECK + "?sid=" + urllib.parse.quote(sid),
        check,
        {"Content-Type": "application/json", "Authorization": "Bearer " + token, **MEDIASET_HEADERS},
    )
    asset = ((data.get("response") or {}).get("dai") or {}).get("assetKey")
    if not asset:
        raise ValueError("Mediaset no devolvió la clave DAI")
    form = urllib.parse.urlencode(
        {
            "ppid": sid,
            "vpa": "auto",
            "wta": "1",
            "vpmute": "0",
            "cust_params": f"device=desktop&os=desktop&userType=infinity&raw_ppid={sid}&puid={sid}&tad=0",
            "url": page,
        }
    ).encode()
    data = _json(
        net,
        MEDIASET_DAI.format(asset),
        form,
        {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://www.mediasetinfinity.es",
            "Referer": page,
        },
    )
    manifest = data.get("stream_manifest")
    if not manifest:
        raise ValueError("el DAI de Mediaset no devolvió el manifiesto")
    return manifest, {"Origin": "https://www.mediasetinfinity.es", "Referer": page}


def _atres(recipe: dict[str, Any], net: Net) -> str:
    """Atresplayer's own HLS for a live channel, from the player API (it is in the clear, with SCTE-35 inside)."""
    url = str(recipe.get("url") or "")
    match = EMBED_ATRES.match(url)
    ident = ATRES_IDS.get(match.group(1).lower()) if match else None
    if not ident:
        raise ValueError("canal de Atresplayer sin identificador")
    data = _json(net, ATRES_PLAYER.format(ident), headers={**ATRES_HEADERS, "Accept": "application/json"})
    for source in data.get("sourcesLive") or []:
        if "mpegurl" in str(source.get("type") or "") and source.get("src"):
            return str(source["src"])
    raise ValueError("Atresplayer no devolvió el HLS")


def _dailymotion(recipe: dict[str, Any], net: Net) -> str:
    url = str(recipe.get("url") or "")
    match = DMPLAYER.search(url)
    if not match:
        raise ValueError("enlace de Dailymotion sin identificador")
    data = json.loads(_text(net, DAILYMOTION.format(match.group(1)), _headers(recipe)))
    qualities = data.get("qualities") or {}
    for name in ("auto", "720", "480", "360"):
        items = qualities.get(name) or []
        if items and items[0].get("url"):
            return str(items[0]["url"])
    raise ValueError("Dailymotion no devolvió el directo")


def _geturl(recipe: dict[str, Any], net: Net) -> str:
    url = str(recipe.get("url") or "")
    if not url:
        raise ValueError("canal sin página de origen")
    headers = _headers(recipe)
    postdata = recipe.get("postdata")
    body = urllib.parse.unquote(str(postdata)).encode() if postdata else None
    text = _text(net, url, {**headers, "Content-Type": "application/json"}, body, 25)
    pattern = urllib.parse.unquote(str(recipe.get("patron") or ""))
    match = re.search(pattern, text) if pattern else None
    if not match:
        raise ValueError("la página no contiene el directo")
    value = (match.group(1) if match.groups() else match.group(0)).replace("\\/", "/")
    base = str(recipe.get("baseurl") or "")
    if base == "https:":
        return "https:" + value if value.startswith("//") else value
    return base + value if base else value


def _aragon(recipe: dict[str, Any], net: Net) -> tuple[str, dict[str, str]]:
    attrs = recipe.get("atributtes") or {}
    graphql = str(attrs.get("urltoken") or "")
    body = urllib.parse.unquote(str(attrs.get("datosurltoken") or ""))
    ident = str(recipe.get("url") or "")
    body = body.replace("$idcanal", ident).encode()
    headers = {"Content-Type": "application/json", **_headers(recipe)}
    text = _text(net, graphql, headers, body, 25)
    token = re.search(r'"accessToken"\s*:\s*"([^"]+)"', text)
    if not token:
        raise ValueError("Aragón TV no devolvió el token del directo")
    url = urllib.parse.unquote(str(attrs.get("baseurl") or ""))
    url = url.replace("$idcanal", ident).replace("$idtoken", token.group(1))
    return url, _headers(recipe)
