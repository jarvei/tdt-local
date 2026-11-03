"""SCTE-35 detection: the decoder, the playlist and segment scanners, the log, the watcher and its routes."""

from __future__ import annotations

import base64
import json
import time
import unittest
from unittest import mock
from urllib.parse import quote

from tdtlocal.scte import (
    crc32_mpeg,
    decode_cue,
    decode_splice_info,
    has_scte_emsg,
    inspect_ts,
    scan_playlist,
    segment_markers,
    splice_kind,
    splice_summary,
    strip_query,
)
from tdtlocal.scte_routes import to_csv
from tdtlocal.scte_watch import ScteLog

from .helpers import AppCase, ServerCase


# ---------------------------------------------------------------- builders for binary data
def with_crc(section: bytes) -> bytes:
    return section + crc32_mpeg(section).to_bytes(4, "big")


def splice_section(command_type: int, command: bytes, descriptors: bytes = b"") -> bytes:
    body = bytes([0, 0]) + (0).to_bytes(4, "big") + bytes([0])  # version, no encryption, pts_adjustment, cw_index
    body += bytes([0xFF, 0xF0 | (len(command) >> 8), len(command) & 0xFF, command_type]) + command
    body += len(descriptors).to_bytes(2, "big") + descriptors
    length = len(body) + 4
    return with_crc(bytes([0xFC, 0x30 | (length >> 8), length & 0xFF]) + body)


def pts(seconds: float, flag: int = 0xFE) -> bytes:
    ticks = int(seconds * 90000)
    return bytes([flag | ((ticks >> 32) & 1)]) + (ticks & 0xFFFFFFFF).to_bytes(4, "big")


def insert_command(event_id: int, out_of_network: bool = True, at: float = 10, duration: float | None = 30) -> bytes:
    flags = 0x40 | 0x0F | (0x80 if out_of_network else 0) | (0x20 if duration is not None else 0)
    command = event_id.to_bytes(4, "big") + bytes([0x7F, flags]) + pts(at)
    if duration is not None:
        command += pts(duration, 0xFE)
    return command + (7).to_bytes(2, "big") + bytes([1, 1])


def segmentation_descriptor(event_id: int, type_id: int, duration: float = 30) -> bytes:
    body = b"CUEI" + event_id.to_bytes(4, "big") + bytes([0x7F, 0xFF]) + int(duration * 90000).to_bytes(5, "big")
    body += bytes([0, 0, type_id, 1, 0])
    return bytes([0x02, len(body)]) + body


def time_signal_section(event_id: int, type_id: int) -> bytes:
    return splice_section(0x06, pts(10), segmentation_descriptor(event_id, type_id))


INSERT = splice_section(0x05, insert_command(1234))


# ---------------------------------------------------------------- the decoder
class DecoderTests(unittest.TestCase):
    def test_crc_is_zero_over_a_complete_section(self):
        self.assertEqual(crc32_mpeg(INSERT), 0)
        self.assertEqual(crc32_mpeg(b"123456789"), 0x0376E6E7)  # the standard check value of CRC-32/MPEG-2

    def test_splice_insert_out_of_network(self):
        info = decode_splice_info(INSERT)
        self.assertEqual(info["command"], "splice_insert")
        self.assertEqual(info["event_id"], 1234)
        self.assertTrue(info["out_of_network"])
        self.assertEqual(info["duration_s"], 30.0)
        self.assertEqual(info["pts_s"], 10.0)
        self.assertTrue(info["crc_ok"])
        self.assertEqual(splice_kind(info), "cue-out")
        self.assertIn("30 s", splice_summary(info))
        self.assertIn("evento 1234", splice_summary(info))

    def test_return_to_network_is_a_cue_in(self):
        info = decode_splice_info(splice_section(0x05, insert_command(9, out_of_network=False, duration=None)))
        self.assertEqual(splice_kind(info), "cue-in")
        self.assertNotIn("duration_s", info)

    def test_a_cancelled_insert_is_not_a_break(self):
        command = (5).to_bytes(4, "big") + bytes([0xFF])  # cancel indicator set
        info = decode_splice_info(splice_section(0x05, command))
        self.assertTrue(info["cancel"])
        self.assertEqual(splice_kind(info), "scte35")
        self.assertIn("cancelado", splice_summary(info))

    def test_time_signal_with_a_segmentation_descriptor(self):
        info = decode_splice_info(time_signal_section(77, 0x34))
        self.assertEqual(info["command"], "time_signal")
        seg = info["segmentation"][0]
        self.assertEqual((seg["event_id"], seg["type_id"], seg["duration_s"]), (77, 0x34, 30.0))
        self.assertIn("oportunidad publicitaria", seg["type"])
        self.assertEqual(splice_kind(info), "cue-out")
        self.assertEqual(splice_kind(decode_splice_info(time_signal_section(77, 0x35))), "cue-in")
        self.assertEqual(splice_kind(decode_splice_info(time_signal_section(77, 0x10))), "scte35")

    def test_a_damaged_section_is_reported_not_trusted(self):
        broken = bytearray(INSERT)
        broken[20] ^= 0xFF
        info = decode_splice_info(bytes(broken))
        self.assertFalse(info["crc_ok"])
        self.assertIn("CRC incorrecto", splice_summary(info))

    def test_encrypted_sections_are_recognised_but_not_read(self):
        data = bytearray(splice_section(0x05, insert_command(1)))
        data[4] |= 0x80
        info = decode_splice_info(bytes(data))
        self.assertTrue(info["encrypted"])
        self.assertNotIn("event_id", info)

    def test_garbage_is_none(self):
        for data in (b"", b"\x00" * 40, INSERT[:20], INSERT[:-1], b"\xfc" + b"\xff" * 30):
            self.assertIsNone(decode_splice_info(data), msg=data[:8])

    def test_truncated_descriptors_do_not_hide_the_command(self):
        section = splice_section(0x06, pts(10), segmentation_descriptor(1, 0x34)[:-6])
        info = decode_splice_info(section)
        self.assertEqual(info["command"], "time_signal")

    def test_hex_and_base64_payloads(self):
        by_hex = decode_cue("0x" + INSERT.hex())
        by_base64 = decode_cue(base64.b64encode(INSERT).decode())
        by_urlsafe = decode_cue(base64.urlsafe_b64encode(INSERT).decode().rstrip("="))
        self.assertEqual(by_hex["event_id"], 1234)
        self.assertEqual(by_base64, by_hex)
        self.assertEqual(by_urlsafe, by_hex)
        for bad in ("", "0xZZ", "no es base64 !!", base64.b64encode(b"hola").decode()):
            self.assertIsNone(decode_cue(bad), msg=bad)


