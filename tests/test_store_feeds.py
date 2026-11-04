import json
import urllib.error

from tdtlocal.store import channel_ok, read_json, source_ok, write_json

from .helpers import AppCase, fixture, make_tv_markdown


class StoreTests(AppCase):
    def test_write_json_is_atomic_and_read_json_tolerates_garbage(self):
        path = self.tmp / "sub" / "x.json"
        write_json(path, {"a": "ñ"})
        self.assertEqual(read_json(path), {"a": "ñ"})
        self.assertFalse(path.with_suffix(".json.tmp").exists())
        path.write_text("{no json", encoding="utf-8")
        self.assertIsNone(read_json(path))
        path.write_text("[1, 2]", encoding="utf-8")
        self.assertIsNone(read_json(path))
        self.assertIsNone(read_json(self.tmp / "missing.json"))

    def test_load_with_empty_data_dir(self):
        self.app.store.load()
        self.assertEqual(self.app.store.all_channels(), [])

    def test_feed_round_trip_and_allow_lists(self):
        store = self.app.store
        store.channels = [
            {
                "id": "a",
                "name": "A",
                "sources": ["https://s.example/a.m3u8"],
                "logo": "https://l.example/a.png",
                "kind": "tv",
                "category": "N",
                "group": "",
            }
        ]
        store.save_feed("tv")
        again = self.make_app().store
        again.load()
        self.assertEqual(again.channels, store.channels)
        self.assertTrue(again.net.is_allowed("s.example"))
        self.assertIn("https://l.example/a.png", again.logo_urls)

    def test_custom_channels_lifecycle(self):
        store = self.app.store
        cid = store.add_custom(
            {"name": "La Sexta", "sources": ["https://x.example/a.m3u8"], "headers": {"Origin": "https://o.example"}}
        )
        self.assertEqual(cid, "custom-la-sexta")
        self.assertEqual(store.custom_headers[cid], {"Origin": "https://o.example"})
        self.assertTrue(store.net.is_allowed("x.example"))
        self.assertEqual(
            store.add_custom({"name": "La Sexta", "sources": ["https://y.example/a.m3u8"]}), "custom-la-sexta-2"
        )
        reloaded = self.make_app().store
        reloaded.load()
        self.assertEqual([c["id"] for c in reloaded.custom], ["custom-la-sexta", "custom-la-sexta-2"])
        self.assertTrue(store.delete_custom(cid))
        self.assertFalse(store.delete_custom(cid))
        self.assertNotIn(cid, store.custom_headers)

    def test_custom_channel_cannot_claim_an_existing_id(self):
        store = self.app.store
        store.channels = [{"id": "custom-x", "name": "X", "sources": [], "logo": "", "kind": "tv"}]
        cid = store.add_custom({"id": "la-1", "name": "X", "sources": ["https://x.example/a.m3u8"]})
        self.assertEqual(cid, "custom-x-2")

    def test_invalid_custom_entry_is_rejected(self):
        with self.assertRaises(ValueError):
            self.app.store.add_custom({"name": "", "sources": []})

    def test_corrupt_custom_entries_are_dropped_on_load(self):
        write_json(
            self.app.config.data_dir / "custom.json",
            {"channels": [{"name": "ok", "sources": ["https://x.example/a"]}, {"name": ""}, "basura"]},
        )
        store = self.make_app().store
        store.load()
        self.assertEqual([c["name"] for c in store.custom], ["ok"])

    def test_sources_by_kind_skip_youtube(self):
        store = self.app.store
        store.channels = [
            {
                "id": "a",
                "sources": ["https://s/a.m3u8", "https://www.youtube.com/watch?v=abcdefghijk"],
                "fmts": ["", ""],
                "logo": "",
                "kind": "tv",
            }
        ]
        store.radios = [{"id": "r", "sources": ["https://s/r.mp3"], "fmts": ["mp3"], "logo": "", "kind": "radio"}]
        self.assertEqual(store.sources("tv"), {"https://s/a.m3u8": ""})
        store.rebuild_allowlists()
        self.assertTrue(store.net.is_allowed("www.youtube.com"))
        self.assertEqual(store.sources("radio"), {"https://s/r.mp3": "mp3"})
        self.assertEqual(len(store.sources("all")), 2)

    def test_health_helpers(self):
        ch = {"sources": ["https://a", "https://b"]}
        self.assertIsNone(channel_ok(ch, {}))
        self.assertTrue(channel_ok(ch, {"https://a": {"ok": False}, "https://b": {"ok": True}}))
        self.assertFalse(channel_ok(ch, {"https://a": {"ok": False}, "https://b": {"ok": False}}))
        self.assertIsNone(channel_ok(ch, {"https://a": {"ok": False}}))  # one unknown -> unknown
        self.assertIsNone(source_ok({"custom": True}, "https://a", {"https://a": {"ok": True}}))
        self.assertIsNone(source_ok({}, "https://www.youtube.com/x", {"https://www.youtube.com/x": {"ok": True}}))


