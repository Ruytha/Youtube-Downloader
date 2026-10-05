const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const els = {
  form: $("#form"), url: $("#url"), paste: $("#paste"),
  skeleton: $("#skeleton"),
  preview: $("#preview"), thumb: $("#thumb"), pTitle: $("#p-title"), pSub: $("#p-sub"),
  picker: $("#picker"), pkTitle: $("#pk-title"), pkSub: $("#pk-sub"), pkList: $("#pk-list"),
  pkFilter: $("#pk-filter"), pkAll: $("#pk-all"), pkNone: $("#pk-none"),
  previewMsg: $("#preview-msg"),
  container: $("#container"), quality: $("#quality"), bitrate: $("#bitrate"),
  qualityField: $("#quality-field"), bitrateField: $("#bitrate-field"),
  outdir: $("#outdir"), browse: $("#browse"), go: $("#go"), goSummary: $("#go-summary"),
  more: $("#more"), moreSummary: $("#more-summary"),
  clipOpt: $("#clip-opt"), clipStart: $("#clip-start"), clipEnd: $("#clip-end"),
  clipTrack: $("#clip-track"), clipHelp: $("#clip-help"),
  subsOpt: $("#subs-opt"), subs: $("#subs"), subLangs: $("#sub-langs"),
  tagsOpt: $("#tags-opt"), smartTags: $("#smart-tags"), notify: $("#notify"),
  queue: $("#queue"), empty: $("#empty"), open: $("#open"), clear: $("#clear"),
  cancelAll: $("#cancel-all"), activeCount: $("#active-count"),
  history: $("#history"), histEmpty: $("#hist-empty"), histSearch: $("#hist-search"), histClear: $("#hist-clear"),
  version: $("#version"), banner: $("#update-banner"), updateText: $("#update-text"),
  updateBtn: $("#update-btn"), updateDismiss: $("#update-dismiss"),
  toast: $("#toast"), jobTpl: $("#job-tpl"), histTpl: $("#hist-tpl"),
};

const CONTAINERS = {
  video: [["mp4", "MP4 (plays everywhere)"], ["mkv", "MKV (keeps original codecs)"], ["webm", "WebM"]],
  audio: [["mp3", "MP3"], ["m4a", "M4A (no re-encode)"], ["opus", "Opus (smallest)"],
          ["flac", "FLAC"], ["wav", "WAV (uncompressed)"]],
};
const LOSSY = new Set(["mp3", "m4a", "opus"]);
const ACTIVE = new Set(["queued", "downloading", "processing"]);

/* What the link box currently holds:
   mode "none" | "single" | "playlist" | "multi"
   items: [{url, title, thumbnail, duration, checked, single, note, error}] */
let view = { mode: "none", key: "", items: [], info: null };
let prefs = {};

/* ---------------- utils ---------------- */

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

let toastTimer;
function toast(msg, isError = false) {
  els.toast.textContent = msg;
  els.toast.classList.toggle("error", isError);
  els.toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.add("hidden"), isError ? 6000 : 3000);
}

