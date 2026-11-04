import unittest
from unittest import mock

from tdtlocal.net import Net
from tdtlocal.youtube import YouTubeLive, live_page, live_video, video_channel, video_page

PAGE = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=WUTDiO_fXdE">'
    '"playabilityStatus":{"status":"OK","playableInEmbed":true,'
    '"liveStreamability":{"liveStreamabilityRenderer":{"videoId":"WUTDiO_fXdE"}}},'
    '"videoDetails":{"videoId":"WUTDiO_fXdE","title":"Live","lengthSeconds":"0","isLive":true,'
    '"channelId":"UCoMdktPbSTixAyNGwb-UYkQ"},'
    '"liveBroadcastDetails":{"isLiveNow":true}'
)
DEAD_WATCH = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=iipR5yUp36o">'
    '"playabilityStatus":{"status":"UNPLAYABLE","reason":"This live stream recording is not available."}'
    '"channelId":"UCBi2mrWuNuyYy4gbM6fU18Q"'
)
ABC_LIVE = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=KQhNZQ_Uag8">'
    '"playabilityStatus":{"status":"OK","playableInEmbed":true},'
    '"videoDetails":{"videoId":"KQhNZQ_Uag8","title":"Live","lengthSeconds":"0","isLive":true,'
    '"channelId":"UCBi2mrWuNuyYy4gbM6fU18Q"}'
)
AMERICA_WATCH = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=zcWXboTnous">'
    '"playabilityStatus":{"status":"OK","playableInEmbed":true},'
    '"videoDetails":{"videoId":"zcWXboTnous","title":"Live","lengthSeconds":"0","isLive":true,'
    '"channelId":"UC6NVDkuzY2exMOVFw4i9oHw"}'
)
AMERICA_LIVE = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=BOtvAr8DPcg">'
    '"playabilityStatus":{"status":"OK","playableInEmbed":true},'
    '"videoDetails":{"videoId":"BOtvAr8DPcg","title":"Live","lengthSeconds":"0","isLive":true,'
    '"channelId":"UC6NVDkuzY2exMOVFw4i9oHw"}'
)
NO_LIVE_CHANNEL = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=BOtvAr8DPcg">'
    '"playabilityStatus":{"status":"OK","playableInEmbed":true}'
)


class LivePageTests(unittest.TestCase):
    def test_normalizes_channel_and_handle_lives(self):
        self.assertEqual(
            live_page("https://www.youtube.com/@SkyNews/live"),
            "https://www.youtube.com/@SkyNews/live?hl=en",
        )
        self.assertEqual(
            live_page("https://youtube.com/channel/UCoMdktPbSTixAyNGwb-UYkQ/live/"),
            "https://www.youtube.com/channel/UCoMdktPbSTixAyNGwb-UYkQ/live?hl=en",
        )

    def test_rejects_anything_that_is_not_a_channel_live(self):
        for url in (
            "https://www.youtube.com/watch?v=WUTDiO_fXdE",
            "https://www.youtube.com/@SkyNews",
            "https://www.youtube.com/@SkyNews/live/extra",
            "https://www.youtube.com:8443/@SkyNews/live",
            "https://evil.example/@SkyNews/live",
            "http://127.0.0.1/@SkyNews/live",
            "",
        ):
            self.assertIsNone(live_page(url), url)


class VideoPageTests(unittest.TestCase):
    def test_normalizes_direct_live_videos(self):
        self.assertEqual(
            video_page("https://www.youtube.com/live/iipR5yUp36o"),
            "https://www.youtube.com/watch?v=iipR5yUp36o&hl=en",
        )
        self.assertEqual(
            video_page("https://youtube.com/live/zcWXboTnous/?x=1"),
            "https://www.youtube.com/watch?v=zcWXboTnous&hl=en",
        )

    def test_rejects_other_videos_and_hosts(self):
        for url in (
            "https://www.youtube.com/watch?v=iipR5yUp36o",
            "https://www.youtube.com/embed/iipR5yUp36o",
            "https://www.youtube.com/live/iipR5yUp36o/extra",
            "https://www.youtube.com:8443/live/iipR5yUp36o",
            "https://evil.example/live/iipR5yUp36o",
            "https://www.youtube.com/live/short",
            "",
        ):
            self.assertIsNone(video_page(url), url)


