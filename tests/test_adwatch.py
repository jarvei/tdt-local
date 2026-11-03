"""The ad-break engine: markers, the lag of the picture and Mediaset's DAI rules."""

from __future__ import annotations

import unittest

from tdtlocal.adwatch import engine, hls, id3, scte35

OUT = "FC302500000000000000FFF014056AE836907FEFFE856F8090FE007B98A0000000000000E44A55DC"
IN = "FC302000000000000000FFF00F056AE836EA7F4FFE85EB1930000000000000B25B3378"


def playlist(out: str = OUT, start: str = "2026-11-03T10:00:00Z", duration: int = 90) -> str:
    return f"""#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:10
#EXT-X-DATERANGE:ID="b1",START-DATE="{start}",PLANNED-DURATION={duration},SCTE35-OUT=0x{out}
#EXTINF:6.0,
seg1.ts
"""


def events(text: str, *, margin: float = 0, mediaset: bool = False):
    return engine.normalize(hls.summarize(text, "https://x/media.m3u8"), margin, mediaset)


def at(text: str) -> float:
    return engine._epoch(text)


class ScteTests(unittest.TestCase):
    def test_splice_insert_is_decoded(self):
        parsed = scte35.from_tag("0x" + OUT)
        self.assertEqual(parsed["command_name"], "splice_insert")
        self.assertTrue(parsed["out_of_network"])
        self.assertEqual(parsed["duration_s"], 90.0)

    def test_in_closes_the_break(self):
        parsed = scte35.from_tag("0x" + IN)
        self.assertFalse(parsed["out_of_network"])


class NormalizeTests(unittest.TestCase):
    def test_daterange_out_carries_start_and_duration(self):
        found = events(playlist())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["class"], "SCTE35-OUT")
        self.assertEqual(found[0]["duration"], 90.0)
        self.assertEqual(found[0]["start"], "2026-11-03T10:00:00Z")

    def test_margin_is_added_to_the_break(self):
        self.assertEqual(events(playlist(), margin=18)[0]["duration"], 108.0)

    def test_cue_in_closes(self):
        text = playlist() + "#EXT-X-CUE-IN\nseg2.ts\n"
        found = events(text)
        self.assertEqual([e["class"] for e in found], ["SCTE35-OUT", "CUE-IN"])

    def test_neutral_daterange_is_ignored(self):
        text = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-DATERANGE:ID="x",CLASS="https://aomedia.org/emsg/ID3",START-DATE="2026-11-03T10:00:00Z",X-MESSAGE-DATA="SUQzBAAAAAAAHFRYWFgAAAASAAADZXZlbnQAMjE0MzIxN19hbGw="
#EXTINF:6.0,
seg.ts
"""
        self.assertEqual(events(text), [])

    def test_id3_admode_starts_a_break_with_the_time_left(self):
        # ID3 TXXX "admode"="out_30000" in base64.
        frame = b"TXXX" + b"\x00\x00\x00" + bytes([0, 0, 0, 11]) + b"\x00admode" + b"\x00" + b"out_30000"
        payload = b"ID3\x03\x00\x00" + bytes([0, 0, 0, len(frame)]) + frame
        import base64

        text = f"""#EXTM3U