class FeedSyncTests(AppCase):
    def setUp(self):
        super().setUp()
        self.tv_md = make_tv_markdown(60)
        self.radio_md = (
            fixture("RADIO.md")
            + "\n".join(f"| R{i} | [mp3](https://r.example/{i}.mp3) | - | - | - | - |" for i in range(40))
            + "\n"
        )
        self.hits = {"tv": 0, "radio": 0}

        def serve(kind, text):
            def route(handler):
                self.hits[kind] += 1
                if handler.headers.get("If-None-Match") == '"v1"':
                    return 304, {}, b""
                return 200, {"ETag": '"v1"'}, text.encode()

            return route

        self.origin.routes["/TELEVISION.md"] = serve("tv", self.tv_md)
        self.origin.routes["/RADIO.md"] = serve("radio", self.radio_md)
        self.app = self.make_app(tv_source=self.origin.url("/TELEVISION.md"), radio_source=self.origin.url("/RADIO.md"))

    def test_first_sync_downloads_parses_and_persists(self):
        result = self.app.feeds.sync()
        self.assertTrue(result["changed"])
        self.assertEqual(result["count"], 60)
        self.assertEqual(result["radio"], 42)
        store = self.app.store
        self.assertEqual(len(store.channels), 60)
        self.assertEqual(store.meta["etag"], '"v1"')
        self.assertIsNotNone(store.meta["updated"])
        saved = json.loads((self.app.config.data_dir / "tv.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved["channels"]), 60)
        self.assertTrue(store.net.is_allowed("tv.example"))

    def test_second_sync_uses_etag_and_reports_no_change(self):
        self.app.feeds.sync()
        result = self.app.feeds.sync()
        self.assertFalse(result["changed"])
        self.assertEqual(result["count"], 60)

    def test_force_ignores_the_etag(self):
        self.app.feeds.sync()
        paths = [p for p, h in self.origin.requests]
        self.assertEqual(paths.count("/TELEVISION.md"), 1)
        self.app.feeds.sync(force=True)
        sent = [h for p, h in self.origin.requests if p == "/TELEVISION.md"]
        self.assertNotIn("if-none-match", sent[-1])

    def test_a_tiny_list_is_rejected_and_the_previous_one_kept(self):
        self.app.feeds.sync()
        self.origin.routes["/TELEVISION.md"] = (200, {"ETag": '"v2"'}, make_tv_markdown(3).encode())
        with self.assertRaises(ValueError):
            self.app.feeds.sync(force=True)
        self.assertEqual(len(self.app.store.channels), 60)
        self.assertIn("formato inesperado", self.app.store.meta["error"])

    def test_http_error_is_recorded(self):
        self.origin.routes["/TELEVISION.md"] = (500, {}, b"boom")
        with self.assertRaises(urllib.error.HTTPError):
            self.app.feeds.sync()
        self.assertEqual(self.app.store.meta["error"], "HTTP 500 al descargar la lista")

    def test_radio_failure_does_not_fail_the_sync(self):
        self.origin.routes["/RADIO.md"] = (500, {}, b"boom")
        result = self.app.feeds.sync()
        self.assertEqual(result["count"], 60)
        self.assertNotIn("radio", result)
        self.assertEqual(self.app.store.radios, [])

    def test_oversized_download_is_rejected(self):
        self.origin.routes["/TELEVISION.md"] = (200, {}, b"x" * 8_000_001)
        with self.assertRaises(ValueError):
            self.app.feeds.sync()
