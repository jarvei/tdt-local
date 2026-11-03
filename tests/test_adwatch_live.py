"""The engine against a real HTTP playlist: a break starts, the picture gets it after the lag, and it ends."""

from __future__ import annotations

import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tdtlocal.adwatch.engine import Vigilance
from tdtlocal.net import Net

OUT = "FC302500000000000000FFF014056AE836907FEFFE856F8090FE007B98A0000000000000E44A55DC"


def _utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Origin(BaseHTTPRequestHandler):
    started = 0.0

    def do_GET(self) -> None:
        if self.path.startswith("/master"):
            body = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nmedia.m3u8\n"
        else:
            start = _utc(Origin.started)
            body = (
                "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:1\n"
                f'#EXT-X-DATERANGE:ID="live",START-DATE="{start}",PLANNED-DURATION=2,SCTE35-OUT=0x{OUT}\n'
                "#EXTINF:2.0,\nseg1.ts\n#EXTINF:2.0,\nseg2.ts\n"
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


class LiveEngineTests(unittest.TestCase):
    def test_a_break_starts_after_the_lag_and_ends_with_its_duration(self):
        Origin.started = time.time() - 1  # the marker is one second old: the picture is not there yet
        server = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        changes: list[str] = []
        stop = threading.Event()
        watcher = Vigilance(
            "canal",
            "Canal",
            f"http://127.0.0.1:{server.server_port}/master.m3u8",
            {},
            Net(allow_private=True),
            lag=1.0,
            margin=0.0,
            interval=0.2,
            on_change=lambda state, _since, _left, _event, _previous: changes.append(state),
            stop=stop,
        )
        watcher.start()
        try:
            time.sleep(4.5)
        finally:
            stop.set()
            server.shutdown()
            server.server_close()
        self.assertEqual(changes[:2], ["publicidad", "programa"])
        self.assertGreaterEqual(watcher.left if watcher.left is not None else 0, 0)


if __name__ == "__main__":
    unittest.main()
