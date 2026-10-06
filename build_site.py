#!/usr/bin/env python3
"""
build_site.py  Turn a folder of amp-setting renders into a static A/B listening site.

    python build_site.py SOURCE_DIR [OUT_DIR=docs] [--serve] [--format auto|copy|flac]
                         [--no-align] [--jobs N]

Expects files named like
    "7 - alnico 2 strat neck single notes - 73 volume at 8 100pf bright cap.mp3"
i.e. "<riff number> - <riff name> - <setting name>.<ext>".

What it does
  1. Parses riffs and settings out of the file names.
  2. Measures integrated loudness (ITU-R BS.1770 / LUFS) and sample peak of every file
     and writes a per-file gain so every setting in a riff plays at the same level.
  3. Checks time alignment inside each riff (amplitude-envelope cross-correlation,
     roughly 1 ms resolution) and writes a per-file offset if anything is out by more than 2 ms.
  4. Copies (or transcodes) the audio into OUT_DIR/audio and writes manifest.json.
  5. Copies index.html (the player, next to this script) into OUT_DIR.

Needs: Python 3.9+, numpy, scipy, and ffmpeg on PATH.
"""
import argparse
import csv
import functools
import http.server
import json
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from scipy import signal

AUDIO_EXT = {".mp3", ".wav", ".flac", ".aif", ".aiff", ".ogg", ".m4a"}
NAME_RE = re.compile(r"^(\d+)\s+-\s+(.+?)\s+-\s+(.+)$")
POS_RE = re.compile(r"^(?P<guitar>.*?)\s+(?P<pos>neck|bridge)\s+(?P<phrase>.+)$", re.I)
# Display-only fixes for known typos in the old file names. Renamed files pass through untouched.
RIFF_FIXES = [
    (re.compile(r"\bbridgt\b", re.I), "bridge"),
    (re.compile(r"\bneck bridge\b", re.I), "bridge"),
    (re.compile(r"\bshuffle single notes\b", re.I), "single notes"),
]

ALIGN_THRESHOLD_MS = 2.0      # offsets smaller than this are treated as aligned
ALIGN_MIN_CORR = 0.6          # below this the estimate is not trusted, file is left alone
PEAK_CEILING_DB = -1.5        # matched files never peak above this

# ---------------------------------------------------------------- setting names

TOKEN_RE = re.compile(
    r"""
     (?P<volume>volume\s+at\s+(?P<vol>\d+))
    |(?P<bright>(?P<bval>\d+\s*pf|\d{4})\s+bright(?:\s+cap)?)
    |(?P<bias>lower\s+bias)
    |(?P<chan>jumpered\s+channels|normal\s+channel)
    |(?P<att>(?P<attv>yes|no)\s+attenuator)
    |(?P<c022>022(?:\s+pi)?)
    |(?P<k10>10k)
    |(?P<fifty>50\+50)
    |(?P<word>\S+)
    """,
    re.X | re.I,
)
FAMILY_ORDER = ["volume", "bright", "bias", "chan", "att", "c022", "k10", "fifty", "other"]
FAMILY_NAME = {
    "volume": "volume", "bright": "bright cap", "bias": "bias", "chan": "channels",
    "att": "attenuator", "c022": "022", "k10": "10k", "fifty": "50+50", "other": "other",
}