# ---------------------------------------------------------------- playlists
HEX = "0x" + INSERT.hex()
B64 = base64.b64encode(INSERT).decode()


class PlaylistTests(unittest.TestCase):
    def test_a_playlist_without_markers_has_none(self):
        text = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:00Z\n#EXTINF:6,\na.ts\n"
        self.assertEqual(scan_playlist(text), [])

    def test_cue_out_in_and_the_continuation(self):
        markers = scan_playlist(
            "#EXTM3U\n#EXT-X-CUE-OUT:DURATION=30\n#EXTINF:6,\na1.ts\n"
            "#EXT-X-CUE-OUT-CONT:ElapsedTime=6,Duration=30\n#EXTINF:6,\na2.ts\n"
            "#EXT-X-CUE-IN\n#EXTINF:6,\na3.ts?token=secret\n"
        )
        self.assertEqual([m["kind"] for m in markers], ["cue-out", "cue-out-cont", "cue-in"])
        self.assertEqual([m["ident"] for m in markers], ["out@a1.ts", "cont", "in@a3.ts"])
        self.assertEqual([m["detail"] for m in markers], ["30 s", "6 s de 30 s", ""])

    def test_plain_and_adobe_style_durations(self):
        out, cont = scan_playlist("#EXT-X-CUE-OUT:15.5\n#EXTINF:6,\na.ts\n#EXT-X-CUE-OUT-CONT:3/20\n")
        self.assertEqual((out["detail"], cont["detail"]), ("15.5 s", "3 s de 20 s"))

    def test_the_same_break_in_a_later_refresh_has_the_same_identity(self):
        first = scan_playlist("#EXT-X-CUE-OUT:30\n#EXTINF:6,\na1.ts\n#EXTINF:6,\na2.ts\n")
        later = scan_playlist("#EXTINF:6,\na0.ts\n#EXT-X-CUE-OUT:30\n#EXTINF:6,\na1.ts\n#EXTINF:6,\na2.ts\n")
        other = scan_playlist("#EXT-X-CUE-OUT:30\n#EXTINF:6,\nb1.ts\n")
        self.assertEqual(first[0]["ident"], later[0]["ident"])
        self.assertNotEqual(first[0]["ident"], other[0]["ident"])

    def test_daterange_with_scte35_payloads(self):
        out, end = scan_playlist(
            f'#EXT-X-DATERANGE:ID="ad1",START-DATE="2026-01-01T00:00:00Z",DURATION=30,SCTE35-OUT={HEX}\n'
            f'#EXT-X-DATERANGE:ID="ad1",START-DATE="2026-01-01T00:00:00Z",SCTE35-IN={HEX}\n'
        )
        self.assertEqual((out["kind"], out["ident"]), ("cue-out", "ev1234"))  # the event id identifies the break
        self.assertEqual((end["kind"], end["ident"]), ("cue-in", "ev1234"))
        self.assertEqual(out["scte"]["event_id"], 1234)
        self.assertIn("30 s", out["detail"])
        (unreadable,) = scan_playlist('#EXT-X-DATERANGE:ID="ad2",START-DATE="2026-01-01T00:00:00Z",SCTE35-OUT=0xZZ\n')
        self.assertEqual((unreadable["kind"], unreadable["ident"]), ("cue-out", "ad2/out"))
        self.assertNotIn("scte", unreadable)

    def test_daterange_interstitials_and_plain_metadata(self):
        inter, meta = scan_playlist(
            '#EXT-X-DATERANGE:ID="i",CLASS="com.apple.hls.interstitial",X-ASSET-URI="https://a/b.m3u8"\n'
            '#EXT-X-DATERANGE:ID="p",CLASS="programa",START-DATE="2026-01-01T00:00:00Z"\n'
            '#EXT-X-DATERANGE:START-DATE="2026-01-01T00:00:00Z"\n'
        )
        self.assertEqual((inter["kind"], meta["kind"]), ("interstitial", "daterange"))
        self.assertEqual(meta["detail"], "programa")

    def test_binary_payload_tags_take_their_identity_from_the_event(self):
        for tag in (f"#EXT-OATCLS-SCTE35:{B64}", f"#EXT-X-SPLICEPOINT-SCTE35:{B64}", f'#EXT-X-SCTE35:CUE="{B64}"'):
            (marker,) = scan_playlist(tag + "\n#EXTINF:6,\na.ts\n")
            self.assertEqual((marker["kind"], marker["ident"]), ("cue-out", "ev1234"), msg=tag)

    def test_a_cue_out_with_a_payload_is_identified_by_the_event_not_the_segment(self):
        (marker,) = scan_playlist(f"#EXT-X-CUE-OUT:Duration=30,SCTE35={B64}\n#EXTINF:6,\na.ts\n")
        self.assertEqual(marker["ident"], "ev1234")

    def test_unknown_cue_tags_are_kept_rather_than_lost(self):
        (marker,) = scan_playlist("#EXT-X-FOO-CUE-BAR:1\n#EXTINF:6,\na.ts\n")
        self.assertEqual(marker["kind"], "scte35")
        self.assertTrue(marker["ident"].startswith("EXT-X-FOO-CUE-BAR@"))

    def test_raw_text_is_capped(self):
        (marker,) = scan_playlist("#EXT-X-CUE-OUT:30 " + "x" * 1000 + "\n")
        self.assertLessEqual(len(marker["raw"]), 300)

    def test_strip_query_drops_tokens(self):
        self.assertEqual(strip_query("https://h.example/a/b.m3u8?token=abc#x"), "https://h.example/a/b.m3u8")
        self.assertEqual(strip_query("not a url?x=1"), "not a url")