function duration(s) {
  if (s == null || Number.isNaN(s)) return "";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

function parseTime(t) {
  t = (t || "").trim();
  if (!t) return null;
  if (!/^\d+(\.\d+)?(:\d+(\.\d+)?){0,2}$/.test(t)) return NaN;
  return t.split(":").reduce((acc, p) => acc * 60 + parseFloat(p), 0);
}

const kind = () => $('input[name="kind"]:checked').value;
const extractUrls = (text) => [...new Set(text.match(/https?:\/\/[^\s"'<>]+/gi) || [])];
const fileName = (p) => (p || "").split(/[\\/]/).pop();
const folderName = (p) => fileName(p.replace(/[\\/]+$/, "")) || p;
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/* ---------------- theme ---------------- */

function setTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  $$(".theme button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.theme === t)));
  try { localStorage.setItem("ytdl-theme", t); } catch {}
}
$$(".theme button").forEach((b) => b.addEventListener("click", () => setTheme(b.dataset.theme)));

/* ---------------- preferences ---------------- */

function savePrefs() {
  prefs = {
    ...prefs,
    kind: kind(),
    [`container_${kind()}`]: els.container.value,
    quality: els.quality.value, bitrate: els.bitrate.value, outdir: els.outdir.value,
    subs: els.subs.value, subLangs: els.subLangs.value,
    smartTags: els.smartTags.checked, notify: els.notify.checked, moreOpen: els.more.open,
  };
  try { localStorage.setItem("ytdl-prefs", JSON.stringify(prefs)); } catch {}
}

function loadPrefs() {
  try { prefs = JSON.parse(localStorage.getItem("ytdl-prefs")) || {}; } catch { prefs = {}; }
}

/* ---------------- options ---------------- */

function fillContainers() {
  const k = kind();
  els.container.innerHTML = CONTAINERS[k].map(([v, label]) => `<option value="${v}">${label}</option>`).join("");
  els.container.value = prefs[`container_${k}`] || CONTAINERS[k][0][0];
}

const isMulti = () => view.mode === "playlist" || view.mode === "multi";

function syncOptions() {
  const k = kind(), c = els.container.value;
  els.qualityField.classList.toggle("hidden", k !== "video");
  els.bitrateField.classList.toggle("hidden", !(k === "audio" && LOSSY.has(c)));

  setDisabled(els.subsOpt, k !== "video");
  setDisabled(els.tagsOpt, k !== "audio");
  setDisabled(els.clipOpt, isMulti());

  // what is switched on, shown next to the collapsed "More options"
  const on = [];
  if (!isMulti() && (els.clipStart.value.trim() || els.clipEnd.value.trim()))
    on.push(`clip ${els.clipStart.value.trim() || "0:00"} to ${els.clipEnd.value.trim() || "end"}`);
  if (k === "video" && els.subs.value !== "off") on.push(`subtitles (${els.subLangs.value || "en"})`);
  if (k === "audio" && els.smartTags.checked) on.push("music tags");
  if (els.notify.checked) on.push("notify");
  els.moreSummary.textContent = on.length ? "On: " + on.join(", ") : "";

  renderClipTrack();
  updateGo();
}

function setDisabled(el, off) {
  el.classList.toggle("disabled", off);
  $$("input, select", el).forEach((i) => { i.disabled = off; });
}

function renderClipTrack() {
  const total = view.mode === "single" ? view.info?.duration : null;
  const s = parseTime(els.clipStart.value), e = parseTime(els.clipEnd.value);
  const active = total && (s != null || e != null) && !Number.isNaN(s) && !Number.isNaN(e);
  els.clipTrack.classList.toggle("hidden", !active);
  if (Number.isNaN(s) || Number.isNaN(e)) {
    els.clipHelp.textContent = "Use minutes and seconds, like 1:30, or hours too, like 1:02:30.";
    els.clipHelp.style.color = "var(--err)";
    return;
  }
  els.clipHelp.style.color = "";
  if (!active) {
    els.clipHelp.textContent = isMulti()
      ? "Clips work on a single video, not on a list."
      : "Leave both empty to save the whole video.";
    return;
  }
  const start = Math.max(0, s || 0), end = Math.min(total, e ?? total);
  const fill = $(".track-fill", els.clipTrack);
  fill.style.left = `${(start / total) * 100}%`;
  fill.style.width = `${Math.max(0, (end - start) / total) * 100}%`;
  els.clipHelp.textContent = end > start
    ? `Saves ${duration(end - start)} of ${duration(total)}, from ${duration(start)} to ${duration(end)}.`
    : "The end has to come after the start.";
  if (end <= start) els.clipHelp.style.color = "var(--err)";
}

function updateGo() {
  const audio = kind() === "audio";
  const noun = audio ? ["track", "tracks"] : ["video", "videos"];
  const n = isMulti() ? view.items.filter((i) => i.checked).length : 1;
  els.go.textContent = n > 1 ? `Download ${n} ${noun[1]}` : "Download";
  els.go.disabled = isMulti() && n === 0;

  const fmt = els.container.value.toUpperCase();
  const dest = folderName(els.outdir.value.trim() || "Downloads");
  els.goSummary.textContent = view.mode === "none"
    ? `${fmt} into ${dest}`
    : `${plural(n, noun[0], noun[1])} as ${fmt} into ${dest}`;
  els.goSummary.title = els.outdir.value;
}

$$('input[name="kind"]').forEach((r) => r.addEventListener("change", () => {
  fillContainers(); syncOptions(); savePrefs();
}));
[els.container, els.quality, els.bitrate, els.subs, els.smartTags, els.notify].forEach((el) =>
  el.addEventListener("change", () => { syncOptions(); savePrefs(); }));
[els.subLangs, els.clipStart, els.clipEnd, els.outdir].forEach((el) =>
  el.addEventListener("input", () => { syncOptions(); savePrefs(); }));
els.more.addEventListener("toggle", savePrefs);

/* ---------------- link box & preview ---------------- */

function autoGrow() {
  els.url.style.height = "auto";
  els.url.style.height = Math.min(els.url.scrollHeight + 2, 168) + "px";
  els.url.style.overflowY = els.url.scrollHeight > 168 ? "auto" : "hidden";
}

function message(text, error = false) {
  els.previewMsg.textContent = text || "";
  els.previewMsg.classList.toggle("error", error);
  els.previewMsg.classList.toggle("hidden", !text);
}

function resetPreview() {
  view = { mode: "none", key: "", items: [], info: null };
  els.preview.classList.add("hidden");
  els.picker.classList.add("hidden");
  els.skeleton.classList.add("hidden");
  els.pkFilter.value = "";
  message("");
  syncOptions();
}

let debounce;
els.url.addEventListener("input", () => {
  autoGrow();
  clearTimeout(debounce);
  debounce = setTimeout(loadPreview, 450);
});
els.url.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); els.form.requestSubmit(); }
});

els.paste.addEventListener("click", async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) return toast("The clipboard is empty.");
    els.url.value = text;
    autoGrow();
    loadPreview();
  } catch {
    els.url.focus();
    toast("Clipboard access was blocked. Press Ctrl+V in the Link box instead.");
  }
});