def parse_setting(raw):
    """Split a raw setting name into a group, ordered tokens and a readable label."""
    s = raw.strip().lower().replace("attenutaor", "attenuator").replace("_", " ")
    if s.startswith("original"):
        return {"group": "Original mic", "short": "Original mic", "tokens": [],
                "detail": "Mic'd amp from the original DI take (67 settings, yes attenuator), not re-amped",
                "label": "Original mic", "fam": {}}
    m = re.match(r"^(\d+)\s*watt\b\s*(.*)$", s)
    if m:
        group, short, rest = f"{m.group(1)} watt", f"{m.group(1)} watt", m.group(2)
    else:
        m = re.match(r"^(67|73)\b(?:\s+settings)?\s*(.*)$", s)
        if m:
            group, short, rest = f"{m.group(1)} settings", m.group(1), m.group(2)
        else:
            group, short, rest = "Other", "", s
    toks = []
    for t in TOKEN_RE.finditer(rest):
        if t.group("volume"):
            toks.append(("volume", t.group("vol"), f"volume at {t.group('vol')}"))
        elif t.group("bright"):
            v = re.sub(r"\s+", "", t.group("bval"))
            toks.append(("bright", v, f"{v} bright cap"))
        elif t.group("bias"):
            toks.append(("bias", "lower", "lower bias"))
        elif t.group("chan"):
            txt = re.sub(r"\s+", " ", t.group("chan"))
            toks.append(("chan", txt, txt))
        elif t.group("att"):
            v = t.group("attv")
            toks.append(("att", v, f"{v} attenuator"))
        elif t.group("c022"):
            txt = re.sub(r"\s+", " ", t.group("c022"))
            toks.append(("c022", txt, txt))
        elif t.group("k10"):
            toks.append(("k10", "10k", "10k"))
        elif t.group("fifty"):
            toks.append(("fifty", "50+50", "50+50"))
        else:
            w = t.group("word")
            if w in ("settings", "with", "and"):
                continue
            toks.append(("other", w, w))
    toks.sort(key=lambda t: FAMILY_ORDER.index(t[0]))
    detail = ", ".join(t[2] for t in toks)
    label = f"{short}: {detail}" if short and detail else (detail or group)
    fam = {}
    for f, v, _ in toks:
        fam.setdefault(f, v)
    return {"group": group, "short": short, "tokens": toks, "detail": detail, "label": label, "fam": fam}


def slug(s):
    s = s.lower().replace("+", "p")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def single_change_pairs(settings):
    """Pairs in the same group that differ by exactly one knob family."""
    pairs = []
    for i, a in enumerate(settings):
        for b in settings[i + 1:]:
            if a["group"] != b["group"] or not a["_fam"] or not b["_fam"]:
                continue
            fams = set(a["_fam"]) | set(b["_fam"])
            diff = [f for f in fams if a["_fam"].get(f) != b["_fam"].get(f)]
            if len(diff) != 1:
                continue
            f = diff[0]
            va, vb = a["_fam"].get(f), b["_fam"].get(f)
            if va is None or vb is None:
                tok = next(t[2] for t in (a["_tokens"] if va is not None else b["_tokens"]) if t[0] == f)
                change = f"{tok}: with and without"
            else:
                change = f"{FAMILY_NAME[f]}: {va} and {vb}"
            pairs.append({"a": a["id"], "b": b["id"], "change": change})
    return pairs


def parse_riff(name):
    name = name.strip()
    for rx, repl in RIFF_FIXES:
        name = rx.sub(repl, name)
    m = POS_RE.match(name)
    if not m:
        return {"guitar": "", "position": "", "phrase": name}
    pos = m.group("pos").lower()
    words = m.group("guitar").split()
    guitar = " ".join(w if (w.isupper() or any(c.isdigit() for c in w)) else w.capitalize() for w in words)
    return {"guitar": guitar, "position": pos, "phrase": m.group("phrase").strip()}


# ---------------------------------------------------------------- audio analysis

B1 = [1.53512485958697, -2.69169618940638, 1.19839281085285]
A1 = [1.0, -1.69065929318241, 0.73248077421585]
B2 = [1.0, -2.0, 1.0]
A2 = [1.0, -1.99004745483398, 0.99007225036621]
SR = 48000


def decode(path):
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def integrated_lufs(x):
    """BS.1770-4 integrated loudness for a (n, 2) array at 48 kHz."""
    y = signal.lfilter(B2, A2, signal.lfilter(B1, A1, x, axis=0), axis=0)
    blk, step = int(0.4 * SR), int(0.1 * SR)
    if len(y) < blk:
        y = np.vstack([y, np.zeros((blk - len(y), 2))])
    cs = np.vstack([np.zeros((1, 2)), np.cumsum(y * y, axis=0)])
    starts = np.arange(0, len(y) - blk + 1, step)
    z = ((cs[starts + blk] - cs[starts]) / blk).sum(axis=1)
    l = -0.691 + 10 * np.log10(np.maximum(z, 1e-12))
    keep = l > -70
    if not keep.any():
        return -70.0
    rel = -0.691 + 10 * np.log10(z[keep].mean()) - 10
    keep = keep & (l > rel)
    if not keep.any():
        return -70.0
    return float(-0.691 + 10 * np.log10(z[keep].mean()))