# ---------------------------------------------------------------- segments
def ts_packet(pid: int, payload: bytes, start: bool = True) -> bytes:
    header = bytes([0x47, (0x40 if start else 0) | (pid >> 8), pid & 0xFF, 0x10])
    return header + payload.ljust(184, b"\xff")


def pat(program_pid: int = 0x100) -> bytes:
    entries = (1).to_bytes(2, "big") + bytes([0xE0 | (program_pid >> 8), program_pid & 0xFF])
    length = 5 + len(entries) + 4
    return b"\x00" + with_crc(bytes([0x00, 0xB0 | (length >> 8), length & 0xFF, 0, 1, 0xC1, 0, 0]) + entries)


def pmt(streams: list[tuple[int, int, bytes]], program_info: bytes = b"") -> bytes:
    """``streams``: ``(stream_type, pid, descriptors)``."""
    es = b"".join(
        bytes([t, 0xE0 | (pid >> 8), pid & 0xFF, 0xF0 | (len(d) >> 8), len(d) & 0xFF]) + d for t, pid, d in streams
    )
    length = 9 + len(program_info) + len(es) + 4
    head = bytes([0x02, 0xB0 | (length >> 8), length & 0xFF, 0, 1, 0xC1, 0, 0, 0xE1, 0x01])
    head += bytes([0xF0 | (len(program_info) >> 8), len(program_info) & 0xFF]) + program_info
    return b"\x00" + with_crc(head + es)


def transport_stream(streams, scte_pid=None, section=INSERT, junk=0) -> bytes:
    data = b"\x12" * junk + ts_packet(0, pat()) + ts_packet(0x100, pmt(streams))
    if scte_pid is not None:
        data += ts_packet(scte_pid, b"\x00" + section)
    return data + ts_packet(0x101, b"\x00\x00\x01" + b"v" * 100) * 2


