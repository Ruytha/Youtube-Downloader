"""Offline tests: nothing here touches the network or downloads anything."""

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
import yt_dlp
from yt_dlp.postprocessor.metadataparser import MetadataParserPP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import web_app as app  # noqa: E402


def make_job(req):
    job_id = "test"
    app.jobs[job_id] = {"title": "x", "url": "x", "thumbnail": "", "files": [], "progress": 0}
    app.job_meta[job_id] = {"req": req, "cancel": threading.Event(), "temp": set()}
    return job_id


def options(**req):
    return app.build_options(make_job(req), req)


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("text, seconds", [
    ("", None), ("  ", None), ("90", 90), ("1:30", 90), ("1:02:03", 3723), ("0:00.5", 0.5),
])
def test_parse_time_valid(text, seconds):
    assert app.parse_time(text) == seconds


@pytest.mark.parametrize("text", ["1:xx", "a", "1:2:3:4", "-5", "1::2"])
def test_parse_time_invalid(text):
    with pytest.raises(ValueError):
        app.parse_time(text)


def test_clean_error_strips_prefixes():
    assert app.clean_error("ERROR: [youtube] abc123: Video unavailable") == "Video unavailable"
    assert app.clean_error("plain message") == "plain message"


def test_normalize_version_orders_correctly():
    assert app.normalize_version("2026.10.01") > app.normalize_version("2026.08.19")
    assert app.normalize_version("2026.8.19") == app.normalize_version("2026.08.19")


# ---------------------------------------------------------------- options

@pytest.mark.parametrize("container", sorted(app.VIDEO_CONTAINERS))
def test_video_options_are_accepted_by_ytdlp(container):
    opts = options(kind="video", container=container, quality="720", subtitles="embed",
                   clip={"start": "0:05", "end": "1:00"})
    assert "[height<=720]" in opts["format"]
    assert opts["writesubtitles"] and "download_ranges" in opts
    yt_dlp.YoutubeDL(opts).close()


@pytest.mark.parametrize("container", sorted(app.AUDIO_CONTAINERS))
def test_audio_options_are_accepted_by_ytdlp(container):
    opts = options(kind="audio", container=container, bitrate="256", smart_tags=True)
    extract = next(p for p in opts["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert extract["preferredcodec"] == container
    assert opts.get("writethumbnail", False) == (container in app.THUMBNAIL_AUDIO)
    yt_dlp.YoutubeDL(opts).close()


def test_best_quality_has_no_height_limit():
    assert "height" not in options(kind="video", container="mp4", quality="best")["format"]


def test_unknown_container_is_rejected():
    with pytest.raises(ValueError):
        options(kind="video", container="avi")
    with pytest.raises(ValueError):
        options(kind="audio", container="mp4")


def test_clip_end_before_start_is_rejected():
    with pytest.raises(ValueError):
        options(kind="video", container="mp4", clip={"start": "1:00", "end": "0:30"})


def test_single_flag_disables_playlist():
    assert options(kind="video", container="mp4", single=True)["noplaylist"] is True
    assert options(kind="video", container="mp4")["noplaylist"] is False


# ---------------------------------------------------------------- music tags

@pytest.mark.parametrize("title, artist, clean", [
    ("Daft Punk - Get Lucky (Official Video) ft. Pharrell", "Daft Punk", "Get Lucky"),
    ("Some Artist \u2013 Song Name [Lyrics]", "Some Artist", "Song Name"),
    ("Band | Track (Official Audio)", "Band", "Track"),
    ("Just a vlog title (HD)", None, "Just a vlog title"),
])
def test_smart_tags(title, artist, clean):
    opts = options(kind="audio", container="mp3", smart_tags=True)
    parser = next(p for p in opts["postprocessors"] if p["key"] == "MetadataParser")
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        pp = MetadataParserPP(ydl, parser["actions"])
        info = {"title": title}
        pp.run(info)
    assert info.get("artist") == artist
    assert info["title"] == clean
    assert info["orig_title"] == title   # filename keeps the original title


# ---------------------------------------------------------------- history

def test_history_roundtrip_and_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app, "HISTORY_FILE", str(tmp_path / "history.json"))
    monkeypatch.setattr(app, "HISTORY_LIMIT", 3)
    assert app.load_history() == []
    for i in range(5):
        app.add_history({"id": str(i), "files": []})
    assert [h["id"] for h in app.load_history()] == ["2", "3", "4"]


def test_corrupt_history_reads_as_empty(tmp_path, monkeypatch):
    bad = tmp_path / "history.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(app, "HISTORY_FILE", str(bad))
    assert app.load_history() == []


# ---------------------------------------------------------------- HTTP API

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app, "HISTORY_FILE", str(tmp_path / "history.json"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    monkeypatch.setattr(app, "PORT", srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def request(url, data=None, headers=None):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


JSON = {"Content-Type": "application/json"}


def test_page_and_config_are_served(server):
    with urllib.request.urlopen(server + "/", timeout=5) as r:
        assert r.status == 200 and b"YT Downloader" in r.read()
    status, cfg = request(server + "/api/config")
    assert status == 200 and cfg["version"] == app.APP_VERSION


def test_post_without_json_content_type_is_blocked(server):
    status, _ = request(server + "/api/clear", {}, {"Content-Type": "text/plain"})
    assert status == 403


def test_post_from_another_website_is_blocked(server):
    status, _ = request(server + "/api/clear", {}, {**JSON, "Origin": "https://evil.example"})
    assert status == 403


def test_reveal_refuses_files_the_app_did_not_download(server):
    status, body = request(server + "/api/reveal", {"path": r"C:\Windows\System32\cmd.exe"}, JSON)
    assert status == 400 and body["error"] == "Unknown file."


def test_download_requires_items(server):
    status, body = request(server + "/api/download", {"items": [], "options": {}}, JSON)
    assert status == 400 and "Nothing to download" in body["error"]


def test_bad_clip_time_is_rejected_before_queueing(server):
    status, _ = request(server + "/api/download", {
        "items": [{"url": "https://example.com/v"}],
        "options": {"clip": {"start": "nope"}},
    }, JSON)
    assert status == 400


def test_unknown_route_is_404(server):
    status, _ = request(server + "/api/does-not-exist", {}, JSON)
    assert status == 404