def envelope(x):
    """Smooth amplitude envelope at 1 kHz, used to find timing offsets between renders."""
    m = np.abs(x.mean(axis=1))
    sos = signal.butter(4, 150, "low", fs=SR, output="sos")
    e = signal.sosfiltfilt(sos, m)[:: SR // 1000]
    e = np.sqrt(np.maximum(e, 0))
    return e - e.mean()


def analyse(path):
    x = decode(path)
    peak = float(np.abs(x).max())
    return {
        "lufs": integrated_lufs(x),
        "peak_db": 20 * np.log10(max(peak, 1e-9)),
        "dur": len(x) / SR,
        "env": envelope(x),
    }


def lag_ms(ref, x, maxlag=100):
    """Lag of x relative to ref in ms (positive = x is late), and the correlation at that lag."""
    n = min(len(ref), len(x))
    a, b = ref[:n], x[:n]
    c = signal.correlate(b, a, mode="full", method="fft")
    lags = signal.correlation_lags(n, n)
    c = c / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-12)
    idx = np.where(np.abs(lags) <= maxlag)[0]
    k = idx[np.argmax(c[idx])]
    d = 0.0
    if 0 < k < len(c) - 1:
        y0, y1, y2 = c[k - 1], c[k], c[k + 1]
        den = y0 - 2 * y1 + y2
        if den != 0:
            d = 0.5 * (y0 - y2) / den
    return float(lags[k] + d), float(c[k])


def align_riff(files):
    """files: list of analysis dicts. Returns (offsets_ms list, notes list)."""
    n = len(files)
    envs = [f["env"] for f in files]
    L = min(len(e) for e in envs)
    E = np.array([e[:L] / (np.linalg.norm(e[:L]) + 1e-12) for e in envs])
    ref = int(np.argmax((E @ E.T).sum(axis=1)))
    lags, notes = [0.0] * n, []
    for i in range(n):
        if i == ref:
            continue
        lag, corr = lag_ms(envs[ref], envs[i])
        if corr < ALIGN_MIN_CORR:
            notes.append(f"low confidence (corr {corr:.2f}), left unaligned")
            lag = 0.0
        lags[i] = lag if abs(lag) >= 1.0 else 0.0
    if max(abs(l) for l in lags) < ALIGN_THRESHOLD_MS:
        return [0.0] * n, max(abs(l) for l in lags), notes
    lo = min(lags)
    return [l - lo for l in lags], max(abs(l) for l in lags), notes


# ---------------------------------------------------------------- feature matrix

# (key, short column heading, full wording). For either/or items a check means the FIRST option listed.
FEATURES = [
    ("original", "Original recording", "Original recording"),
    ("attenuation", "Attenuation", "Attenuation"),
    ("volume8", "Volume 8 (not 5)", "Volume at 8 (not volume at 5)"),
    ("bright100", "100pf bright (not 4700pf)", "100pf bright cap (not 4700pf)"),
    ("splitcath", "Split cathode (not shared)", "Split cathode (not shared cathode)"),
    ("v1b022", "0.022uf V1B (not 0.0022uf)", "0.022uf V1B coupling cap (not 0.0022uf)"),
    ("leadstack", "Lead tone stack", "Lead tone stack"),
    ("nfb67", "67-spec NFB (not 72)", "67-spec (high) NFB (not 72-spec low NFB)"),
    ("postpi01", "0.1uf post-PI (not 0.022uf)", "0.1uf post phase inverter couplers (not 0.022uf)"),
    ("filter48", "Filtering 48+32+32+32+32", "48+32+32+32+32uf filtering (not 100+50+50+32+32uf)"),
    ("dropres8k2", "2x8k2 B+ droppers (not 2x10k)", "2 x 8k2 B+ dropping resistors (not 2 x 10k)"),
    ("highbias", "High bias (not low)", "High bias (not low bias)"),
    ("watt18", "18 watt amp", "18 watt (separate amp)"),
    ("normal", "Normal channel", "Normal channel"),
    ("brightch", "Bright channel", "Bright channel"),
    ("jumpered", "Jumpered channels", "Jumpered channels"),
]
YES = {"1", "y", "yes", "true", "t", "check", "✓"}
NO = {"0", "n", "no", "false", "f", "x", "✗"}