async function loadPreview() {
  const urls = extractUrls(els.url.value);
  const key = urls.join("\n");
  if (key === view.key) return;
  resetPreview();
  view.key = key;
  if (!urls.length) {
    if (els.url.value.trim()) message("That doesn't look like a link. It should start with https://", true);
    return;
  }
  if (urls.length > 1) return loadMulti(urls, key);

  els.skeleton.classList.remove("hidden");
  try {
    const info = await api("/api/info", { url: urls[0] });
    if (view.key !== key) return;
    els.skeleton.classList.add("hidden");
    if (info.playlist) return showPlaylist(info);
    view.mode = "single";
    view.info = info;
    view.items = [{ url: urls[0], title: info.title, thumbnail: info.thumbnail, checked: true, single: true }];
    els.thumb.src = info.thumbnail || "";
    els.pTitle.textContent = info.title || "Untitled video";
    els.pSub.textContent = [info.uploader, duration(info.duration)].filter(Boolean).join(", ");
    if (info.has_subs) {
      const langs = info.sub_langs.slice(0, 5).join(", ") + (info.sub_langs.length > 5 ? ", ..." : "");
      els.pSub.textContent += `. Subtitles: ${langs}`;
    }
    els.preview.classList.remove("hidden");
    syncOptions();
  } catch (e) {
    if (view.key !== key) return;
    els.skeleton.classList.add("hidden");
    message(`Couldn't open this link: ${e.message}. Check that it's public and complete.`, true);
  }
}

function showPlaylist(info) {
  view.mode = "playlist";
  view.info = info;
  view.items = info.entries.map((e) => ({ ...e, checked: true, single: true }));
  els.pkTitle.textContent = info.title || "Playlist";
  renderPicker();
  els.picker.classList.remove("hidden");
  syncOptions();
}