class VideoChannelTests(unittest.TestCase):
    def test_reads_the_channel_from_the_video_details(self):
        self.assertEqual(video_channel(PAGE), "UCoMdktPbSTixAyNGwb-UYkQ")

    def test_falls_back_to_the_channel_of_an_unplayable_page(self):
        self.assertEqual(video_channel(DEAD_WATCH), "UCBi2mrWuNuyYy4gbM6fU18Q")

    def test_returns_none_without_a_channel(self):
        self.assertIsNone(video_channel("<html></html>"))


class LiveVideoTests(unittest.TestCase):
    def test_reads_the_canonical_live_video(self):
        self.assertEqual(live_video(PAGE), "WUTDiO_fXdE")

    def test_requires_a_live_broadcast(self):
        self.assertIsNone(live_video(PAGE.replace('"isLive":true', '"isLive":false')))

    def test_accepts_a_channel_page_without_live_now(self):
        page = PAGE.replace('"liveBroadcastDetails":{"isLiveNow":true}', "")
        self.assertEqual(live_video(page), "WUTDiO_fXdE")

    def test_refuses_a_non_embeddable_or_failed_video(self):
        self.assertIsNone(live_video(PAGE.replace('"playableInEmbed":true', '"playableInEmbed":false')))
        self.assertIsNone(live_video(PAGE.replace('"status":"OK"', '"status":"ERROR"')))

    def test_requires_the_canonical_link(self):
        self.assertIsNone(live_video(PAGE.replace('<link rel="canonical"', '<link rel="alternate"')))

    def test_rejects_an_invalid_url_before_any_request(self):
        with self.assertRaises(ValueError):
            YouTubeLive(Net(allow_private=True)).resolve("https://www.youtube.com/watch?v=WUTDiO_fXdE")


class DirectLiveResolveTests(unittest.TestCase):
    def test_follows_the_channel_when_the_fixed_video_is_dead(self):
        live = YouTubeLive(Net(allow_private=True))
        with mock.patch.object(live, "_read", side_effect=[DEAD_WATCH, ABC_LIVE]) as read:
            video = live.resolve("https://www.youtube.com/live/iipR5yUp36o")
        self.assertEqual(video, "KQhNZQ_Uag8")
        self.assertEqual(
            read.call_args_list,
            [
                mock.call("https://www.youtube.com/watch?v=iipR5yUp36o&hl=en"),
                mock.call("https://www.youtube.com/channel/UCBi2mrWuNuyYy4gbM6fU18Q/live?hl=en"),
            ],
        )

    def test_prefers_the_channel_live_over_an_old_still_live_video(self):
        live = YouTubeLive(Net(allow_private=True))
        with mock.patch.object(live, "_read", side_effect=[AMERICA_WATCH, AMERICA_LIVE]):
            video = live.resolve("https://www.youtube.com/live/zcWXboTnous")
        self.assertEqual(video, "BOtvAr8DPcg")

    def test_keeps_the_video_when_the_channel_has_no_live(self):
        live = YouTubeLive(Net(allow_private=True))
        with mock.patch.object(live, "_read", side_effect=[AMERICA_WATCH, NO_LIVE_CHANNEL]):
            video = live.resolve("https://www.youtube.com/live/zcWXboTnous")
        self.assertEqual(video, "zcWXboTnous")

    def test_reports_a_dead_video_without_a_channel_live(self):
        live = YouTubeLive(Net(allow_private=True))
        patch = mock.patch.object(live, "_read", side_effect=[DEAD_WATCH, NO_LIVE_CHANNEL])
        with patch, self.assertRaises(ValueError):
            live.resolve("https://www.youtube.com/live/iipR5yUp36o")

    def test_caches_the_resolved_direct_live(self):
        live = YouTubeLive(Net(allow_private=True))
        url = "https://www.youtube.com/live/zcWXboTnous"
        with mock.patch.object(live, "_read", side_effect=[AMERICA_WATCH, AMERICA_LIVE]) as read:
            self.assertEqual(live.resolve(url), "BOtvAr8DPcg")
            self.assertEqual(live.resolve(url), "BOtvAr8DPcg")
        self.assertEqual(read.call_count, 2)
