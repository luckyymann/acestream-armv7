#-plugin-sig:bzMtTLk6Xa53TuHJXlc7Z89K8u1JPjauRCUAr1AyFs2OPwTiEJNPVcYzg1HohfttCrekf1uJ23/Uqmo/NFJEy2SJ7KZptY0sEchzfeGuYxGJVfYEStjT3XsjTZJIi7L9i4E68JBQrNPLJ8oBsNPOrcLYBGxVqW2osYr33lRQI0vlKlJ1b6v+wbf3vFYuum/ZENdAzOv+R1CGbzj5qD+Lb3Ijy5T3mFecFWGYNUhNrsOHi4lJkuDOjWNrcucHwhOVxyQpRnQ0p/UTCeebapDPkM2FMR7i3HXVvQpB+sy9hwnTmbWbFhQUoK46Qoz4F25IhvbcNUwJxM4mq643d0wRsg==
import re

from ACEStream.PluginsContainer.livestreamer.compat import urlparse, parse_qsl
from ACEStream.PluginsContainer.livestreamer.plugin import Plugin, PluginError, AccessDeniedError
from ACEStream.PluginsContainer.livestreamer.plugin.api import http, validate
from ACEStream.PluginsContainer.livestreamer.plugin.api.utils import parse_query
from ACEStream.PluginsContainer.livestreamer.stream import HTTPStream, HLSStream

API_KEY = "AIzaSyBDBi-4roGzWJN4du9TuDMLd_jVTcVkKz4"
API_BASE = "https://www.googleapis.com/youtube/v3"
API_SEARCH_URL = API_BASE + "/search"
API_VIDEO_INFO = "http://youtube.com/get_video_info"
HLS_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def parse_stream_map(stream_map):
    if not stream_map:
        return []

    return [parse_query(s) for s in stream_map.split(",")]


def parse_fmt_list(formatsmap):
    formats = {}
    if not formatsmap:
        return formats

    for format in formatsmap.split(","):
        s = format.split("/")
        (w, h) = s[1].split("x")
        formats[int(s[0])] = "{0}p".format(h)

    return formats


_config_schema = validate.Schema(
    {
        validate.optional("fmt_list"): validate.all(
            validate.text,
            validate.transform(parse_fmt_list)
        ),
        validate.optional("url_encoded_fmt_stream_map"): validate.all(
            validate.text,
            validate.transform(parse_stream_map),
            [{
                "itag": validate.all(
                    validate.text,
                    validate.transform(int)
                ),
                "quality": validate.text,
                "url": validate.url(scheme="http"),
                validate.optional("s"): validate.text,
                validate.optional("stereo3d"): validate.all(
                    validate.text,
                    validate.transform(int),
                    validate.transform(bool)
                ),
            }]
        ),
        validate.optional("adaptive_fmts"): validate.all(
            validate.text,
            validate.transform(parse_stream_map),
            [{
                validate.optional("s"): validate.text,
                "type": validate.all(
                    validate.text,
                    validate.transform(lambda t: t.split(";")[0].split("/")),
                    [validate.text, validate.text]
                ),
                "url": validate.all(
                    validate.url(scheme="http")
                )
            }]
        ),
        validate.optional("hlsvp"): validate.text,
        validate.optional("live_playback"): validate.transform(bool),
        "status": validate.text
    }
)
_search_schema = validate.Schema(
    {
        "items": [{
            "id": {
                "videoId": validate.text
            }
        }]
    },
    validate.get("items")
)

#TODO: check "gaming.youtube.com"