async function loadMulti(urls, key) {
  view.mode = "multi";
  view.items = urls.map((url) => ({ url, title: url, thumbnail: "", checked: true, single: true, note: "Looking up" }));
  els.pkTitle.textContent = plural(urls.length, "link", "links");
  renderPicker();
  els.picker.classList.remove("hidden");
  syncOptions();

  let next = 0;
  const worker = async () => {
    while (next < urls.length) {
      const item = view.items[next++];
      try {
        const info = await api("/api/info", { url: item.url });
        if (view.key !== key) return;
        Object.assign(item, {
          title: info.title || item.url, thumbnail: info.thumbnail || "",
          duration: info.duration, single: !info.playlist,
          note: info.playlist ? `Playlist, ${plural(info.entries.length, "video", "videos")}` : "",
        });
      } catch (e) {
        if (view.key !== key) return;
        Object.assign(item, { note: e.message, checked: false, error: true });
      }
      renderPicker();
    }
  };
  await Promise.all([worker(), worker(), worker()]);
}

function pickerSummary() {
  const sel = view.items.filter((i) => i.checked);
  const total = sel.reduce((s, i) => s + (i.duration || 0), 0);
  let text = `${sel.length} of ${view.items.length} selected`;
  if (total) text += `, ${duration(total)} total`;
  if (view.mode === "playlist" && view.info?.uploader) text = `${view.info.uploader}. ${text}`;
  return text;
}

function renderPicker() {
  const q = els.pkFilter.value.trim().toLowerCase();
  els.pkSub.textContent = pickerSummary();
  els.pkFilter.classList.toggle("hidden", view.items.length < 8);

  els.pkList.replaceChildren(...view.items.map((item) => {
    if (q && !item.title.toLowerCase().includes(q)) return null;
    const li = document.createElement("li");
    li.className = "pk-item" + (item.checked ? "" : " off") + (item.error ? " bad" : "");
    li.innerHTML = `<label><input type="checkbox"><span class="thumb"></span><span class="pk-text"></span><span class="pk-side"></span></label>`;
    const cb = $("input", li);
    cb.checked = item.checked;
    cb.disabled = !!item.error;
    if (item.thumbnail) $(".thumb", li).style.backgroundImage = `url("${item.thumbnail}")`;
    $(".pk-text", li).textContent = item.title;
    $(".pk-text", li).title = item.title;
    $(".pk-side", li).textContent = item.note || duration(item.duration);
    if (item.error) $(".pk-side", li).title = item.note;
    cb.addEventListener("change", () => {
      item.checked = cb.checked;
      li.classList.toggle("off", !item.checked);
      els.pkSub.textContent = pickerSummary();
      updateGo();
    });
    return li;
  }).filter(Boolean));
  updateGo();
}

function setAll(on) {
  const q = els.pkFilter.value.trim().toLowerCase();
  view.items.forEach((i) => { if (!i.error && (!q || i.title.toLowerCase().includes(q))) i.checked = on; });
  renderPicker();
}
els.pkAll.addEventListener("click", () => setAll(true));
els.pkNone.addEventListener("click", () => setAll(false));
els.pkFilter.addEventListener("input", renderPicker);

/* ---------------- folder ---------------- */

els.browse.addEventListener("click", async () => {
  els.browse.disabled = true;
  try {
    const { outdir } = await api("/api/browse", { outdir: els.outdir.value });
    if (outdir) { els.outdir.value = outdir; syncOptions(); savePrefs(); }
  } catch (e) { toast(e.message, true); }
  finally { els.browse.disabled = false; }
});
els.open.addEventListener("click", () =>
  api("/api/open", { outdir: els.outdir.value }).catch((e) => toast(e.message, true)));

/* ---------------- download ---------------- */

