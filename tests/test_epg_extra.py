"""The extra guide: the programme of own channels (Atresmedia's…) that the lists' guides do not have."""

from __future__ import annotations

import gzip
import unittest
import unittest.mock

from tdtlocal.custom import build_custom_entry
from tdtlocal.epg import extra_epg_id, parse_xmltv

from .helpers import AppCase, ServerCase

XML = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="La Sexta HD"><display-name>La Sexta</display-name></channel>
  <channel id="Otro"><display-name>Otro</display-name></channel>
  <programme start="20990101100000 +0000" stop="20990101110000 +0000" channel="La Sexta HD">
    <title>laSexta Noticias</title><desc>El informativo.</desc><category>Noticias</category>
  </programme>
  <programme start="20990101110000 +0000" stop="20990101120000 +0000" channel="La Sexta HD">
    <title>Más Vale Tarde</title>
  </programme>
  <programme start="20990101100000 +0000" stop="20990101110000 +0000" channel="Otro">
    <title>No me interesa</title>
  </programme>
  <programme start="20000101100000 +0000" stop="20000101110000 +0000" channel="La Sexta HD">
    <title>Ya pasó</title>
  </programme>
</tv>
"""
SEXTA = {
    "name": "La Sexta",
    "sources": ["https://www.atresplayer.com/embed/live/lasexta/"],
    "web": "https://www.atresplayer.com/directos/lasexta/",
    "epg": "La Sexta HD",
}


class ParseTests(unittest.TestCase):
    def test_only_keeps_the_channels_asked_for(self):
        guide = parse_xmltv(XML.encode(), only={"La Sexta HD"})
        self.assertEqual(list(guide), ["La Sexta HD"])
        self.assertEqual(
            [p[2] for p in guide["La Sexta HD"]], ["laSexta Noticias", "Más Vale Tarde"]
        )  # the past one is gone
        self.assertEqual(guide["La Sexta HD"][0][3:], ["El informativo.", "Noticias"])

    def test_without_only_it_keeps_all_of_them(self):
        self.assertEqual(set(parse_xmltv(XML.encode())), {"La Sexta HD", "Otro"})

    def test_entities_are_refused_also_when_reading_a_few_channels(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE tv [<!ENTITY a "aaaa">]><tv><programme channel="x"/></tv>'
        with self.assertRaises(ValueError):
            parse_xmltv(evil, only={"x"})

    def test_a_broken_document_is_an_error(self):
        with self.assertRaises(Exception):  # noqa: B017 - ElementTree.ParseError
            parse_xmltv(b"<tv><programme channel='x'>", only={"x"})


class ExtraIdTests(unittest.TestCase):
    def test_atresmedia_ids_are_translated_to_the_extra_guide(self):
        self.assertEqual(extra_epg_id("Antena3.TV"), "Antena 3 HD")
        self.assertEqual(extra_epg_id("LaSexta.TV"), "La Sexta HD")
        self.assertEqual(extra_epg_id("A3P_Multicine.TV"), "Multicine atresplayer")

    def test_other_ids_are_left_alone(self):
        self.assertEqual(extra_epg_id("La1.TV"), "La1.TV")
        self.assertEqual(extra_epg_id("La Sexta HD"), "La Sexta HD")


class CustomEntryTests(unittest.TestCase):
    def test_a_guide_id_is_kept_if_it_is_reasonable(self):
        self.assertEqual(build_custom_entry(SEXTA)["epg"], "La Sexta HD")
        self.assertEqual(build_custom_entry({**SEXTA, "epg": " Neox "})["epg"], "Neox")

    def test_a_guide_id_that_is_not_one_is_dropped(self):
        self.assertEqual(build_custom_entry({**SEXTA, "epg": "x" * 200})["epg"], "")
        self.assertEqual(build_custom_entry({**SEXTA, "epg": "a\nb"})["epg"], "")
        self.assertEqual(build_custom_entry({**SEXTA, "epg": 5})["epg"], "5")
        self.assertEqual(build_custom_entry({k: v for k, v in SEXTA.items() if k != "epg"})["epg"], "")


class ExtraGuideTests(AppCase):
    def setUp(self):
        super().setUp()
        self.app.config.epg_extra_enabled = True
        self.app.config.epg_extra_source = self.origin.url("/extra.xml")
        self.hits = 0

        def route(handler):
            self.hits += 1
            if handler.headers.get("If-None-Match") == '"v1"':
                return 304, {}, b""
            return 200, {"ETag": '"v1"', "Content-Type": "application/xml"}, XML.encode()

        self.origin.routes["/extra.xml"] = route

    def test_it_reads_only_the_channels_that_need_it(self):
        self.app.store.add_custom(SEXTA)
        self.app.store.add_custom({"name": "Sin guía", "sources": ["https://x.example/a.m3u8"]})
        self.assertEqual(self.app.epg.extra_ids(), {"La Sexta HD"})
        result = self.app.epg.sync_extra()
        self.assertEqual((result["changed"], result["error"]), (True, None))
        self.assertEqual(list(self.app.store.epg["extra"]), ["La Sexta HD"])

    def test_a_channel_that_a_list_guide_covers_does_not_need_it(self):
        self.app.store.epg["tv"]["La Sexta HD"] = [[1, 2, "x", "", ""]]
        self.app.store.add_custom(SEXTA)
        self.assertEqual(self.app.epg.extra_ids(), set())

    def test_the_guide_reaches_the_page(self):
        self.app.store.add_custom(SEXTA)
        self.app.epg.sync_extra()
        now = self.app.epg.payload()["now"]
        self.assertEqual([p[2] for p in now["La Sexta HD"]], ["laSexta Noticias", "Más Vale Tarde"])
        self.assertEqual(len(self.app.epg.payload("La Sexta HD")["programmes"]), 2)

    def test_a_catalogue_channel_uses_the_extra_guide_under_its_own_id(self):
        self.app.store.channels = [{"id": "lasexta-tv", "name": "laSexta", "epg": "LaSexta.TV"}]
        self.assertEqual(self.app.epg.extra_ids(), {"La Sexta HD"})
        self.assertTrue(self.app.epg.sync_extra()["changed"])
        self.assertEqual(list(self.app.store.epg["extra"]), ["La Sexta HD"])
        now = self.app.epg.payload()["now"]
        self.assertEqual([p[2] for p in now["LaSexta.TV"]], ["laSexta Noticias", "Más Vale Tarde"])
        detail = self.app.epg.payload("LaSexta.TV")
        self.assertEqual([p[2] for p in detail["programmes"]], ["laSexta Noticias", "Más Vale Tarde"])

    def test_a_catalogue_channel_a_list_guide_covers_does_not_need_it(self):
        self.app.store.epg["tv"]["LaSexta.TV"] = [[1, 2, "x", "", ""]]
        self.app.store.channels = [{"id": "lasexta-tv", "name": "laSexta", "epg": "LaSexta.TV"}]
        self.assertEqual(self.app.epg.extra_ids(), set())

    def test_the_guide_is_cached_and_reused_at_startup(self):
        self.app.store.add_custom(SEXTA)
        self.assertTrue(self.app.epg.sync_extra()["changed"])
        self.assertTrue((self.app.config.data_dir / "epg_extra.json").exists())
        restarted = self.make_app(epg_extra_source=self.origin.url("/extra.xml"))
        restarted.store.load()
        self.assertEqual(list(restarted.store.epg["extra"]), ["La Sexta HD"])
        self.assertEqual(restarted.store.epg_extra_meta["wanted"], ["La Sexta HD"])
        self.assertEqual(restarted.epg.extra_ids(), {"La Sexta HD"})
        again = restarted.epg.sync_extra()
        self.assertEqual((again["changed"], again["error"], self.hits), (False, None, 2))  # one download, one 304

    def test_a_new_source_is_read_whole(self):
        self.app.store.add_custom(SEXTA)
        self.app.epg.sync_extra()
        self.origin.routes["/extra2.xml"] = (200, {"ETag": '"v2"'}, XML.encode())
        self.app.config.epg_extra_source = self.origin.url("/extra2.xml")
        self.assertTrue(self.app.epg.sync_extra()["changed"])
        self.assertNotIn("if-none-match", self.origin.requests[-1][1])

    def test_a_corrupt_cache_is_ignored(self):
        path = self.app.config.data_dir / "epg_extra.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{no json", encoding="utf-8")
        self.app.store.load()
        self.assertEqual(self.app.store.epg["extra"], {})

    def test_nothing_is_downloaded_when_no_channel_needs_it(self):
        result = self.app.epg.sync_extra()
        self.assertEqual((result["changed"], self.hits), (False, 0))

    def test_it_asks_again_only_with_its_etag_and_when_nothing_changed(self):
        self.app.store.add_custom(SEXTA)
        self.app.epg.sync_extra()
        again = self.app.epg.sync_extra()
        self.assertEqual((again["changed"], again["error"], self.hits), (False, None, 2))  # a 304: the data is kept
        self.assertEqual(list(self.app.store.epg["extra"]), ["La Sexta HD"])
        self.app.store.add_custom({**SEXTA, "name": "Otro", "epg": "Otro"})  # a new channel needs reading it again
        self.assertTrue(self.app.epg.sync_extra()["changed"])
        self.assertEqual(set(self.app.store.epg["extra"]), {"La Sexta HD", "Otro"})

    def test_a_gzipped_guide_is_understood(self):
        self.origin.routes["/extra.xml"] = (200, {}, gzip.compress(XML.encode()))
        self.app.store.add_custom(SEXTA)
        self.assertTrue(self.app.epg.sync_extra()["changed"])

    def test_failures_are_reported_and_what_was_read_is_kept(self):
        self.app.store.add_custom(SEXTA)
        self.app.epg.sync_extra()
        self.origin.routes["/extra.xml"] = (500, {}, b"no")
        failed = self.app.epg.sync_extra(force=True)
        self.assertEqual((failed["changed"], failed["error"]), (False, ["extra: HTTP 500"]))
        self.assertEqual(list(self.app.store.epg["extra"]), ["La Sexta HD"])
        self.origin.routes["/extra.xml"] = (200, {}, b"<tv><programme")
        self.assertIn("extra:", self.app.epg.sync_extra(force=True)["error"][0])

    def test_a_guide_that_is_too_big_is_refused(self):
        self.app.store.add_custom(SEXTA)
        with unittest.mock.patch("tdtlocal.epg.MAX_EXTRA_XML", 100):
            result = self.app.epg.sync_extra(force=True)
        self.assertIn("demasiado grande", result["error"][0])


class ExtraGuideSettingsTests(ServerCase):
    def test_it_is_on_by_default_and_can_be_turned_off(self):
        self.assertTrue(self.app.config.epg_extra_enabled)
        self.assertTrue(self.app.config.epg_extra_source.startswith("https://"))
        status, data = self.post_json("/api/settings", {"epg_extra_enabled": False})
        self.assertEqual((status, data["ok"]), (200, True))
        self.assertFalse(self.app.config.epg_extra_enabled)
        self.assertEqual(self.post_json("/api/settings", {"epg_extra_source": "ftp://x"})[0], 400)

    def test_adding_a_channel_with_a_guide_id_keeps_it_and_merging_fills_it_in(self):
        stream = {"name": "La Sexta", "sources": ["https://cdn.example/a.m3u8"], "merge": True}
        self.post_json("/api/custom", stream)
        merged = self.post_json("/api/custom", {**SEXTA, "merge": True})[1]
        self.assertTrue(merged["merged"])
        channel = next(c for c in self.get_json("/api/channels")[1]["channels"] if c["custom"])
        self.assertEqual(channel["epg"], "La Sexta HD")


if __name__ == "__main__":
    unittest.main()
