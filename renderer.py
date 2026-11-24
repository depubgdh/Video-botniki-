"""Phonk edit renderer: rasm + musiqa + so'zlar -> MP4 (bass'ga moslashgan animatsiya)."""
import math
import os
import random
import shutil
import subprocess
import tempfile

import cv2
import librosa
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FPS = 30
MIN_DUR = 10.0
MAX_DUR = 25.0

FORMATS = {
    ("shorts", "720"): (720, 1280),
    ("shorts", "1080"): (1080, 1920),
    ("youtube", "720"): (1280, 720),
    ("youtube", "1080"): (1920, 1080),
}
COLORS = {  # RGB
    "purple": (190, 40, 255),
    "red": (255, 40, 60),
    "blue": (50, 120, 255),
    "cyan": (0, 230, 255),
    "green": (60, 255, 120),
    "gold": (255, 190, 40),
}
FONT_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "font.ttf"),  # o'zingizning shriftingiz
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


# ----------------------------------------------------------------- audio
def _decode(src, wav):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "1",
         "-ar", "22050", "-f", "wav", wav],
        check=True,
    )


def _norm(x):
    lo, hi = np.percentile(x, 10), np.percentile(x, 95)
    return np.clip((x - lo) / (hi - lo + 1e-9), 0, 1)


def analyze(wav):
    y, sr = librosa.load(wav, sr=22050, mono=True)
    hop = 512
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    bass = S[freqs < 160].mean(axis=0)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    n = min(len(bass), len(onset))
    bass, onset = bass[:n], onset[:n]
    _, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop)
    return {
        "total": len(y) / sr, "bass": bass, "onset": onset, "hop": hop, "sr": sr,
        "beats": np.atleast_1d(librosa.frames_to_time(beats, sr=sr, hop_length=hop)),
    }


def pick_window(a, dur):
    """Musiqa uzun bo'lsa, eng kuchli (bass baland) qismini tanlaydi."""
    total = a["total"]
    if total <= dur + 0.5:
        return 0.0
    e = _norm(a["bass"]) + _norm(a["onset"])
    w = int(dur * a["sr"] / a["hop"])
    cs = np.concatenate([[0], np.cumsum(e)])
    scores = cs[w:] - cs[:-w]
    start = int(np.argmax(scores)) * a["hop"] / a["sr"]
    beats = a["beats"]
    if len(beats):
        start = float(beats[np.argmin(np.abs(beats - start))])
    return float(min(max(start, 0.0), max(total - dur, 0.0)))


def energy_curve(a, start, n):
    t = start + np.arange(n) / FPS
    tf = np.arange(len(a["bass"])) * a["hop"] / a["sr"]
    b = _norm(np.interp(t, tf, a["bass"]))
    o = _norm(np.interp(t, tf, a["onset"]))
    raw = 0.7 * b + 0.3 * o
    pulse = np.zeros(n)
    for bt in a["beats"]:
        k = int(round((bt - start) * FPS))
        if 0 <= k < n:
            pulse[k] = 0.85
    e = np.zeros(n)
    cur = 0.0
    for i in range(n):
        target = max(raw[i], pulse[i])
        cur = target if target > cur else cur * 0.85  # tez ko'tarilib, silliq tushadi
        e[i] = cur
    return np.clip(e, 0, 1)


# ----------------------------------------------------------------- tasvirlar
def _read(path, what):
    im = cv2.imread(path, cv2.IMREAD_COLOR)
    if im is None:
        raise ValueError(f"{what} rasmini o'qib bo'lmadi")
    return im