class SegmentTests(unittest.TestCase):
    video = (0x1B, 0x101, b"")

    def test_finds_the_scte35_pid_and_the_message_on_it(self):
        found = inspect_ts(transport_stream([self.video, (0x86, 0x1F3, b"")], scte_pid=0x1F3))
        self.assertEqual(found["programs"], 1)
        self.assertEqual(found["pids"], {0x1F3: "stream_type 0x86"})
        self.assertEqual(found["cues"][0]["event_id"], 1234)

    def test_a_sample_that_starts_in_the_middle_of_a_packet(self):
        found = inspect_ts(transport_stream([self.video, (0x86, 0x1F3, b"")], junk=57))
        self.assertEqual(list(found["pids"]), [0x1F3])

    def test_cuei_registration_marks_a_private_data_pid(self):
        cuei = bytes([0x05, 4]) + b"CUEI"
        found = inspect_ts(transport_stream([self.video, (0x06, 0x1F4, cuei)]))
        self.assertEqual(found["pids"], {0x1F4: "descriptor CUEI"})

    def test_cuei_in_the_program_info_is_noticed(self):
        self.assertFalse(inspect_ts(transport_stream([self.video]))["cuei"])
        packet = ts_packet(0x100, pmt([self.video], bytes([0x05, 4]) + b"CUEI"))
        self.assertTrue(inspect_ts(ts_packet(0, pat()) + packet + packet)["cuei"])

    def test_a_stream_without_scte35(self):
        found = inspect_ts(transport_stream([self.video, (0x0F, 0x102, b"")]))
        self.assertEqual((found["pids"], found["cues"]), ({}, []))
        self.assertEqual(segment_markers(transport_stream([self.video])), [])

    def test_not_a_transport_stream(self):
        self.assertIsNone(inspect_ts(b"ftypiso5" + b"\x00" * 400))
        self.assertIsNone(inspect_ts(b""))
        self.assertIsNone(inspect_ts(b"\x47" * 100))

    def test_damaged_tables_do_not_raise(self):
        data = bytearray(transport_stream([self.video, (0x86, 0x1F3, b"")]))
        for i in range(190, 380, 7):
            data[i] = 0xFF
        inspect_ts(bytes(data))

    def test_segment_markers_for_ts_and_emsg(self):
        markers = segment_markers(transport_stream([self.video, (0x86, 0x1F3, b"")], scte_pid=0x1F3))
        self.assertEqual({m["kind"] for m in markers}, {"ts-pid", "cue-out"})
        fragment = b"\x00\x00\x00\x20moof....emsg" + b"urn:scte:scte35:2013:bin\x00" + b"\x00" * 40
        self.assertTrue(has_scte_emsg(fragment))
        self.assertEqual([m["kind"] for m in segment_markers(fragment)], ["emsg"])
        self.assertEqual(segment_markers(b"plain bytes"), [])


# ---------------------------------------------------------------- the log
class LogTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sub" / "scte.jsonl"

    def event(self, n, channel="a"):
        return {"ts": 1000 + n, "channel": channel, "name": channel.upper(), "kind": "cue-out", "detail": str(n)}

    def test_events_get_increasing_ids_and_survive_a_restart(self):
        log = ScteLog(self.path, 100)
        ids = [log.add(self.event(n))["id"] for n in range(3)]
        self.assertEqual(ids, [1, 2, 3])
        again = ScteLog(self.path, 100)
        again.load()
        self.assertEqual([e["id"] for e in again.all()], [1, 2, 3])
        self.assertEqual(again.add(self.event(9))["id"], 4)
        self.assertEqual(again.last_id, 4)

    def test_only_the_newest_are_kept(self):
        log = ScteLog(self.path, 5)
        for n in range(100):
            log.add(self.event(n))
        self.assertEqual([e["id"] for e in log.all()], [96, 97, 98, 99, 100])
        self.assertLess(len(self.path.read_text(encoding="utf-8").splitlines()), 100)  # the file is compacted too
        reloaded = ScteLog(self.path, 5)
        reloaded.load()
        self.assertEqual(reloaded.last_id, 100)

    def test_lowering_the_limit_trims_the_file(self):
        log = ScteLog(self.path, 50)
        for n in range(10):
            log.add(self.event(n))
        log.set_keep(3)
        self.assertEqual(len(log), 3)
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 3)

    def test_since_limit_and_summary(self):
        log = ScteLog(self.path, 100)
        for n in range(6):
            log.add(self.event(n, "a" if n < 4 else "b"))
        self.assertEqual([e["id"] for e in log.since(2)], [3, 4, 5, 6])
        self.assertEqual([e["id"] for e in log.since(0, 2)], [5, 6])
        self.assertEqual(log.since(0, 0), [])
        summary = log.summary()
        self.assertEqual([(r["channel"], r["count"]) for r in summary], [("a", 4), ("b", 2)])
        self.assertEqual(summary[0]["kinds"], {"cue-out": 4})

    def test_corrupt_lines_are_skipped(self):
        self.path.parent.mkdir(parents=True)
        good = '{"id": 1, "ts": 1, "channel": "a", "kind": "x"}'
        self.path.write_text(f'{good}\nnot json\n[1]\n{{"id": "x"}}\n', encoding="utf-8")
        log = ScteLog(self.path, 10)
        log.load()
        self.assertEqual([e["id"] for e in log.all()], [1])

    def test_clear_empties_memory_and_file(self):
        log = ScteLog(self.path, 10)
        log.add(self.event(1))
        self.assertEqual(log.clear(), 1)
        self.assertEqual(len(log), 0)
        self.assertFalse(self.path.exists())
        self.assertEqual(log.clear(), 0)

    def test_an_unwritable_file_does_not_lose_the_event(self):
        log = ScteLog(self.path, 10)
        with mock.patch.object(type(self.path), "open", side_effect=PermissionError("no")):
            log.add(self.event(1))
        self.assertEqual(len(log), 1)