_channelid_re = re.compile('meta itemprop="channelId" content="([^"]+)"')
_livechannelid_re = re.compile('meta property="og:video:url" content="([^"]+)')
_url_re = re.compile("""
    http(s)?://
    (?:
        (?:
            (\w+\.)?youtube\\.com
            (?:
                (?:
                    /(watch.+v=|embed/|v/)
                    (?P<video_id>[0-9A-z_-]{11})
                )
                |
                (?:
                    /(user|channel)/(?P<user>[^/?]+)
                )
                |
                (?:
                    /c/(?P<liveChannel>[^/?]+)/live
                )
            )
        )
        |
        (?:
            (\w+\.)?youtu\\.be/(?P<video_id2>[0-9A-z_-]{11})
        )
    )
""", re.VERBOSE)


class YouTube(Plugin):
    @classmethod
    def can_handle_url(self, url):
        return _url_re.match(url)

    @classmethod
    def stream_weight(cls, stream):
        match = re.match("(\w+)_3d", stream)
        if match:
            weight, group = Plugin.stream_weight(match.group(1))
            weight -= 1
            group = "youtube_3d"
        else:
            weight, group = Plugin.stream_weight(stream)

        return weight, group

    def _find_channel_video(self):
        res = http.get(self.url)
        match = _channelid_re.search(res.text)
        if not match:
            return

        return self._get_channel_video(match.group(1))

    def _get_channel_video(self, channel_id):
        query = {
            "channelId": channel_id,
            "type": "video",
            "eventType": "live",
            "part": "id",
            "key": API_KEY
        }
        res = http.get(API_SEARCH_URL, params=query)
        videos = http.json(res, schema=_search_schema)

        for video in videos:
            video_id = video["id"]["videoId"]
            return video_id

    def _find_canonical_stream_info(self):
        res = http.get(self.url)
        match = _livechannelid_re.search(res.text)
        if not match:
            return

        return self._get_stream_info(match.group(1))

    def _get_stream_info(self, url):
        match = _url_re.match(url)
        user = match.group("user")
        live_channel = match.group("liveChannel")

        if user:
            video_id = self._find_channel_video()
        elif live_channel:
            return self._find_canonical_stream_info()
        else:
            video_id = match.group("video_id")
            video_id2 = match.group("video_id2")
            if video_id2:
                video_id = video_id2

            if video_id == "live_stream":
                query_info = dict(parse_qsl(urlparse(url).query))
                if "channel" in query_info:
                    video_id = self._get_channel_video(query_info["channel"])

        if not video_id:
            return

        params = {
            "video_id": video_id,
            "el": "player_embedded"
        }
        res = http.get(API_VIDEO_INFO, params=params, headers=HLS_HEADERS)
        return parse_query(res.text, name="config", schema=_config_schema)

    def _get_streams(self):
        info = self._get_stream_info(self.url)
        if not info:
            return

        formats = info.get("fmt_list")
        streams = {}
        protected = False
        for stream_info in info.get("url_encoded_fmt_stream_map", []):
            if stream_info.get("s"):
                protected = True
                continue

            stream = HTTPStream(self.session, stream_info["url"])
            name = formats.get(stream_info["itag"]) or stream_info["quality"]

            if stream_info.get("stereo3d"):
                name += "_3d"

            streams[name] = stream

        # Extract audio streams from the DASH format list
        for stream_info in info.get("adaptive_fmts", []):
            if stream_info.get("s"):
                protected = True
                continue

            stream_type, stream_format = stream_info["type"]
            if stream_type != "audio":
                continue

            stream = HTTPStream(self.session, stream_info["url"])
            name = "audio_{0}".format(stream_format)

            streams[name] = stream

        hls_playlist = info.get("hlsvp")
        if hls_playlist:
            try:
                hls_streams = HLSStream.parse_variant_playlist(
                    self.session, hls_playlist, headers=HLS_HEADERS, namekey="pixels"
                )
                streams.update(hls_streams)
            except IOError as err:
                self.logger.warning("Failed to extract HLS streams: {0}", err)

        if not streams and protected:
            raise AccessDeniedError("This plugin does not support protected videos, "
                              "try youtube-dl instead")

        return streams

__plugin__ = YouTube