els.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearTimeout(debounce);
  const urls = extractUrls(els.url.value);
  if (!urls.length) {
    els.url.focus();
    return message("Paste a link first. It should start with https://", true);
  }

  let items;
  if (view.key === urls.join("\n") && view.mode !== "none") {
    items = view.items.filter((i) => i.checked)
      .map(({ url, title, thumbnail, single }) => ({ url, title, thumbnail, single }));
  } else {
    items = urls.map((url) => ({ url })); // lookup not finished yet: download as-is
  }
  if (!items.length) return toast("Tick at least one video to download.", true);

  const clip = isMulti() || items.length > 1 ? {} : { start: els.clipStart.value, end: els.clipEnd.value };
  const s = parseTime(clip.start), en = parseTime(clip.end);
  if (Number.isNaN(s) || Number.isNaN(en)) { els.more.open = true; return toast("Fix the clip times first, like 1:30.", true); }
  if (s != null && en != null && en <= s) { els.more.open = true; return toast("The clip end has to come after the start.", true); }

  els.go.disabled = true;
  try {
    await api("/api/download", {
      items,
      options: {
        kind: kind(), container: els.container.value,
        quality: els.quality.value, bitrate: els.bitrate.value,
        outdir: els.outdir.value.trim(), clip,
        subtitles: kind() === "video" ? els.subs.value : "off", sub_langs: els.subLangs.value,
        smart_tags: kind() === "audio" && els.smartTags.checked,
        notify: els.notify.checked,
      },
    });
    els.url.value = "";
    els.clipStart.value = els.clipEnd.value = "";
    autoGrow();
    resetPreview();
    switchTab("active");
    toast(items.length > 1 ? `Added ${items.length} downloads.` : "Download started.");
    refresh();
  } catch (err) {
    toast(err.message, true);
  } finally {
    updateGo();
    els.url.focus();
  }
});

/* ---------------- downloads list ---------------- */

const nodes = new Map();

function stepText(j) {
  const s = j.step || "";
  if (/Merger/.test(s)) return "Joining video and audio";
  if (/ExtractAudio/.test(s)) return `Converting to ${j.container.toUpperCase()}`;
  if (/Thumbnail/.test(s)) return "Adding cover art";
  if (/Subtitle/.test(s)) return "Adding subtitles";
  if (/Metadata/.test(s)) return "Writing tags";
  return "Processing";
}

function statusText(j, waiting) {
  const item = j.item ? `Item ${j.item}. ` : "";
  switch (j.state) {
    case "queued": return `Waiting, ${plural(waiting, "download", "downloads")} ahead`;
    case "downloading": {
      const parts = [`${j.progress.toFixed(0)}%`];
      if (j.speed) parts.push(j.speed);
      if (j.eta) parts.push(`${j.eta} left`);
      return item + parts.join(", ");
    }
    case "processing": return item + stepText(j);
    case "done": return j.files.length > 1
      ? `Saved ${j.files.length} files to ${folderName(j.outdir)}`
      : `Saved as ${fileName(j.files[0]) || "file"}`;
    case "cancelled": return "Cancelled. Partial files were removed.";
    case "error": {
      const why = (j.error || "Download failed").replace(/\.$/, "");
      return `${why}. Check the link opens in a browser, then Retry. If many fail, update yt-dlp.`;
    }
  }
  return "";
}

function jobAction(j) {
  if (ACTIVE.has(j.state)) return ["Cancel", () => api("/api/cancel", { id: j.id }).then(refresh)];
  if (j.state === "error" || j.state === "cancelled") return ["Retry", () => api("/api/retry", { id: j.id }).then(refresh)];
  if (j.state === "done" && j.files.length)
    return ["Show file", () => api("/api/reveal", { path: j.files[0] }).catch((e) => toast(e.message, true))];
  return null;
}