#EXT-X-DATERANGE:ID="m",START-DATE="2026-11-03T10:00:00Z",X-MESSAGE-DATA="{base64.b64encode(payload).decode()}"
#EXTINF:6.0,
seg.ts
"""
        found = events(text, mediaset=True)
        self.assertEqual(found[0]["class"], "ID3-AD")
        self.assertEqual(found[0]["duration"], 30.0)

    def test_the_admode_reader(self):
        import base64

        payload = b"ID3\x04\x00\x00\x00\x00\x00\x0dTXXX\x00\x00\x00\x05\x00\x00admode"
        self.assertEqual(id3.admode(base64.b64encode(payload).decode()), None)
        self.assertIsNone(id3.admode(None))

    def test_epoch_honours_the_zone(self):
        self.assertEqual(engine._epoch("2026-11-03T10:00:00+02:00"), engine._epoch("2026-11-03T08:00:00Z"))
        self.assertEqual(engine._epoch("2026-11-03T08:00:00Z"), 1793692800.0)
        self.assertEqual(engine._epoch("2026-11-03T08:00:00.400Z"), 1793692800.4)
        self.assertIsNone(engine._epoch("no es una fecha"))

    def test_seen_is_trimmed(self):
        import threading

        v = object.__new__(engine.Vigilance)
        v.seen, v.events, v._lock = {}, [], threading.Lock()
        v._remember([{"key": ("k", i)} for i in range(engine.MAX_SEEN + 1000)])
        self.assertLessEqual(len(v.seen), engine.MAX_SEEN)
        self.assertLessEqual(len(v.events), 500)

    def test_the_watcher_follows_the_first_variant_like_admute(self):
        text = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=622000,RESOLUTION=640x360
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=7146000,RESOLUTION=1920x1080
high.m3u8
"""
        manifest = hls.summarize(text, "https://x/playlist.m3u8")
        self.assertEqual(engine._first_variant(manifest, "https://x/playlist.m3u8"), "https://x/low.m3u8")
        self.assertEqual(engine._first_variant({"type": "media"}, "https://x/media.m3u8"), "https://x/media.m3u8")

    def test_a_time_signal_is_classified_by_its_segmentation_type(self):
        def command(segment_type: int) -> dict:
            raw = b"CUEI" + (12345).to_bytes(4, "big") + b"\x00\x00" + bytes([segment_type]) + b"\x00\x00"
            return {"command": 0x06, "descriptors": [{"tag": 0x02, "raw": raw.hex()}]}

        self.assertEqual(engine._command_class(command(0x22)), "SCTE35-BREAK-START")
        self.assertEqual(engine._command_class(command(0x23)), "SCTE35-BREAK-END")
        self.assertEqual(engine._command_class(command(0x10)), "SCTE35-PROGRAM")
        self.assertEqual(engine._command_class({"command": 0x06, "descriptors": []}), "SCTE35-CMD")


class StateTests(unittest.TestCase):
    def test_before_the_lag_the_picture_is_still_on_programme(self):
        found = events(playlist())
        start = at("2026-11-03T10:00:00Z")
        state, _, _ = engine.state_of(found, start + 10 - 18)  # what the live edge had 18 s ago
        self.assertEqual(state, "programa")

    def test_after_the_lag_it_is_an_ad_break(self):
        found = events(playlist())
        start = at("2026-11-03T10:00:00Z")
        state, since, left = engine.state_of(found, start + 20 - 18)
        self.assertEqual(state, "publicidad")
        self.assertEqual(left, 88)
        self.assertEqual(since, "2026-11-03T10:00:00Z")

    def test_the_duration_closes_it(self):
        found = events(playlist())
        start = at("2026-11-03T10:00:00Z")
        state, _, _ = engine.state_of(found, start + 91)
        self.assertEqual(state, "programa")

    def test_cue_in_closes_it_early(self):
        found = events(playlist(duration=300) + "#EXT-X-CUE-IN\nseg2.ts\n")
        start = at("2026-11-03T10:00:00Z")
        state, _, _ = engine.state_of(found, start + 60)
        self.assertEqual(state, "programa")

    def test_a_cue_in_is_timed_for_the_picture_too(self):
        """The playlist runs ahead of the player: the end must wait the same lag as the start."""
        found = events(playlist() + "#EXT-X-CUE-IN\nseg2.ts\n")
        cue_in = found[-1]
        cue_in["seen"] = at("2026-11-03T10:00:50Z")
        seen = cue_in["seen"]
        # state_of() is given the picture's clock: now - lag. 10 s of lag still to go, then none.
        self.assertEqual(engine.state_of(found, seen - 8)[0], "publicidad")
        self.assertEqual(engine.state_of(found, seen)[0], "programa")

    def test_mediaset_ignores_an_early_in(self):
        text = playlist() + "#EXT-X-CUE-IN\nseg2.ts\n"
        found = events(text, mediaset=True)
        start = at("2026-11-03T10:00:00Z")
        state, _, _ = engine.state_of(found, start + 20, mediaset=True)  # still 70 s to go
        self.assertEqual(state, "publicidad")

    def test_max_break_is_a_safety_net(self):
        found = events(playlist(duration=5000))
        start = at("2026-11-03T10:00:00Z")
        self.assertEqual(engine.state_of(found, start + 4000)[0], "publicidad")

    def test_a_break_without_duration_stays_until_its_in(self):
        text = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-CUE-OUT
#EXTINF:6.0,
seg.ts
"""
        found = events(text)
        seen = found[0]["seen"] = at("2026-11-03T10:00:00Z")
        state, _, left = engine.state_of(found, seen + 60)
        self.assertEqual((state, left), ("publicidad", None))


if __name__ == "__main__":
    unittest.main()
