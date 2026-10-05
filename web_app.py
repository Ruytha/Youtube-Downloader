"""YouTube downloader with an HTML/CSS/JS interface.

Runs a small local web server (127.0.0.1 only) and shows the UI in its own
window (pywebview) or, as a fallback, in your browser.

    python web_app.py            # app window (falls back to browser)
    python web_app.py --browser  # always use the browser
"""

import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    import yt_dlp
    from yt_dlp.postprocessor.metadataparser import MetadataParserPP
    from yt_dlp.utils import DownloadCancelled, download_range_func
except ImportError:
    raise SystemExit("yt-dlp is not installed. Run:  pip install -r requirements.txt") from None

APP_NAME = "YT Downloader"
APP_VERSION = "1.0.0"
HOST, PORT = "127.0.0.1", 8765
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.dirname(sys.executable) if FROZEN else BASE_DIR
WEB_DIR = os.path.join(BASE_DIR, "web")
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "YTDownloader")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
MAX_PARALLEL = 3
HISTORY_LIMIT = 500

# Use an ffmpeg.exe shipped next to the app if there is one, else whatever is on PATH.
_local_ffmpeg = os.path.join(APP_DIR, "ffmpeg.exe")
FFMPEG_LOCATION = _local_ffmpeg if os.path.isfile(_local_ffmpeg) else None

VIDEO_CONTAINERS = {"mp4", "webm", "mkv"}
AUDIO_CONTAINERS = {"mp3", "m4a", "opus", "flac", "wav"}
THUMBNAIL_AUDIO = {"mp3", "m4a", "opus", "flac"}

jobs = {}        # id -> public job dict (sent to the UI)
job_meta = {}    # id -> {"req", "cancel": Event, "temp": set}
jobs_lock = threading.Lock()
slots = threading.Semaphore(MAX_PARALLEL)
history_lock = threading.Lock()
notify_batch = {"done": 0, "failed": 0, "enabled": False}
main_window = None  # pywebview window, when running in app mode


# ---------------------------------------------------------------- helpers

def clean_error(e):
    return re.sub(r"^ERROR:\s*(\[\w+\]\s*\S+:\s*)?", "", str(e)).strip()