function render(jobs) {
  els.empty.classList.toggle("hidden", jobs.length > 0);
  const active = jobs.filter((j) => ACTIVE.has(j.state));
  const running = active.filter((j) => j.state !== "queued").length;
  els.activeCount.textContent = active.length;
  els.activeCount.classList.toggle("hidden", !active.length);
  els.cancelAll.classList.toggle("hidden", active.length < 2);
  els.clear.classList.toggle("hidden", jobs.length === active.length);
  document.title = active.length ? `(${active.length}) YT Downloader` : "YT Downloader";

  const seen = new Set();
  let queuedBefore = 0;
  const waitingAhead = new Map();
  for (const j of jobs) if (j.state === "queued") waitingAhead.set(j.id, running + queuedBefore++);

  [...jobs].reverse().forEach((j, i) => {
    seen.add(j.id);
    let li = nodes.get(j.id);
    if (!li) {
      li = els.jobTpl.content.firstElementChild.cloneNode(true);
      nodes.set(j.id, li);
    }
    if (els.queue.children[i] !== li) els.queue.insertBefore(li, els.queue.children[i] || null);
    li.className = `row ${j.state}`;
    const thumb = $(".thumb", li);
    if (j.thumbnail && thumb.dataset.src !== j.thumbnail) {
      thumb.dataset.src = j.thumbnail;
      thumb.style.backgroundImage = `url("${j.thumbnail}")`;
    }
    $(".tag", li).textContent = j.container.toUpperCase();
    $(".row-title", li).textContent = j.title;
    $(".row-title", li).title = j.title;
    $(".track-fill", li).style.width = `${j.progress}%`;
    const status = $(".row-status", li);
    status.textContent = statusText(j, waitingAhead.get(j.id) || 0);
    status.title = j.state === "error" ? j.error : "";

    const btn = $(".row-action", li);
    const action = jobAction(j);
    btn.classList.toggle("hidden", !action);
    if (action) { btn.textContent = action[0]; btn.onclick = action[1]; }
  });
  for (const [id, li] of nodes) if (!seen.has(id)) { li.remove(); nodes.delete(id); }
}

let pollTimer, lastDone = 0, offline = false;
async function refresh() {
  clearTimeout(pollTimer);
  try {
    const jobs = await api("/api/jobs");
    if (offline) { offline = false; toast("Reconnected."); }
    render(jobs);
    const done = jobs.filter((j) => j.state === "done").length;
    if (done !== lastDone) { lastDone = done; loadHistory(); }
    pollTimer = setTimeout(refresh, jobs.some((j) => ACTIVE.has(j.state)) ? 500 : 2500);
  } catch {
    if (!offline) { offline = true; toast("Lost connection to the app. Retrying...", true); }
    pollTimer = setTimeout(refresh, 3000);
  }
}

els.clear.addEventListener("click", () => api("/api/clear", {}).then(refresh));
els.cancelAll.addEventListener("click", () => api("/api/cancel", {}).then(refresh));

/* ---------------- history ---------------- */

let historyItems = [], historyError = false;

async function loadHistory() {
  try { historyItems = await api("/api/history"); historyError = false; }
  catch { historyError = true; }
  renderHistory();
}

function renderHistory() {
  const q = els.histSearch.value.trim().toLowerCase();
  const list = historyItems.filter((h) => !q || h.title.toLowerCase().includes(q));
  const [title, help] = $$("p", els.histEmpty);
  if (historyError) {
    title.textContent = "Couldn't load history";
    help.textContent = "The history file couldn't be read. It will be recreated after your next download.";
  } else if (historyItems.length) {
    title.textContent = "No matches";
    help.textContent = `Nothing in your history has "${els.histSearch.value.trim()}" in the title.`;
  } else {
    title.textContent = "Nothing here yet";
    help.textContent = "Finished downloads are kept here, so you can find the files again later.";
  }
  els.histEmpty.classList.toggle("hidden", list.length > 0);
  els.histClear.classList.toggle("hidden", !historyItems.length);
  els.histSearch.classList.toggle("hidden", historyItems.length < 2);

  els.history.replaceChildren(...list.slice(0, 200).map((h) => {
    const li = els.histTpl.content.firstElementChild.cloneNode(true);
    if (h.thumbnail) $(".thumb", li).style.backgroundImage = `url("${h.thumbnail}")`;
    $(".row-title", li).textContent = h.title;
    $(".row-title", li).title = h.title;
    $(".tag", li).textContent = (h.container || "").toUpperCase();
    const when = new Date(h.date).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    const meta = $(".hist-meta", li);
    meta.textContent = `${when}, ${h.files.length > 1 ? h.files.length + " files" : fileName(h.files[0])}`;
    if (!h.exists) {
      const miss = document.createElement("span");
      miss.className = "missing";
      miss.textContent = ". File was moved or deleted";
      meta.append(miss);
    }

    const show = $(".h-show", li);
    show.disabled = !h.exists;
    show.onclick = () => api("/api/reveal", { path: h.files.find(Boolean) }).catch((e) => toast(e.message, true));
    $(".h-again", li).onclick = () => {
      els.url.value = h.url;
      autoGrow();
      loadPreview();
      window.scrollTo({ top: 0, behavior: "smooth" });
      els.url.focus();
    };
    $(".h-remove", li).onclick = async () => {
      await api("/api/history/remove", { id: h.id });
      loadHistory();
    };
    return li;
  }));
}

