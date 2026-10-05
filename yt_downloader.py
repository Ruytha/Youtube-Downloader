"""Simple YouTube downloader (MP4 / MP3) with a Tkinter GUI, powered by yt-dlp."""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import yt_dlp
except ImportError:
    raise SystemExit("yt-dlp is not installed. Run:  pip install -r requirements.txt") from None

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
QUALITIES = ["Best", "1080p", "720p", "480p", "360p"]
BITRATES = ["320", "256", "192", "128"]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Downloader")
        self.geometry("560x330")
        self.resizable(False, False)

        self.url = tk.StringVar()
        self.fmt = tk.StringVar(value="mp4")
        self.quality = tk.StringVar(value="Best")
        self.bitrate = tk.StringVar(value="192")
        self.outdir = tk.StringVar(value=DEFAULT_DIR)
        self.status = tk.StringVar(value="Paste a YouTube link and press Download.")

        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, **pad)

        ttk.Label(frm, text="Video / playlist URL:").grid(row=0, column=0, sticky="w")
        url_entry = ttk.Entry(frm, textvariable=self.url, width=60)
        url_entry.grid(row=1, column=0, columnspan=3, sticky="we", pady=(0, 8))
        url_entry.focus()

        ttk.Label(frm, text="Format:").grid(row=2, column=0, sticky="w")
        fmt_box = ttk.Frame(frm)
        fmt_box.grid(row=2, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(fmt_box, text="MP4 (video)", variable=self.fmt, value="mp4",
                        command=self._toggle).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(fmt_box, text="MP3 (audio)", variable=self.fmt, value="mp3",
                        command=self._toggle).pack(side="left")

        ttk.Label(frm, text="Video quality:").grid(row=3, column=0, sticky="w", pady=4)
        self.q_combo = ttk.Combobox(frm, textvariable=self.quality, values=QUALITIES,
                                    state="readonly", width=10)
        self.q_combo.grid(row=3, column=1, sticky="w")

        ttk.Label(frm, text="MP3 bitrate (kbps):").grid(row=4, column=0, sticky="w", pady=4)
        self.b_combo = ttk.Combobox(frm, textvariable=self.bitrate, values=BITRATES,
                                    state="disabled", width=10)
        self.b_combo.grid(row=4, column=1, sticky="w")

        ttk.Label(frm, text="Save to:").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.outdir, width=45).grid(row=5, column=1, sticky="we")
        ttk.Button(frm, text="Browse…", command=self._browse).grid(row=5, column=2, padx=(6, 0))

        self.dl_btn = ttk.Button(frm, text="Download", command=self._start)
        self.dl_btn.grid(row=6, column=0, columnspan=3, pady=(12, 6), sticky="we")

        self.progress = ttk.Progressbar(frm, maximum=100)
        self.progress.grid(row=7, column=0, columnspan=3, sticky="we")
        ttk.Label(frm, textvariable=self.status, wraplength=520).grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(6, 0))

        frm.columnconfigure(1, weight=1)
        self.bind("<Return>", lambda _e: self._start())

    def _toggle(self):
        audio = self.fmt.get() == "mp3"
        self.q_combo.config(state="disabled" if audio else "readonly")
        self.b_combo.config(state="readonly" if audio else "disabled")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.outdir.get())
        if d:
            self.outdir.set(d)

    def _set_status(self, text, pct=None):
        def apply():
            self.status.set(text)
            if pct is not None:
                self.progress["value"] = pct
        self.after(0, apply)

    def _start(self):
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please paste a YouTube link first.")
            return
        os.makedirs(self.outdir.get(), exist_ok=True)
        self.dl_btn.config(state="disabled")
        self.progress["value"] = 0
        threading.Thread(target=self._download, args=(url,), daemon=True).start()

    def _hook(self, d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes", 0)
            pct = done / total * 100 if total else 0
            name = os.path.basename(d.get("filename", ""))
            speed = d.get("_speed_str", "").strip()
            self._set_status(f"Downloading {name}  {pct:.1f}%  {speed}", pct)
        elif d["status"] == "finished":
            self._set_status("Processing (merging / converting)…", 100)

    def _options(self):
        opts = {
            "outtmpl": os.path.join(self.outdir.get(), "%(title)s.%(ext)s"),
            "progress_hooks": [self._hook],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": False,
        }
        if self.fmt.get() == "mp3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                 "preferredquality": self.bitrate.get()},
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ]
            opts["writethumbnail"] = True
        else:
            q = self.quality.get()
            limit = "" if q == "Best" else f"[height<={q[:-1]}]"
            opts["format"] = (f"bestvideo{limit}[ext=mp4]+bestaudio[ext=m4a]/"
                              f"bestvideo{limit}+bestaudio/best{limit}/best")
            opts["merge_output_format"] = "mp4"
        return opts

    def _download(self, url):
        try:
            with yt_dlp.YoutubeDL(self._options()) as ydl:
                ydl.download([url])
            self._set_status(f"Done! Saved to {self.outdir.get()}", 100)
        except Exception as e:
            self._set_status(f"Error: {e}", 0)
        finally:
            self.after(0, lambda: self.dl_btn.config(state="normal"))


if __name__ == "__main__":
    App().mainloop()