def build_bg(path, W, H, color_bgr):
    im = _read(path, "Orqa fon")
    h, w = im.shape[:2]
    f = max(W / w, H / h) * 1.3
    im = cv2.resize(im, (int(w * f), int(h * f)),
                    interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
    tint = np.full_like(im, color_bgr)
    im = cv2.addWeighted(im, 0.84, tint, 0.16, 0)
    return cv2.convertScaleAbs(im, alpha=0.8)


def build_vignette(W, H):
    y, x = np.ogrid[:H, :W]
    d = np.sqrt(((x - W / 2) / (W / 2)) ** 2 + ((y - H / 2) / (H / 2)) ** 2)
    v = np.clip(1.15 - 0.55 * d ** 1.6, 0.25, 1.0)
    return (np.repeat(v[:, :, None], 3, axis=2) * 255).astype(np.uint8)


def build_center(path, D, color_bgr):
    im = _read(path, "O'rtadagi")
    h, w = im.shape[:2]
    s = min(h, w)
    im = im[(h - s) // 2:(h - s) // 2 + s, (w - s) // 2:(w - s) // 2 + s]
    im = cv2.resize(im, (D, D), interpolation=cv2.INTER_AREA)

    CS = int(D * 2.0)
    c = CS // 2
    big = np.zeros((D * 4, D * 4), np.uint8)
    cv2.circle(big, (D * 2, D * 2), D * 2 - 2, 255, -1)
    mask = cv2.resize(big, (D, D), interpolation=cv2.INTER_AREA)

    pic = np.zeros((CS, CS, 4), np.uint8)
    o = (CS - D) // 2
    pic[o:o + D, o:o + D, :3] = im
    pic[o:o + D, o:o + D, 3] = mask
    th = max(3, D // 55)
    cv2.circle(pic, (c, c), D // 2 + th // 2, (*color_bgr, 255), th, cv2.LINE_AA)

    ring_a = np.zeros((CS, CS, 4), np.uint8)
    r = int(D / 2 * 1.22)
    th2 = max(3, D // 70)
    for k in range(0, 360, 30):
        cv2.ellipse(ring_a, (c, c), (r, r), 0, k, k + 18, (*color_bgr, 255), th2, cv2.LINE_AA)

    ring_b = np.zeros((CS, CS, 4), np.uint8)
    r2 = int(D / 2 * 1.38)
    for k in range(0, 360, 60):
        cv2.ellipse(ring_b, (c, c), (r2, r2), 0, k + 10, k + 34, (*color_bgr, 210),
                    max(2, th2 - 1), cv2.LINE_AA)

    g = np.zeros((CS, CS), np.uint8)
    cv2.circle(g, (c, c), int(D / 2 * 1.05), 255, max(6, D // 10), cv2.LINE_AA)
    g = g.astype(np.float32) / 255.0
    glow = np.clip(cv2.GaussianBlur(g, (0, 0), D / 16) * 0.9
                   + cv2.GaussianBlur(g, (0, 0), D / 6) * 0.8, 0, 1)
    return pic, ring_a, ring_b, glow


# ----------------------------------------------------------------- matn
def _font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap(draw, text, font, maxw):
    out = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split():
            t = (cur + " " + w).strip()
            if not cur or draw.textlength(t, font=font) <= maxw:
                cur = t
            else:
                out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    return "\n".join(out) or " "


def text_layer(text, W, fs, color_rgb):
    font = _font(fs)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    wrapped = _wrap(tmp, text, font, W * 0.86)
    sp = fs // 6
    box = tmp.multiline_textbbox((0, 0), wrapped, font=font, align="center",
                                 stroke_width=fs // 8, spacing=sp)
    pad = fs
    cw, ch = int(box[2] - box[0]) + pad * 2, int(box[3] - box[1]) + pad * 2
    pos = (pad - box[0], pad - box[1])

    glow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(glow).multiline_text(
        pos, wrapped, font=font, fill=color_rgb + (255,), align="center",
        stroke_width=fs // 8, stroke_fill=color_rgb + (255,), spacing=sp)
    glow = glow.filter(ImageFilter.GaussianBlur(fs / 5))

    main = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(main).multiline_text(
        pos, wrapped, font=font, fill=(255, 255, 255, 255), align="center",
        stroke_width=fs // 12, stroke_fill=(0, 0, 0, 255), spacing=sp)

    out = Image.alpha_composite(Image.alpha_composite(glow, glow), main)
    return cv2.cvtColor(np.array(out), cv2.COLOR_RGBA2BGRA)


def lyric_timeline(lyrics, dur, beats_rel):
    lines = [l.strip() for l in (lyrics or "").splitlines() if l.strip()]
    if not lines:
        return []
    max_chunks = max(1, int(dur / 1.6))
    if len(lines) > max_chunks:
        k = math.ceil(len(lines) / max_chunks)
        lines = ["\n".join(lines[i:i + k]) for i in range(0, len(lines), k)]
    n = len(lines)
    edges = list(np.linspace(0.4, dur - 0.3, n + 1))
    if len(beats_rel):
        for i in range(1, n):
            j = int(np.argmin(np.abs(beats_rel - edges[i])))
            if abs(beats_rel[j] - edges[i]) < 0.45:
                edges[i] = float(beats_rel[j])
    edges = list(np.maximum.accumulate(edges))
    return [(edges[i], edges[i + 1], lines[i]) for i in range(n)]


def blend_bgra(frame, layer, cx, cy, opacity=1.0):
    h, w = layer.shape[:2]
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    fx0, fy0 = max(x0, 0), max(y0, 0)
    fx1, fy1 = min(x0 + w, frame.shape[1]), min(y0 + h, frame.shape[0])
    if fx1 <= fx0 or fy1 <= fy0:
        return
    sub = layer[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    a = sub[..., 3:4].astype(np.float32) / 255.0 * opacity
    reg = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    frame[fy0:fy1, fx0:fx1] = (reg * (1 - a) + sub[..., :3] * a).astype(np.uint8)


# ----------------------------------------------------------------- asosiy
def render_video(bg_path, center_path, music_path, lyrics, fmt, quality, color,
                 out_path, progress=None):
    W, H = FORMATS[(fmt, quality)]
    color_rgb = COLORS.get(color, COLORS["purple"])
    color_bgr = color_rgb[::-1]
    work = tempfile.mkdtemp(prefix="render_")
    try:
        wav = os.path.join(work, "a.wav")
        _decode(music_path, wav)
        a = analyze(wav)
        if a["total"] < MIN_DUR:
            raise ValueError(f"Musiqa juda qisqa ({a['total']:.1f}s). Kamida {int(MIN_DUR)} soniya kerak.")
        n = int(min(a["total"], MAX_DUR) * FPS)
        dur = n / FPS
        start = pick_window(a, dur)
        energy = energy_curve(a, start, n)

        bg = build_bg(bg_path, W, H, color_bgr)
        BH, BW = bg.shape[:2]
        vig = build_vignette(W, H)
        D = int(min(W, H) * 0.42)
        pic, ring_a, ring_b, glow = build_center(center_path, D, color_bgr)
        CS = pic.shape[0]
        c = CS // 2
        cx = W // 2
        cy = int(H * 0.44) if H > W else int(H * 0.45)
        x0 = max(0, min(cx - CS // 2, W - CS))
        y0 = max(0, min(cy - CS // 2, H - CS))
        color_vec = np.array(color_bgr, np.float32)

        fs = int(min(W, H) * 0.075)
        ty = int(H * 0.80) if H > W else int(H * 0.87)
        tl = lyric_timeline(lyrics, dur, a["beats"] - start)
        lyr = [(s, e, text_layer(t, W, fs, color_rgb)) for s, e, t in tl]

        rnd = random.Random(7)
        NP = 45
        px = [rnd.uniform(0, W) for _ in range(NP)]
        py = [rnd.uniform(0, H) for _ in range(NP)]
        sp = [rnd.uniform(25, 110) * H / 720 for _ in range(NP)]
        sz = [rnd.uniform(1.0, 3.2) * W / 720 for _ in range(NP)]
        pcol = tuple(int(0.5 * ch + 127) for ch in color_bgr)

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", music_path,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-maxrate", "10M", "-bufsize", "20M", "-pix_fmt", "yuv420p",
            "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={dur - 0.7:.3f}:d=0.7",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out_path,
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        rng = np.random.default_rng(3)
        spin = 0.0

        try:
            for i in range(n):
                t = i / FPS
                e = float(energy[i])

                # --- orqa fon: zoom + titrash + yorug'lik pulsi
                z = 1.06 + 0.05 * (t / dur) + 0.08 * e
                M = cv2.getRotationMatrix2D((BW / 2, BH / 2), math.sin(t * 0.8), z / 1.3)
                sh = e * e * 0.012 * W
                M[0, 2] += W / 2 - BW / 2 + rng.uniform(-sh, sh)
                M[1, 2] += H / 2 - BH / 2 + rng.uniform(-sh, sh)
                frame = cv2.warpAffine(bg, M, (W, H), flags=cv2.INTER_LINEAR,
                                       borderMode=cv2.BORDER_REFLECT)
                frame = cv2.convertScaleAbs(frame, alpha=0.62 + 0.6 * e)
                frame = cv2.multiply(frame, vig, scale=1 / 255.0)

                # --- zarrachalar
                for k in range(NP):
                    py[k] -= sp[k] / FPS * (1 + 2.5 * e)
                    if py[k] < -10:
                        py[k] = H + 10
                        px[k] = rnd.uniform(0, W)
                    xx = px[k] + math.sin(t * 1.3 + k) * 12
                    cv2.circle(frame, (int(xx), int(py[k])), max(1, int(sz[k] * (1 + e * 1.2))),
                               pcol, -1, cv2.LINE_AA)

                # --- bass urganda glitch
                if e > 0.72:
                    s = int((2 + 10 * (e - 0.72) / 0.28) * W / 720)
                    b, g, r = cv2.split(frame)
                    frame = cv2.merge((np.roll(b, s, axis=1), g, np.roll(r, -s, axis=1)))
                    if e > 0.9 and rng.random() < 0.6:
                        y1 = int(rng.uniform(0, H * 0.9))
                        hh = int(rng.uniform(0.02, 0.07) * H)
                        off = int(rng.uniform(-0.04, 0.04) * W)
                        frame[y1:y1 + hh] = np.roll(frame[y1:y1 + hh], off, axis=1)

                # --- o'rtadagi dumaloq rasm: pulsatsiya, aylanish, nur
                scale = 1.0 + 0.17 * e
                wob = 3.0 * math.sin(t * 2.4) * (0.4 + e)
                spin += 0.7 + 4.5 * e
                dx = rng.uniform(-1, 1) * e * D * 0.012
                dy = rng.uniform(-1, 1) * e * D * 0.012

                def xf(angle, sc):
                    m = cv2.getRotationMatrix2D((c, c), angle, sc)
                    m[0, 2] += dx
                    m[1, 2] += dy
                    return m

                reg = frame[y0:y0 + CS, x0:x0 + CS].astype(np.float32)
                gl = cv2.warpAffine(glow, xf(0, scale), (CS, CS))
                reg += gl[..., None] * color_vec * (0.35 + 1.1 * e)
                for layer, ang, sc in ((ring_b, -spin * 0.7, scale * 1.02),
                                       (ring_a, spin, scale),
                                       (pic, wob, scale)):
                    im = cv2.warpAffine(layer, xf(ang, sc), (CS, CS), flags=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
                    al = im[..., 3:4].astype(np.float32) / 255.0
                    reg = reg * (1 - al) + im[..., :3].astype(np.float32) * al
                frame[y0:y0 + CS, x0:x0 + CS] = np.clip(reg, 0, 255).astype(np.uint8)

                # --- qo'shiq so'zlari
                for s0, s1, layer in lyr:
                    if s0 <= t < s1:
                        fade = min(1.0, (t - s0) / 0.12, (s1 - t) / 0.12)
                        k = 1.0 + 0.07 * e
                        lay = cv2.resize(layer, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR)
                        blend_bgra(frame, lay, W // 2, ty, max(0.0, fade))
                        break

                # --- boshida va oxirida yumshoq fade
                f = min(1.0, t / 0.4, (dur - t) / 0.5)
                if f < 1.0:
                    frame = cv2.convertScaleAbs(frame, alpha=max(f, 0.0))

                proc.stdin.write(frame.tobytes())
                if progress and i % 15 == 0:
                    progress(i / n)
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
        err = proc.stderr.read().decode(errors="ignore")
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg xatosi: " + err[-500:])
        if progress:
            progress(1.0)
        return out_path, W, H, dur
    finally:
        shutil.rmtree(work, ignore_errors=True)