def parse_cell(v):
    v = (v or "").strip().lower()
    return 1 if v in YES else 0 if v in NO else None


def infer_features(s):
    """Fill in only what the file name itself states. Everything else stays blank for the owner."""
    f = {k: None for k, _, _ in FEATURES}
    fam = s["_fam"]
    f["original"] = 1 if s["group"] == "Original mic" else 0
    f["watt18"] = 1 if s["group"] == "18 watt" else 0
    if s["group"] == "Original mic":
        f["attenuation"] = 1                       # stated by the owner: 67 settings, yes attenuator
    if "att" in fam:
        f["attenuation"] = 1 if fam["att"] == "yes" else 0
    if "volume" in fam:
        f["volume8"] = {"8": 1, "5": 0}.get(fam["volume"])
    if "bright" in fam:
        f["bright100"] = 1 if fam["bright"].startswith("100") else 0 if fam["bright"].startswith("4700") else None
    if "bias" in fam:
        f["highbias"] = 0                          # "lower bias" in the name
    if "chan" in fam:
        f["jumpered" if "jumpered" in fam["chan"] else "normal"] = 1
    return f


APPENDIX_TEMPLATE = """## Settings common to every recording {#ap-common}

- Microphone: large diaphragm condenser, 6 ft from the cabinet
- Speakers: 4x12
<!-- Add your tone control settings and anything else that never changed, one bullet per line. -->

## What each setting means

""" + "\n".join(f"### {lg} {{#ap-{k}}}\n<!-- Write about this setting here. Plain text, **bold**, *italics*, [links](https://example.com) and - bullets work. -->\n"
               for k, _, lg in FEATURES)


# ---------------------------------------------------------------- build

def find_ffmpeg():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg was not found on PATH. Install it (Windows: winget install Gyan.FFmpeg) and try again.")


def scan(src):
    riffs = {}
    skipped = []
    for p in sorted(Path(src).iterdir()):
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        m = NAME_RE.match(p.stem)
        if not m:
            skipped.append(p.name)
            continue
        n, rname, sname = int(m.group(1)), m.group(2), m.group(3)
        riffs.setdefault(n, {"name": rname, "files": {}})
        if riffs[n]["name"] != rname:
            print(f"warning: riff {n} has two different names: {riffs[n]['name']!r} and {rname!r}")
        riffs[n]["files"][sname.strip()] = p
    return riffs, skipped


def out_name(p, fmt):
    ext = p.suffix.lower()
    if fmt == "flac" or (fmt == "auto" and ext in (".wav", ".aif", ".aiff")):
        return ".flac"
    return ext