# ---------------------------------------------------------------- the watcher
def channel(cid, name, *sources):
    return {"id": cid, "name": name, "category": "Nacionales", "group": "", "sources": list(sources), "logo": ""}


BREAK = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-CUE-OUT:30\n#EXTINF:6,\nseg1.ts\n#EXT-X-CUE-IN\n#EXTINF:6,\nseg2.ts\n"


class WatcherTests(AppCase):
    config_overrides = {"scte_enabled": True}  # noqa: RUF012

    @property
    def scte(self):
        return self.app.scte

    def kinds(self):
        return [e["kind"] for e in self.scte.log.all()]

    def test_nothing_is_recorded_while_disabled(self):
        self.app.config.scte_enabled = False
        self.scte.observe("https://h.example/a.m3u8", BREAK)
        self.assertEqual(len(self.scte.log), 0)

    def test_a_watched_playlist_is_recorded_once(self):
        self.scte.observe("https://h.example/a.m3u8?token=1", BREAK)
        self.assertEqual(self.kinds(), ["cue-out", "cue-in"])
        self.scte.observe("https://h.example/a.m3u8?token=2", BREAK)  # the next refresh shows the same break
        self.assertEqual(len(self.scte.log), 2)
        event = self.scte.log.all()[0]
        self.assertEqual(event["url"], "https://h.example/a.m3u8")  # no token stored
        self.assertEqual(event["source"], "reproducción")
        self.assertEqual(event["name"], "h.example")  # an unknown channel is told by its host

    def test_a_marker_is_reported_again_after_it_expires(self):
        self.scte.observe("https://h.example/a.m3u8", BREAK)
        with mock.patch("tdtlocal.scte_watch.time.time", return_value=time.time() + 7 * 3600):
            self.scte.observe("https://h.example/a.m3u8", BREAK)
        self.assertEqual(len(self.scte.log), 4)

    def test_a_marker_in_a_long_dvr_window_is_not_reported_every_few_minutes(self):
        self.scte.observe("https://h.example/a.m3u8", BREAK)
        with mock.patch("tdtlocal.scte_watch.time.time", return_value=time.time() + 3600):
            self.scte.observe("https://h.example/a.m3u8", BREAK)
        self.assertEqual(len(self.scte.log), 2)

    def test_one_break_described_by_several_tags_is_reported_once(self):
        repeated = (
            "#EXT-X-CUE-OUT:30\n#EXTINF:6,\ns1.ts\n#EXT-X-CUE-OUT:30\n#EXTINF:6,\ns2.ts\n"
            f"#EXT-OATCLS-SCTE35:{B64}\n#EXTINF:6,\ns3.ts\n"
        )
        self.scte.observe("https://h.example/a.m3u8", repeated)
        self.assertEqual(self.kinds(), ["cue-out"])
        self.scte.observe("https://h.example/a.m3u8", repeated)  # and not again on the next refresh
        self.assertEqual(self.kinds(), ["cue-out"])

    def test_a_cue_in_ends_the_break_so_the_next_one_counts(self):
        self.scte.observe("https://h.example/a.m3u8", BREAK)
        later = BREAK.replace("seg1.ts", "seg7.ts").replace("seg2.ts", "seg8.ts")
        self.scte.observe("https://h.example/a.m3u8", later)
        self.assertEqual(self.kinds(), ["cue-out", "cue-in", "cue-out", "cue-in"])

    def test_the_break_is_over_after_its_duration(self):
        self.scte.observe("https://h.example/a.m3u8", "#EXT-X-CUE-OUT:30\n#EXTINF:6,\nx1.ts\n")
        with mock.patch("tdtlocal.scte_watch.time.time", return_value=time.time() + 60):
            self.scte.observe("https://h.example/a.m3u8", "#EXT-X-CUE-OUT:30\n#EXTINF:6,\nx9.ts\n")
        self.assertEqual(self.kinds(), ["cue-out", "cue-out"])

    def test_variants_and_segments_belong_to_the_channel_of_the_master(self):
        self.app.store.channels = [channel("la-1", "La 1", "https://h.example/live/master.m3u8?t=1")]
        master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow/index.m3u8\n"
        self.scte.observe("https://h.example/live/master.m3u8?t=2", master)
        self.scte.observe("https://h.example/live/low/index.m3u8?t=3", BREAK)
        self.assertEqual({(e["channel"], e["name"]) for e in self.scte.log.all()}, {("la-1", "La 1")})

    def test_a_custom_channel_is_found_by_its_tag(self):
        self.app.store.custom = [channel("mio", "Mi canal", "https://c.example/x.m3u8")]
        self.scte.observe("https://other.example/y.m3u8", BREAK, "mio")
        self.assertEqual(self.scte.log.all()[0]["name"], "Mi canal")

    def test_a_restart_does_not_report_known_markers_again(self):
        self.scte.observe("https://h.example/a.m3u8", BREAK)
        again = self.make_app(scte_enabled=True)
        again.scte.load()
        again.scte.observe("https://h.example/a.m3u8", BREAK)
        self.assertEqual(len(again.scte.log), 2)

    def test_clear_forgets_what_was_seen(self):
        self.scte.observe("https://h.example/a.m3u8", BREAK)
        self.assertEqual(self.scte.clear(), 2)
        self.scte.observe("https://h.example/a.m3u8", BREAK)
        self.assertEqual(len(self.scte.log), 2)

    def test_a_broken_playlist_never_raises(self):
        with mock.patch("tdtlocal.scte_watch.scan_playlist", side_effect=RuntimeError("boom")):
            self.scte.observe("https://h.example/a.m3u8", BREAK)
        self.assertEqual(len(self.scte.log), 0)

    def test_reconfigure_applies_the_log_size(self):
        for n in range(8):
            self.scte.observe(f"https://h.example/{n}.m3u8", BREAK)
        self.app.config.scte_keep = 100
        self.scte.reconfigure()
        self.assertEqual(self.scte.log.keep, 100)

    def test_request_scan_explains_why_not(self):
        self.assertFalse(self.scte.request_scan()[0])  # scope "viewing"
        self.app.config.scte_scope = "all"
        self.assertEqual(self.scte.request_scan(), (True, ""))
        self.app.config.scte_enabled = False
        self.assertIn("desactivada", self.scte.request_scan()[1])