els.histSearch.addEventListener("input", renderHistory);
els.histClear.addEventListener("click", async () => {
  if (!confirm("Clear the download history? Your downloaded files are not deleted.")) return;
  await api("/api/history/clear", {});
  loadHistory();
});

/* ---------------- tabs (arrow keys move between them) ---------------- */

const tabs = $$(".tab");
function switchTab(name, focus = false) {
  tabs.forEach((t) => {
    const on = t.dataset.tab === name;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    if (on && focus) t.focus();
  });
  $("#tab-active").classList.toggle("hidden", name !== "active");
  $("#tab-history").classList.toggle("hidden", name !== "history");
  if (name === "history") loadHistory();
}
tabs.forEach((t, i) => {
  t.addEventListener("click", () => switchTab(t.dataset.tab));
  t.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    switchTab(next.dataset.tab, true);
  });
});

/* ---------------- yt-dlp version / update ---------------- */

async function checkVersion() {
  try {
    const v = await api("/api/version");
    els.version.textContent = `yt-dlp ${v.current}`;
    els.version.classList.toggle("outdated", v.outdated);
    els.version.title = v.latest
      ? (v.outdated ? `Version ${v.latest} is available` : "Up to date")
      : "Couldn't check for updates";
    let dismissed = null;
    try { dismissed = localStorage.getItem("ytdl-dismissed-update"); } catch {}
    if (!v.outdated || dismissed === v.latest) return;
    els.updateText.textContent = v.frozen
      ? `yt-dlp ${v.latest} is out (you have ${v.current}). Run build.bat again to include it.`
      : `yt-dlp ${v.latest} is out (you have ${v.current}). If downloads start failing, updating usually fixes it.`;
    els.updateBtn.classList.toggle("hidden", v.frozen);
    els.updateDismiss.onclick = () => {
      try { localStorage.setItem("ytdl-dismissed-update", v.latest); } catch {}
      els.banner.classList.add("hidden");
    };
    els.banner.classList.remove("hidden");
  } catch {
    els.version.textContent = "";
  }
}

els.updateBtn.addEventListener("click", async () => {
  els.updateBtn.disabled = true;
  els.updateBtn.textContent = "Updating...";
  try {
    const { version } = await api("/api/update", {});
    els.updateText.textContent = `Updated to yt-dlp ${version}. Close and reopen the app to use it.`;
    els.updateBtn.classList.add("hidden");
    els.updateDismiss.textContent = "OK";
    els.version.textContent = `yt-dlp ${version} after restart`;
    els.version.classList.remove("outdated");
  } catch (e) {
    toast(`Update failed: ${e.message}`, true);
    els.updateBtn.disabled = false;
    els.updateBtn.textContent = "Update yt-dlp";
  }
});

/* ---------------- init ---------------- */

async function init() {
  let theme = "system";
  try { theme = localStorage.getItem("ytdl-theme") || "system"; } catch {}
  setTheme(theme);

  loadPrefs();
  let cfg = { outdir: "" };
  try { cfg = await api("/api/config"); } catch {}
  els.outdir.value = prefs.outdir || cfg.outdir;
  if (prefs.kind === "audio") $("#kind-audio").checked = true;
  fillContainers();
  if (prefs.quality) els.quality.value = prefs.quality;
  if (prefs.bitrate) els.bitrate.value = prefs.bitrate;
  if (prefs.subs) els.subs.value = prefs.subs;
  if (prefs.subLangs != null) els.subLangs.value = prefs.subLangs;
  if (prefs.smartTags != null) els.smartTags.checked = prefs.smartTags;
  if (prefs.notify != null) els.notify.checked = prefs.notify;
  els.more.open = !!prefs.moreOpen;
  syncOptions();
  refresh();
  loadHistory();
  checkVersion();
  els.url.focus();
}

init();