def place(src, dst, fmt):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix == src.suffix.lower():
        shutil.copy2(src, dst)
    else:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:a", "flac",
                        "-compression_level", "8", str(dst)], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("out", nargs="?", default="docs", help="output folder (default: docs)")
    ap.add_argument("--format", choices=["auto", "copy", "flac"], default="auto",
                    help="auto: keep mp3/flac as they are, convert wav/aiff to flac")
    ap.add_argument("--no-align", action="store_true", help="skip the alignment check")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--serve", action="store_true", help="serve OUT on localhost:8000 when done")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    find_ffmpeg()
    src, out = Path(args.source), Path(args.out)
    riffs, skipped = scan(src)
    if not riffs:
        sys.exit(f"No audio files named '<n> - <riff> - <setting>' found in {src}")
    for s in skipped:
        print(f"skipped (name does not match the pattern): {s}")

    # settings: union over all riffs, in a stable logical order
    raw_names = sorted({s for r in riffs.values() for s in r["files"]})
    settings = []
    for raw in raw_names:
        ps = parse_setting(raw)
        settings.append({"raw": raw, "id": slug(raw), "group": ps["group"], "label": ps["label"],
                         "detail": ps["detail"], "_fam": ps["fam"], "_tokens": ps["tokens"],
                         "_ntok": len(ps["tokens"])})
    ids = [s["id"] for s in settings]
    if len(set(ids)) != len(ids):
        sys.exit("Two different setting names collapse to the same id; rename one of them.")
    group_rank = {"Original mic": 0, "18 watt": 1, "67 settings": 2, "73 settings": 3}
    settings.sort(key=lambda s: (group_rank.get(s["group"], 9), s["_ntok"], s["label"]))
    by_raw = {s["raw"]: s for s in settings}

    out.mkdir(parents=True, exist_ok=True)
    cfg_path = out / "settings.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    cfg.setdefault("title", "Plexi settings, back to back")
    cfg.setdefault("intro", "One performance, re-amped through every setup. Flip between them while it plays "
                            "and hear what actually changes.")
    cfg.setdefault("settings", {})
    cfg.setdefault("riffs", {})
    cfg.setdefault("featured", [])
    for s in settings:
        cfg["settings"].setdefault(s["id"], {"label": s["label"], "detail": s["detail"], "source_name": s["raw"]})

    # feature matrix (editable in features.csv)
    feat_path = out / "features.csv"
    existing = {}
    if feat_path.exists():
        with open(feat_path, newline="", encoding="utf-8-sig") as fh:
            rd = csv.DictReader(fh)
            have_cols = set(rd.fieldnames or [])
            for row in rd:
                existing[(row.get("id") or "").strip()] = row
    else:
        have_cols = set()
    feat_vals, changed = {}, (not feat_path.exists()) or bool({k for k, _, _ in FEATURES} - have_cols)
    for s_ in settings:
        row = existing.get(s_["id"])
        if row is None:
            feat_vals[s_["id"]] = infer_features(s_)
            changed = True
        else:
            feat_vals[s_["id"]] = {k: parse_cell(row.get(k)) for k, _, _ in FEATURES}
    if changed:
        with open(feat_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "setting"] + [k for k, _, _ in FEATURES])
            for s_ in settings:
                w.writerow([s_["id"], s_["raw"]] + ["" if feat_vals[s_["id"]][k] is None else feat_vals[s_["id"]][k]
                                                   for k, _, _ in FEATURES])
    blanks = sum(v is None for d in feat_vals.values() for v in d.values())
    print(f"features.csv: {blanks} of {len(settings) * len(FEATURES)} cells are blank. Fill them in at {feat_path}")
    ap_path = out / "appendix.md"
    if not ap_path.exists():
        ap_path.write_text(APPENDIX_TEMPLATE, encoding="utf-8")
    else:
        text = ap_path.read_text(encoding="utf-8")
        keys = [k for k, _, _ in FEATURES]
        for i, (k, _, lg) in enumerate(FEATURES):
            if f"{{#ap-{k}}}" in text:
                continue
            block = f"### {lg} {{#ap-{k}}}\n<!-- Write about this setting here. -->\n\n"
            at = -1
            for kk in keys[i + 1:]:
                m_ = re.search(rf"^###[^\n]*\{{#ap-{kk}\}}", text, re.M)
                if m_:
                    at = m_.start()
                    break
            text = text[:at] + block + text[at:] if at >= 0 else text.rstrip("\n") + "\n\n" + block
            print(f"appendix.md: added a section for {lg}")
        ap_path.write_text(text, encoding="utf-8")

    # analysis
    jobs = [(n, raw, p) for n, r in sorted(riffs.items()) for raw, p in r["files"].items()]
    print(f"{len(riffs)} riffs, {len(settings)} settings, {len(jobs)} files. Analysing...")
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        results = list(ex.map(lambda j: analyse(j[2]), jobs))
    analysis = {(j[0], j[1]): r for j, r in zip(jobs, results)}

    manifest_riffs, report = [], []
    worst_lag = 0.0
    for n, r in sorted(riffs.items()):
        raws = sorted(r["files"])
        missing = [s["raw"] for s in settings if s["raw"] not in r["files"]]
        if missing:
            print(f"warning: riff {n} is missing {len(missing)} settings: {', '.join(missing)}")
        items = [analysis[(n, raw)] for raw in raws]
        lufs = [i["lufs"] for i in items]
        target = min(float(np.median(lufs)), min(l - i["peak_db"] + PEAK_CEILING_DB for l, i in zip(lufs, items)))
        if args.no_align:
            offs, mx, notes = [0.0] * len(items), 0.0, []
        else:
            offs, mx, notes = align_riff(items)
        worst_lag = max(worst_lag, mx)
        durs = [i["dur"] - o / 1000 for i, o in zip(items, offs)]
        if max(durs) - min(durs) > 0.05:
            print(f"warning: riff {n}: file lengths differ by {max(durs) - min(durs):.2f} s")
        files = {}
        for raw, item, off in zip(raws, items, offs):
            s = by_raw[raw]
            dst = Path("audio") / f"riff-{n:02d}" / (s["id"] + out_name(r["files"][raw], args.format))
            place(r["files"][raw], out / dst, args.format)
            files[s["id"]] = {"url": dst.as_posix(), "gain_db": round(target - item["lufs"], 2),
                              "offset_ms": round(off, 2), "lufs": round(item["lufs"], 2),
                              "peak_db": round(item["peak_db"], 2)}
        for note in notes:
            print(f"note: riff {n}: {note}")
        spread = max(lufs) - min(lufs)
        report.append((n, spread, mx))
        rp = parse_riff(r["name"])
        ov = cfg["riffs"].get(str(n), {})
        label = ov.get("title") or ", ".join(x for x in [rp["position"].capitalize(), rp["phrase"]] if x)
        manifest_riffs.append({"n": n, "name": r["name"], "guitar": ov.get("guitar", rp["guitar"]),
                               "label": label, "duration": round(min(durs), 3), "files": files})
        cfg["riffs"].setdefault(str(n), {"title": label, "guitar": rp["guitar"], "source_name": r["name"]})

    pairs = single_change_pairs(settings)
    orig = next((x for x in settings if x["group"] == "Original mic"), None)
    reamp = next((x for x in settings if x["group"] == "67 settings" and x["_fam"] == {"att": "yes"}), None)
    if orig and reamp:
        pairs.insert(0, {"a": orig["id"], "b": reamp["id"],
                         "change": "same settings, original mic take vs the re-amp of it"})
    manifest = {
        "title": cfg["title"], "intro": cfg["intro"],
        "settings": [{"id": s["id"], "group": s["group"],
                      "label": cfg["settings"][s["id"]].get("label", s["label"]),
                      "detail": cfg["settings"][s["id"]].get("detail", s["detail"]),
                      "note": cfg["settings"][s["id"]].get("note", ""),
                      "features": feat_vals[s["id"]]} for s in settings],
        "features": [{"key": k, "short": sh, "long": lg} for k, sh, lg in FEATURES],
        "pairs": cfg["featured"] if cfg["featured"] else pairs,
        "riffs": [dict(r, label=cfg["riffs"][str(r["n"])].get("title", r["label"]),
                       guitar=cfg["riffs"][str(r["n"])].get("guitar", r["guitar"])) for r in manifest_riffs],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    cfg_path.write_text(json.dumps(cfg, indent=1, ensure_ascii=False), encoding="utf-8")
    tpl = Path(__file__).with_name("index.html")
    if tpl.exists():
        shutil.copy2(tpl, out / "index.html")
    else:
        print("warning: index.html not found next to build_site.py; copy it into the output folder yourself")

    print("\nLoudness spread inside each riff before matching (max minus min LUFS):")
    for n, spread, mx in report:
        print(f"  riff {n:>2}: {spread:5.1f} LU   max timing offset {mx:5.1f} ms")
    print(f"\nWorst timing offset found: {worst_lag:.1f} ms "
          f"({'corrected in manifest' if worst_lag >= ALIGN_THRESHOLD_MS else 'treated as aligned'})")
    print(f"{len(pairs)} single-change pairs found.  Site written to {out.resolve()}")

    if args.serve:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
            url = f"http://localhost:{args.port}/"
            print(f"Serving {url}  (Ctrl+C to stop)")
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()