class ScanTests(AppCase):
    config_overrides = {"scte_enabled": True, "scte_scope": "all"}  # noqa: RUF012

    def setUp(self):
        super().setUp()
        o = self.origin
        o.routes["/live/master.m3u8"] = (200, {}, b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n")
        o.routes["/live/v.m3u8"] = (200, {}, BREAK.encode())
        o.routes["/quiet/index.m3u8"] = (200, {}, b"#EXTM3U\n#EXTINF:6,\ns.ts\n")
        o.routes["/ts/index.m3u8"] = (200, {}, b"#EXTM3U\n#EXTINF:6,\nseg.ts\n")
        o.routes["/ts/seg.ts"] = (200, {}, transport_stream([(0x1B, 0x101, b""), (0x86, 0x1F3, b"")]))
        self.app.store.channels = [
            channel("a", "Canal A", o.url("/live/master.m3u8?x=1")),
            channel("b", "Canal B", o.url("/quiet/index.m3u8")),
            channel("c", "Canal C", o.url("/missing.m3u8")),
        ]

    def test_scanning_all_channels(self):
        self.assertTrue(self.app.scte.scan())
        events = self.app.scte.log.all()
        self.assertEqual([(e["channel"], e["kind"]) for e in events], [("a", "cue-out"), ("a", "cue-in")])
        self.assertEqual(events[0]["source"], "exploración")
        scan = self.app.scte.status()["scan"]
        self.assertEqual((scan["total"], scan["done"], scan["errors"], scan["running"]), (3, 3, 1, False))
        self.assertIn("Canal C", scan["error"])
        self.assertTrue(scan["last"])

    def test_a_scan_skips_what_is_left_once_the_app_closes(self):
        """The pool's threads hold the process open until the whole queue is done, so closing must drain it."""
        self.app.scte._stop.set()
        self.assertTrue(self.app.scte.scan())
        self.assertEqual(self.app.scte.log.all(), [])
        self.assertEqual(self.app.scte.status()["scan"]["done"], 0)

    def test_the_list_scope_only_looks_at_the_named_channels(self):
        self.app.config.scte_scope = "list"
        self.app.config.scte_channels = "  canal b , A "  # by name or id, any case, extra spaces
        self.assertEqual({t["id"] for t in self.app.scte._targets()}, {"a", "b"})
        self.app.config.scte_channels = ""
        self.assertEqual(self.app.scte._targets(), [])

    def test_only_usable_streams_are_targets(self):
        o = self.origin
        self.app.store.channels = [
            channel("ok", "Bien", o.url("/quiet/index.m3u8")),
            channel("bad", "Caído", o.url("/live/master.m3u8")),
            channel("yt", "YouTube", "https://www.youtube.com/watch?v=abc"),
            channel("tpl", "Plantilla", "https://h.example/a.m3u8?id=[DEVICE_ID]"),
            channel("mp3", "Radio", "https://h.example/radio.mp3"),
            channel("two", "Dos", o.url("/live/master.m3u8"), o.url("/quiet/index.m3u8")),
        ]
        self.app.store.status = {o.url("/live/master.m3u8"): {"ok": False}}
        targets = {t["id"]: t["url"] for t in self.app.scte._targets()}
        self.assertEqual(set(targets), {"ok", "two"})
        self.assertTrue(targets["two"].endswith("/quiet/index.m3u8"))  # the first source that is not known to fail

    def test_custom_headers_travel_with_the_probe(self):
        self.app.store.custom = [channel("mio", "Mío", self.origin.url("/quiet/index.m3u8"))]
        self.app.store.custom_headers = {"mio": {"Origin": "https://o.example"}}
        self.app.store.channels = []
        self.app.scte.scan()
        self.assertEqual(self.origin.requests[-1][1].get("origin"), "https://o.example")

    def test_segments_are_inspected_only_when_asked(self):
        self.app.store.channels = [channel("t", "TS", self.origin.url("/ts/index.m3u8"))]
        self.app.scte.scan()
        self.assertEqual(len(self.app.scte.log), 0)
        self.assertFalse(any(path.endswith("seg.ts") for path, _ in self.origin.requests))
        self.app.config.scte_inspect_segments = True
        self.app.scte.scan()
        (event,) = self.app.scte.log.all()
        self.assertEqual((event["kind"], event["channel"]), ("ts-pid", "t"))
        self.assertIn("0x86", event["raw"])
        segment_request = next(h for p, h in self.origin.requests if p.endswith("seg.ts"))
        self.assertIn("bytes=0-", segment_request["range"])
        self.app.scte.scan()  # a static property of the stream: reported once, not at every scan
        self.assertEqual(len(self.app.scte.log), 1)

    def test_two_scans_do_not_overlap(self):
        self.assertTrue(self.app.scte._scan_lock.acquire(blocking=False))
        try:
            self.assertFalse(self.app.scte.scan())
        finally:
            self.app.scte._scan_lock.release()

    def test_the_background_loop_scans_when_asked_and_stops(self):
        import threading

        stop = threading.Event()
        worker = threading.Thread(target=self.app.scte.run, args=(stop,), daemon=True)
        worker.start()
        deadline = time.time() + 15
        while time.time() < deadline and not len(self.app.scte.log):
            time.sleep(0.05)
        stop.set()
        self.app.scte._wake.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertGreaterEqual(len(self.app.scte.log), 2)


# ---------------------------------------------------------------- HTTP
class RouteTests(ServerCase):
    config_overrides = {"scte_enabled": True}  # noqa: RUF012

    def add_events(self, count=3):
        for n in range(count):
            self.app.scte.observe(f"https://h{n}.example/a.m3u8", BREAK)

    def test_listing_with_after_limit_and_summary(self):
        self.add_events(2)
        status, data = self.get_json("/api/scte?summary=1")
        self.assertEqual(status, 200)
        self.assertTrue(data["enabled"] and data["notify"])
        self.assertEqual((data["last_id"], len(data["events"])), (4, 4))
        self.assertNotIn("key", data["events"][0])
        self.assertEqual(len(data["summary"]), 2)
        self.assertEqual(data["status"]["scope"], "viewing")
        _, newer = self.get_json("/api/scte?after=3")
        self.assertEqual([e["id"] for e in newer["events"]], [4])
        self.assertNotIn("summary", newer)
        _, none = self.get_json("/api/scte?limit=0")
        self.assertEqual((none["events"], none["last_id"]), ([], 4))

    def test_bad_numbers_fall_back(self):
        self.add_events(1)
        status, data = self.get_json("/api/scte?after=abc&limit=zzz")
        self.assertEqual((status, len(data["events"])), (200, 2))
        status, data = self.get_json("/api/scte?limit=999999")
        self.assertEqual((status, len(data["events"])), (200, 2))

    def test_export_as_json_and_csv(self):
        self.add_events(1)
        self.app.scte.log.add({"ts": 5, "channel": "x", "name": "=HYPERLINK(1)", "kind": "cue-out", "detail": "+1"})
        status, headers, body = self.request("GET", "/api/scte/export")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        self.assertEqual(len(json.loads(body)), 3)
        self.assertNotIn(b'"key"', body)
        status, headers, body = self.request("GET", "/api/scte/export?fmt=csv")
        self.assertIn("text/csv", headers["content-type"])
        text = body.decode("utf-8-sig")
        self.assertTrue(text.startswith("id,fecha,canal,tipo"))
        self.assertIn("'=HYPERLINK(1)", text)  # a spreadsheet must not run it
        self.assertIn("'+1", text)

    def test_csv_of_nothing_has_only_the_header(self):
        self.assertEqual(to_csv([]).strip(), "id,fecha,canal,tipo,detalle,origen,url,etiqueta")

    def test_clearing_the_log(self):
        self.add_events(1)
        status, data = self.post_json("/api/scte/clear")
        self.assertEqual((status, data), (200, {"ok": True, "removed": 2}))
        self.assertEqual(self.get_json("/api/scte")[1]["events"], [])

    def test_scan_request(self):
        status, data = self.post_json("/api/scte/scan")
        self.assertEqual(status, 409)
        self.assertIn("solo lo que veo", data["error"])
        self.app.config.scte_scope = "all"
        self.assertEqual(self.post_json("/api/scte/scan")[0], 200)

    def test_changes_are_refused_from_other_machines(self):
        self.add_events(1)
        with mock.patch("tdtlocal.scte_routes._from_this_machine", return_value=False):
            for path in ("/api/scte/scan", "/api/scte/clear"):
                self.assertEqual(self.post_json(path)[0], 403, msg=path)
            self.assertEqual(self.get_json("/api/scte")[0], 200)  # reading is fine
        self.assertEqual(len(self.app.scte.log), 2)

    def test_the_request_guard_applies(self):
        self.assertEqual(self.post_json("/api/scte/clear", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.get_json("/api/scte", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_the_channel_list_says_whether_markers_are_watched(self):
        self.assertTrue(self.get_json("/api/channels")[1]["meta"]["scte"])
        self.app.config.scte_enabled = False
        self.assertFalse(self.get_json("/api/channels")[1]["meta"]["scte"])


class ProxyIntegrationTests(ServerCase):
    config_overrides = {"scte_enabled": True}  # noqa: RUF012

    def test_a_playlist_served_through_the_proxy_is_looked_at(self):
        self.origin.routes["/live.m3u8"] = (200, {"Content-Type": "application/vnd.apple.mpegurl"}, BREAK.encode())
        url = self.origin.url("/live.m3u8?token=secret")
        self.app.net.allow(url)
        status, _, body = self.request("GET", f"/proxy?u={quote(url, safe='')}")
        self.assertEqual(status, 200)
        self.assertIn(b"/proxy?u=", body)  # playback is untouched
        events = self.app.scte.log.all()
        self.assertEqual([e["kind"] for e in events], ["cue-out", "cue-in"])
        self.assertNotIn("token", json.dumps(events))

    def test_nothing_is_logged_when_it_is_off(self):
        self.app.config.scte_enabled = False
        self.origin.routes["/live.m3u8"] = (200, {"Content-Type": "application/vnd.apple.mpegurl"}, BREAK.encode())
        url = self.origin.url("/live.m3u8")
        self.app.net.allow(url)
        self.request("GET", f"/proxy?u={quote(url, safe='')}")
        self.assertEqual(len(self.app.scte.log), 0)


class SettingsIntegrationTests(ServerCase):
    def test_the_settings_are_validated_saved_and_applied(self):
        with mock.patch.object(self.app.scte, "reconfigure") as reconfigure:
            status, _ = self.post_json(
                "/api/settings",
                {"scte_enabled": True, "scte_scope": "list", "scte_channels": "La 1, La 2", "scte_interval": 30},
            )
        self.assertEqual(status, 200)
        reconfigure.assert_called_once_with()
        config = self.app.config
        self.assertEqual((config.scte_enabled, config.scte_scope, config.scte_interval), (True, "list", 30))
        saved = json.loads((config.data_dir / "settings.json").read_text(encoding="utf-8"))["values"]
        self.assertEqual(saved["scte_channels"], "La 1, La 2")
        self.assertNotIn("scte_notify", saved)  # still the default

    def test_wrong_values_are_refused(self):
        status, data = self.post_json("/api/settings", {"scte_scope": "todo", "scte_interval": 2, "scte_keep": 5})
        self.assertEqual(status, 400)
        self.assertEqual(set(data["errors"]), {"scte_scope", "scte_interval", "scte_keep"})
        self.assertTrue(self.app.config.scte_enabled)

    def test_it_is_on_by_default(self):
        config = self.app.config
        self.assertEqual(
            (config.scte_enabled, config.scte_scope, config.scte_inspect_segments, config.scte_notify),
            (True, "viewing", False, True),
        )


if __name__ == "__main__":
    unittest.main()
