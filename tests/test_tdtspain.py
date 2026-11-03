"""The TDT Spain catalogue (`canalesv4.json`) and the resolution of its recipes."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tdtlocal import tdtspain

OUT = "FC302500000000000000FFF014056AE836907FEFFE856F8090FE007B98A0000000000000E44A55DC"


def payload() -> dict:
    return {
        "streamstype": {
            "stream14": {
                "atributtes": {
                    "urltoken": "https://eu.next.mediastream.co/graphql",
                    "datosurltoken": "%5B%7B%22query%22%3A%22getLive%22%7D%5D",
                    "baseurl": "https://eu.mdstrm.com/live-stream-playlist/$idcanal.m3u8?access_token=$idtoken",
                },
                "headers": {"x-client-id": "abc"},
            }
        },
        "canales": [
            {
                "id": "La1.TV",
                "name": "La 1",
                "epgid": "La1.TV",
                "group": "Nacionales",
                "logo": "https://x/la1.svg",
                "streamtype": "stream1",
                "url": "1688877",
            },
            {
                "id": "Antena3.TV",
                "name": "Antena 3",
                "epgid": "Antena3.TV",
                "group": "Nacionales",
                "streamtype": "stream11",
                "url": "https://www.atresplayer.com/directos/antena3/",
            },
            {
                "id": "Multicine.TV",
                "name": "Atresplayer Multicine",
                "group": "Temáticos A3media",
                "streamtype": "stream11",
                "url": "https://www.atresplayer.com/directos/multicine/",
            },
            {
                "id": "Cuatro.TV",
                "name": "Cuatro",
                "epgid": "Cuatro.TV",
                "group": "Nacionales",
                "streamtype": "stream10",
                "url": "https://www.mediasetinfinity.es/directo/cuatro/",
            },
            {
                "id": "Raro.TV",
                "name": "Canal Raro",
                "group": "Otros",
                "streamtype": "stream11",
                "url": "https://www.atresplayer.com/directos/canal-raro/",
            },
            {
                "id": "ETB.TV",
                "name": "ETB 1",
                "group": "País Vasco",
                "url": "https://x/etb1/index.mpd",
            },
            {
                "id": "Kairos.TV",
                "name": "Kairos TV",
                "group": "Religiosos;Internacional",
                "ocultar": "1",
                "url": "https://x/kairos.m3u8",
            },
            {
                "id": "Peques.TV",
                "name": "Peques",
                "group": "Infantiles;Galicia",
                "url": "https://x/peques.m3u8",
            },
            {
                "id": "Euronews.TV",
                "name": "Euronews",
                "group": "Informativos",
                "streamtype": "youtube",
                "url": "https://www.youtube.com/@euronews/live",
            },
            {
                "id": "La7.TV",
                "name": "La 7",
                "group": "Castilla y León",
                "streamtype": "geturl",
                "url": "https://www.cyltv.es/live/La7",
                "patron": "%28https%3F%3A%5C%2F%5C%2F.%2A%3F%5C.m3u8%29",
            },
            {
                "id": "Melilla.TV",
                "name": "Melilla TV",
                "group": "Melilla",
                "streamtype": "stream12",
                "url": "https://www.dailymotion.com/player/metadata/video/x9jhjqk",
            },
            {
                "id": "Aragon.TV",
                "name": "Aragón El Tiempo",
                "group": "Aragón",
                "streamtype": "stream14",
                "url": "699341cfc03fcc0e33b898c3",
            },
            {
                "id": "DMax.TV",
                "name": "DMAX",
                "group": "Nacionales",
                "streamtype": "stream2",
                "url": "https://public.aurora.enhanced.live/playback/v3/",
                "msgerror": "Este canal ha dejado de emitir directos por internet.",
            },
        ],
    }


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.channels = {c["name"]: c for c in tdtspain.parse_catalog(payload())}

    def test_hidden_channels_are_dropped(self):
        self.assertNotIn("Kairos TV", self.channels)
        self.assertEqual(len(self.channels), 12)

    def test_rtve_gets_its_public_hls(self):
        self.assertEqual(self.channels["La 1"]["sources"], [tdtspain.RTVE_HLS["La1.TV"]])
        self.assertEqual(self.channels["La 1"]["epg"], "La1.TV")
        self.assertTrue(self.channels["La 1"]["logo_svg"])

    def test_atresmedia_with_an_id_keeps_its_hls_recipe(self):
        channel = self.channels["Antena 3"]
        self.assertEqual(channel["sources"], ["tdtspain://antena3-tv"])
        self.assertEqual(channel["tdtspain"]["streamtype"], "stream11")
        self.assertEqual(channel["web"], "https://www.atresplayer.com/directos/antena3/")

    def test_atresmedia_without_an_id_uses_the_embedded_player(self):
        self.assertEqual(
            self.channels["Canal Raro"]["sources"],
            ["https://www.atresplayer.com/embed/live/canal-raro/"],
        )

    def test_atresmedia_thematic_channels_also_use_their_hls(self):
        self.assertEqual(self.channels["Atresplayer Multicine"]["sources"], ["tdtspain://multicine-tv"])

    def test_mediaset_keeps_a_recipe_for_play_time(self):
        channel = self.channels["Cuatro"]
        self.assertEqual(channel["sources"], ["tdtspain://cuatro-tv"])
        self.assertEqual(channel["tdtspain"]["streamtype"], "stream10")
        self.assertEqual(channel["web"], "https://www.mediasetinfinity.es/directo/cuatro/")

    def test_dash_only_channels_have_no_source(self):
        self.assertEqual(self.channels["ETB 1"]["sources"], [])

    def test_multi_group_is_split(self):
        self.assertEqual(self.channels["Peques"]["group"], "Infantiles")
        self.assertEqual(self.channels["Peques"]["tags"], ["Galicia"])

    def test_youtube_is_kept(self):
        self.assertTrue(self.channels["Euronews"]["sources"][0].startswith("https://www.youtube.com/"))

    def test_dmax_with_a_message_has_no_source(self):
        self.assertEqual(self.channels["DMAX"]["sources"], [])

    def test_looks_like(self):
        self.assertTrue(tdtspain.looks_like(b'  {"canales": []}'))
        self.assertFalse(tdtspain.looks_like(b"# TELEVISION\n| Canal |"))


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.channels = {c["name"]: c for c in tdtspain.parse_catalog(payload())}

    def test_geturl_reads_the_page(self):
        html = b'<script>var p = {"url":"https://la7live.example/playlist.m3u8"};</script>'
        with patch.object(tdtspain, "_fetch", return_value=html) as fetch:
            out = tdtspain.resolve(self.channels["La 7"], object())
        self.assertEqual(out["url"], "https://la7live.example/playlist.m3u8")
        self.assertEqual(fetch.call_args[0][1], "https://www.cyltv.es/live/La7")

    def test_dailymotion_reads_the_qualities(self):
        data = json.dumps({"qualities": {"auto": [{"url": "https://cdn/x.m3u8"}]}}).encode()
        with patch.object(tdtspain, "_fetch", return_value=data):
            out = tdtspain.resolve(self.channels["Melilla TV"], object())
        self.assertEqual(out["url"], "https://cdn/x.m3u8")

    def test_mediaset_logs_in_and_opens_the_dai_session(self):
        login = json.dumps({"response": {"sid": "s1", "beToken": "t1"}}).encode()
        check = json.dumps({"response": {"dai": {"assetKey": "KEY"}}}).encode()
        dai = json.dumps({"stream_manifest": "https://directos/x/dai.m3u8"}).encode()
        with patch.object(tdtspain, "_fetch", side_effect=[login, check, dai]) as fetch:
            out = tdtspain.resolve(self.channels["Cuatro"], object())
        self.assertEqual(out["url"], "https://directos/x/dai.m3u8")
        self.assertEqual(out["headers"]["Referer"], "https://www.mediasetinfinity.es/directo/cuatro/")
        self.assertIn("/ssai/event/KEY/streams", fetch.call_args_list[2][0][1])
        self.assertEqual(fetch.call_args_list[2][0][1], "https://pubads.g.doubleclick.net/ssai/event/KEY/streams")

    def test_atresplayer_hls_is_read_from_the_player_api(self):
        data = json.dumps(
            {
                "sourcesLive": [
                    {"type": "application/dash+xml", "src": "https://atres-live/x.mpd"},
                    {"type": "application/vnd.apple.mpegurl", "src": "https://atres-live/x.m3u8"},
                ]
            }
        ).encode()
        with patch.object(tdtspain, "_fetch", return_value=data) as fetch:
            out = tdtspain.resolve(self.channels["Antena 3"], object())
        self.assertEqual(out["url"], "https://atres-live/x.m3u8")
        self.assertEqual(fetch.call_args[0][1], tdtspain.ATRES_PLAYER.format("5a6a165a7ed1a834493ebf6a"))

    def test_aragon_uses_the_graphql_token(self):
        graphql = b'[{"data":{"getLive":{"accessToken":"tok"}}}]'
        with patch.object(tdtspain, "_fetch", return_value=graphql):
            out = tdtspain.resolve(self.channels["Aragón El Tiempo"], object())
        self.assertIn("699341cfc03fcc0e33b898c3", out["url"])
        self.assertIn("access_token=tok", out["url"])

    def test_a_channel_without_a_recipe_is_refused(self):
        with self.assertRaises(ValueError):
            tdtspain.resolve(self.channels["La 1"], object())


if __name__ == "__main__":
    unittest.main()