def parse_time(text):
    """'1:23:45', '2:30' or '90' -> seconds. Empty -> None."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError(f"Invalid time '{text}'. Use e.g. 1:30 or 0:01:30.")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_history(items):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items[-HISTORY_LIMIT:], f, ensure_ascii=False, indent=1)
    os.replace(tmp, HISTORY_FILE)


def add_history(entry):
    with history_lock:
        items = load_history()
        items.append(entry)
        save_history(items)


def notify(title, message):
    """Windows toast notification via PowerShell (no extra packages needed)."""
    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                 .replace('"', "&quot;").replace("'", "''"))
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>{esc(title)}</text><text>{esc(message)}</text></binding></visual></toast>')
$app = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        pass


_latest_cache = {"time": 0, "version": None}


def latest_ytdlp_version():
    if time.time() - _latest_cache["time"] < 3600:
        return _latest_cache["version"]
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=6) as r:
            _latest_cache["version"] = json.load(r)["info"]["version"]
    except Exception:
        _latest_cache["version"] = None
    _latest_cache["time"] = time.time()
    return _latest_cache["version"]


def normalize_version(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


# ---------------------------------------------------------------- downloads

def build_options(job_id, req):
    job, meta = jobs[job_id], job_meta[job_id]
    kind = req.get("kind", "video")
    container = req.get("container") or ("mp4" if kind == "video" else "mp3")
    outdir = req.get("outdir") or DEFAULT_DIR

    def check_cancel():
        if meta["cancel"].is_set():
            raise DownloadCancelled("Cancelled by user")

    def progress_hook(d):
        check_cancel()
        info = d.get("info_dict") or {}
        for key in ("filename", "tmpfilename"):
            if d.get(key):
                meta["temp"].add(d[key])
        with jobs_lock:
            if job["title"] == job["url"] and info.get("title"):
                job["title"] = info["title"]
            if not job["thumbnail"] and info.get("thumbnail"):
                job["thumbnail"] = info["thumbnail"]
            idx, total_items = info.get("playlist_index"), info.get("n_entries")
            job["item"] = f"{idx} of {total_items}" if idx and total_items else ""
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes", 0)
                job.update(state="downloading",
                           progress=round(done / total * 100, 1) if total else job["progress"],
                           speed=(d.get("_speed_str") or "").strip(),
                           eta=(d.get("_eta_str") or "").strip())
            elif d["status"] == "finished":
                job.update(state="processing", progress=100, speed="", eta="")

    def pp_hook(d):
        check_cancel()
        if d["status"] == "started":
            with jobs_lock:
                job.update(state="processing", step=d.get("postprocessor", ""))

    def post_hook(path):
        with jobs_lock:
            job["files"].append(os.path.abspath(path))

    name = "%(orig_title,title)s"
    clip = req.get("clip") or {}
    start, end = parse_time(clip.get("start")), parse_time(clip.get("end"))
    if start is not None or end is not None:
        start = start or 0
        if end is not None and end <= start:
            raise ValueError("Clip end must be after the start.")
        name += " (clip %(section_start)d-%(section_end)ds)" if end else " (from %(section_start)ds)"

    opts = {
        "outtmpl": {"default": os.path.join(outdir, name + ".%(ext)s")},
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [pp_hook],
        "post_hooks": [post_hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "windowsfilenames": True,
        "noplaylist": bool(req.get("single")),
    }
    if FFMPEG_LOCATION:
        opts["ffmpeg_location"] = FFMPEG_LOCATION
    if start is not None:
        opts["download_ranges"] = download_range_func(None, [(start, end or float("inf"))])
        opts["force_keyframes_at_cuts"] = True

    pps = []
    if kind == "audio":
        if container not in AUDIO_CONTAINERS:
            raise ValueError(f"Unsupported audio format: {container}")
        opts["format"] = "bestaudio/best"
        if req.get("smart_tags"):
            interpret, replace = MetadataParserPP.Actions.INTERPRET, MetadataParserPP.Actions.REPLACE
            junk = (r"(?i)\s*[\(\[][^\)\]]*\b(official|lyrics?|audio|video|visuali[sz]er|"
                    r"hd|hq|4k|remaster(ed)?|mv)\b[^\)\]]*[\)\]]")
            pps.append({"key": "MetadataParser", "when": "pre_process", "actions": [
                (interpret, "title", "(?P<orig_title>.+)"),        # keep original for the filename
                (replace, "title", junk, ""),
                (interpret, "title", r"^(?P<artist>.+?)\s+[-–—|]\s+(?P<title>.+)$"),
                (replace, "title", r"(?i)\s*\b(ft\.?|feat\.?)\s.*$", ""),
            ]})
        pps.append({"key": "FFmpegExtractAudio", "preferredcodec": container,
                    "preferredquality": str(req.get("bitrate") or "192")})
        pps.append({"key": "FFmpegMetadata", "add_metadata": True})
        if container in THUMBNAIL_AUDIO:
            opts["writethumbnail"] = True
            pps.append({"key": "EmbedThumbnail"})
    else:
        if container not in VIDEO_CONTAINERS:
            raise ValueError(f"Unsupported video format: {container}")
        q = str(req.get("quality") or "best")
        lim = "" if q == "best" else f"[height<={int(q)}]"
        if container == "mp4":
            opts["format"] = f"bv{lim}[ext=mp4]+ba[ext=m4a]/bv{lim}+ba/b{lim}/b"
            opts["merge_output_format"] = "mp4"
        elif container == "webm":
            opts["format"] = f"bv{lim}[ext=webm]+ba[ext=webm]/bv{lim}+ba/b{lim}/b"
            opts["merge_output_format"] = "webm/mkv"
        else:
            opts["format"] = f"bv{lim}+ba/b{lim}/b"
            opts["merge_output_format"] = "mkv"

        subs = req.get("subtitles", "off")
        if subs in ("embed", "files"):
            langs = [lang.strip() for lang in (req.get("sub_langs") or "en").split(",") if lang.strip()]
            opts.update(writesubtitles=True, writeautomaticsub=True,
                        subtitleslangs=langs + ["-live_chat"])
            if subs == "embed":
                pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
            else:
                pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
        pps.append({"key": "FFmpegMetadata", "add_metadata": True})

    opts["postprocessors"] = pps
    return opts


def cleanup_partials(job_id):
    """Delete leftovers of a cancelled download, keeping files that finished."""
    finished = set(jobs[job_id]["files"])
    for path in job_meta[job_id]["temp"]:
        if os.path.abspath(path) in finished:
            continue
        for f in [path] + glob.glob(glob.escape(path) + ".*"):
            if f.endswith((".part", ".ytdl")) or ".part-Frag" in f or f == path:
                try:
                    os.remove(f)
                except OSError:
                    pass


def run_job(job_id):
    job, meta = jobs[job_id], job_meta[job_id]
    req = meta["req"]

    while not slots.acquire(timeout=0.3):      # wait for a free download slot
        if meta["cancel"].is_set():
            break
    else:
        try:
            if meta["cancel"].is_set():
                raise DownloadCancelled("Cancelled by user")
            os.makedirs(req.get("outdir") or DEFAULT_DIR, exist_ok=True)
            opts = build_options(job_id, req)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([req["url"]])
            with jobs_lock:
                job.update(state="done", progress=100, speed="", eta="", step="")
            add_history({
                "id": job_id, "title": job["title"], "url": req["url"],
                "thumbnail": job["thumbnail"], "kind": req.get("kind", "video"),
                "container": job["container"], "files": job["files"],
                "outdir": req.get("outdir") or DEFAULT_DIR,
                "date": datetime.now().isoformat(timespec="seconds"),
            })
        except DownloadCancelled:
            pass
        except Exception as e:
            if not meta["cancel"].is_set():
                with jobs_lock:
                    job.update(state="error", error=clean_error(e), speed="", eta="")
        finally:
            slots.release()

    if meta["cancel"].is_set() and job["state"] != "done":
        with jobs_lock:
            job.update(state="cancelled", speed="", eta="", step="")
        cleanup_partials(job_id)
    finish_batch(job["state"])


def finish_batch(state):
    """Send one notification when the whole queue becomes idle."""
    with jobs_lock:
        if state == "done":
            notify_batch["done"] += 1
        elif state == "error":
            notify_batch["failed"] += 1
        busy = any(j["state"] in ("queued", "downloading", "processing") for j in jobs.values())
        if busy or not (notify_batch["done"] or notify_batch["failed"]):
            return
        done, failed, enabled = notify_batch["done"], notify_batch["failed"], notify_batch["enabled"]
        notify_batch.update(done=0, failed=0)
    if enabled:
        msg = f"{done} download{'s' if done != 1 else ''} finished"
        if failed:
            msg += f", {failed} failed"
        notify(APP_NAME, msg + ".")


def start_job(req):
    job_id = uuid.uuid4().hex[:10]
    kind = req.get("kind", "video")
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id, "url": req["url"], "title": req.get("title") or req["url"],
            "thumbnail": req.get("thumbnail") or "", "kind": kind,
            "container": req.get("container") or ("mp4" if kind == "video" else "mp3"),
            "outdir": req.get("outdir") or DEFAULT_DIR, "state": "queued",
            "progress": 0, "speed": "", "eta": "", "step": "", "item": "",
            "error": "", "files": [], "created": time.time(),
        }
        job_meta[job_id] = {"req": req, "cancel": threading.Event(), "temp": set()}
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return job_id


def fetch_info(url):
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                           "extract_flat": "in_playlist", "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = []
        for e in info.get("entries") or []:
            if not e:
                continue
            vid = e.get("id")
            thumbs = e.get("thumbnails") or []
            entries.append({
                "id": vid, "title": e.get("title") or vid,
                "duration": e.get("duration"),
                "url": e.get("url") if (e.get("url") or "").startswith("http")
                       else f"https://www.youtube.com/watch?v={vid}",
                "thumbnail": thumbs[-1]["url"] if thumbs else
                             (f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else ""),
            })
        thumbs = info.get("thumbnails") or []
        return {"playlist": True, "title": info.get("title"),
                "uploader": info.get("uploader") or info.get("channel"),
                "thumbnail": thumbs[-1]["url"] if thumbs else
                             (entries[0]["thumbnail"] if entries else ""),
                "entries": entries}
    return {"playlist": False, "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("channel"),
            "thumbnail": info.get("thumbnail"), "duration": info.get("duration"),
            "has_subs": bool(info.get("subtitles")),
            "sub_langs": sorted((info.get("subtitles") or {}).keys())[:40]}


def pick_folder(initial):
    initial = initial if initial and os.path.isdir(initial) else DEFAULT_DIR
    if main_window is not None:
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
        result = main_window.create_file_dialog(kind, directory=initial)
        return result[0] if result else ""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(initialdir=initial)
    root.destroy()
    return os.path.normpath(folder) if folder else ""


def update_ytdlp():
    if FROZEN:
        raise RuntimeError("This is the packaged .exe. Rebuild it with build.bat to update yt-dlp.")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                       capture_output=True, text=True, timeout=300,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1])
    out = subprocess.run([sys.executable, "-c", "import yt_dlp;print(yt_dlp.version.__version__)"],
                         capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out.stdout.strip()


# ---------------------------------------------------------------- HTTP API

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, *_):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _same_origin(self):
        # Block other websites from driving this local API.
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/jobs":
            with jobs_lock:
                return self._json(sorted(jobs.values(), key=lambda j: j["created"]))
        if path == "/api/history":
            with history_lock:
                items = load_history()
            for h in items:
                h["exists"] = any(os.path.exists(f) for f in h.get("files", []))
            return self._json(list(reversed(items)))
        if path == "/api/config":
            return self._json({"outdir": DEFAULT_DIR, "frozen": FROZEN, "version": APP_VERSION,
                               "window": main_window is not None})
        if path == "/api/version":
            current = yt_dlp.version.__version__
            latest = latest_ytdlp_version()
            outdated = bool(latest) and normalize_version(latest) > normalize_version(current)
            return self._json({"current": current, "latest": latest,
                               "outdated": outdated, "frozen": FROZEN})
        return super().do_GET()

    def do_POST(self):
        if not self._same_origin() or self.headers.get("Content-Type") != "application/json":
            return self._json({"error": "forbidden"}, 403)
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        route = getattr(self, "api_" + urlparse(self.path).path.removeprefix("/api/").replace("/", "_"), None)
        if not route:
            return self._json({"error": "not found"}, 404)
        try:
            return self._json(route(data))
        except Exception as e:
            return self._json({"error": clean_error(e)}, 400)

    # --- routes (POST /api/<name>) ---

    def api_info(self, data):
        return fetch_info(data["url"])

    def api_download(self, data):
        opts = data.get("options") or {}
        items = data.get("items") or []
        if not items:
            raise ValueError("Nothing to download.")
        clip = opts.get("clip") or {}
        parse_time(clip.get("start")), parse_time(clip.get("end"))   # validate early
        notify_batch["enabled"] = bool(opts.get("notify"))
        ids = [start_job({**opts, **{k: v for k, v in item.items() if v is not None}})
               for item in items if item.get("url")]
        return {"ids": ids}

    def api_cancel(self, data):
        ids = data.get("ids") or ([data["id"]] if data.get("id") else list(job_meta))
        for job_id in ids:
            if job_id in job_meta and jobs[job_id]["state"] in ("queued", "downloading", "processing"):
                job_meta[job_id]["cancel"].set()
        return {"ok": True}

    def api_retry(self, data):
        old = job_meta.get(data.get("id"))
        if not old:
            raise ValueError("Unknown download.")
        with jobs_lock:
            jobs.pop(data["id"], None)
            job_meta.pop(data["id"], None)
        return {"id": start_job(old["req"])}

    def api_clear(self, data):
        with jobs_lock:
            for k in [k for k, j in jobs.items() if j["state"] in ("done", "error", "cancelled")]:
                jobs.pop(k)
                job_meta.pop(k)
        return {"ok": True}

    def api_browse(self, data):
        return {"outdir": pick_folder(data.get("outdir"))}

    def api_open(self, data):
        folder = data.get("outdir") or DEFAULT_DIR
        if not os.path.isdir(folder):
            raise ValueError("That folder doesn't exist yet.")
        os.startfile(folder)
        return {"ok": True}

    def api_reveal(self, data):
        # Only reveal files this app downloaded (listed in history or current jobs).
        path = data.get("path", "")
        with history_lock:
            known = {f for h in load_history() for f in h.get("files", [])}
        with jobs_lock:
            known |= {f for j in jobs.values() for f in j["files"]}
        if path not in known:
            raise ValueError("Unknown file.")
        if not os.path.exists(path):
            raise ValueError("The file was moved or deleted.")
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        return {"ok": True}

    def api_history_remove(self, data):
        with history_lock:
            save_history([h for h in load_history() if h["id"] != data.get("id")])
        return {"ok": True}

    def api_history_clear(self, data):
        with history_lock:
            save_history([])
        return {"ok": True}

    def api_update(self, data):
        return {"version": update_ytdlp()}


# ---------------------------------------------------------------- startup

def main():
    global main_window
    url = f"http://{HOST}:{PORT}/"
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        # Already running — just show the existing instance.
        webbrowser.open(url)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()

    if "--server" in sys.argv:       # headless: serve only, open nothing
        print(f"{APP_NAME} serving at {url}")
        server_thread_wait()
        return

    if "--browser" not in sys.argv:
        try:
            import webview
            main_window = webview.create_window(APP_NAME, url, width=900, height=940,
                                                min_size=(440, 600), background_color="#0f1115")
            webview.start()
            return
        except Exception as e:
            main_window = None
            print(f"App window unavailable ({e}); opening in browser instead.")

    print(f"{APP_NAME} running at {url}  (Ctrl+C to quit)")
    webbrowser.open(url)
    server_thread_wait()


def server_thread_wait():
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
