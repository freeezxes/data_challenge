"""Голос оператора по записи звонка: темп, паузы, громкость, интонация.

Claude звук не принимает, поэтому запись разбирает код. Запись — один канал, но у каждой
реплики расшифровки есть время начала: отрезок от начала реплики до начала следующей
приписываем её автору. Это даёт метрики именно оператора, а не всего звонка.

Метрики — справочные для проверяющего и в баллы чек-листа не входят: эталон размечен
по расшифровке, и подмешивать звук в оценку без отдельной проверки точности нельзя.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

from cqa.facts import WAIT_WARNING

SR = 16000
HOP = 160            # 10 мс — шаг для громкости и речевой активности
PITCH_WIN = 640      # 40 мс — окно для высоты голоса
PITCH_HOP = 320      # 20 мс
F0_MIN, F0_MAX = 70, 400
LONG_SILENCE_S = 8.0  # заметная тишина в звонке


class VoiceUnavailable(RuntimeError):
    pass


def _np():
    try:
        import numpy as np
        return np
    except ImportError as exc:
        raise VoiceUnavailable("для анализа голоса нужен пакет numpy: pip install -r requirements.txt") from exc


def decode(path: Path):
    """mp3/wav → моно 16 кГц float32. miniaudio (колесо без зависимостей), иначе ffmpeg."""
    np = _np()
    try:
        import miniaudio
        d = miniaudio.decode_file(str(path), output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1, sample_rate=SR)
        return np.asarray(d.samples, dtype=np.float32)
    except ImportError:
        pass
    if shutil.which("ffmpeg"):
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    raise VoiceUnavailable("для анализа голоса нужен пакет miniaudio (pip install -r requirements.txt) или ffmpeg")


def fetch_audio(src: str, public: Path, cache: Path) -> Path:
    """Локальная запись из public/ или скачанная по audio_url (кэшируется на диске)."""
    if src.startswith("/"):
        p = (public / src.lstrip("/")).resolve()
        if public.resolve() not in p.parents or not p.is_file():
            raise VoiceUnavailable("файл записи не найден")
        return p
    cache.mkdir(parents=True, exist_ok=True)
    p = cache / (hashlib.sha1(src.encode()).hexdigest()[:16] + ".audio")
    if not p.exists():
        try:
            with urllib.request.urlopen(src, timeout=60) as r:
                p.write_bytes(r.read())
        except Exception as exc:
            raise VoiceUnavailable(f"запись не скачалась: {exc}") from exc
    return p


def _frames_db(x):
    np = _np()
    n = len(x) // HOP
    rms = np.sqrt((x[: n * HOP].reshape(n, HOP) ** 2).mean(1) + 1e-12)
    return 20 * np.log10(rms)


def _voiced(db):
    """Речь — кадры заметно громче фона; короткие провалы заливаем, короткие всплески убираем."""
    np = _np()
    floor = np.percentile(db, 15)
    v = db > floor + 10
    v = _close(v, 15)   # провалы до 150 мс внутри речи
    v = _open(v, 8)     # щелчки короче 80 мс
    return v


def _runs(mask):
    np = _np()
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _close(mask, gap):
    m = mask.copy()
    runs = _runs(~m)
    for a, b in runs:
        if 0 < a and b < len(m) and b - a <= gap:
            m[a:b] = True
    return m


def _open(mask, size):
    m = mask.copy()
    for a, b in _runs(m):
        if b - a < size:
            m[a:b] = False
    return m


def _pitch(x, mask_fn):
    """Высота голоса автокорреляцией по окнам 40 мс; только там, где mask_fn(время) истинно."""
    np = _np()
    n = (len(x) - PITCH_WIN) // PITCH_HOP
    if n <= 0:
        return np.array([]), np.array([])
    idx = np.arange(PITCH_WIN)[None, :] + PITCH_HOP * np.arange(n)[:, None]
    times = (np.arange(n) * PITCH_HOP + PITCH_WIN / 2) / SR
    keep = mask_fn(times)
    if not keep.any():
        return np.array([]), np.array([])
    fr = x[idx[keep]]
    fr = fr - fr.mean(1, keepdims=True)
    fr *= np.hanning(PITCH_WIN)[None, :]
    spec = np.fft.rfft(fr, 2 * PITCH_WIN, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :PITCH_WIN]
    ac /= ac[:, :1] + 1e-9
    lo, hi = SR // F0_MAX, SR // F0_MIN
    lag = lo + np.argmax(ac[:, lo:hi], axis=1)
    strength = ac[np.arange(len(lag)), lag]
    ok = strength > 0.6  # только уверенно периодичные кадры
    t, f0 = times[keep][ok], SR / lag[ok]
    if len(f0) < 20:
        return t, f0
    # Октавные ошибки: всё, что дальше полуоктавы от медианы, — подгоняем на октаву или выкидываем.
    med = np.median(f0)
    f0 = np.where(f0 > med * 1.6, f0 / 2, np.where(f0 < med / 1.6, f0 * 2, f0))
    good = (f0 > med / 1.5) & (f0 < med * 1.5)
    t, f0 = t[good], f0[good]
    if len(f0) >= 5:  # медианное сглаживание по 5 кадрам
        pad = np.pad(f0, 2, mode="edge")
        f0 = np.median(np.stack([pad[i:i + len(f0)] for i in range(5)]), axis=0)
    return t, f0


def _words(text):
    text = re.sub(r"\[[^\]]*\]", " слово ", text)  # «[Имя скрыто]» — прозвучавшее слово
    return len(re.findall(r"[\w'-]+", text))


def analyze(conv: dict, audio_path: Path) -> dict:
    np = _np()
    x = decode(audio_path)
    dur = len(x) / SR
    db = _frames_db(x)
    voiced = _voiced(db)
    ft = np.arange(len(db)) * HOP / SR

    # Метки расшифровки совпадают с началом речи в записи примерно до секунды (проверено на DEV),
    # поэтому сдвиг не подбираем: в звонках с музыкой на удержании подбор неустойчив.
    msgs = [m for m in conv["messages"] if m["role"] in ("operator", "client", "bot")]
    best_off = 0.0

    # Отрезки реплик: от начала реплики до начала следующей реплики с голосом.
    segs = []
    for i, m in enumerate(msgs):
        a = max(0.0, m["t"] + best_off)
        nxt = msgs[i + 1]["t"] + best_off if i + 1 < len(msgs) else dur
        # не дольше, чем можно произносить столько слов даже медленно: иначе в отрезок попадает удержание с музыкой
        b = min(nxt, a + 1.0 + _words(m["text"]) * 0.75)
        b = min(max(b, a + 0.3), dur)
        if a < dur:
            segs.append({"id": m["id"], "role": m["role"], "start": round(a, 2), "end": round(b, 2), "text": m["text"]})

    def seg_mask(role):
        mk = np.zeros(len(db), dtype=bool)
        for s in segs:
            if s["role"] == role:
                mk[int(s["start"] * 100): int(s["end"] * 100)] = True
        return mk

    op_mask, cl_mask = seg_mask("operator") & voiced, seg_mask("client") & voiced

    # Темп: слова оператора на секунды его речи.
    turns = []
    for s in segs:
        if s["role"] != "operator":
            continue
        a, b = int(s["start"] * 100), int(s["end"] * 100)
        sec = voiced[a:b].sum() / 100
        w = _words(s["text"])
        rate = w / sec * 60 if sec >= 1.5 and w >= 4 else None
        # вне 60–320 слов/мин — почти наверняка ошибка разметки отрезка (реплика разбита или слита), не речь
        turns.append({"id": s["id"], "words": w, "speech_s": round(float(sec), 1),
                      "wpm": round(rate) if rate and 60 <= rate <= 320 else None})
    tw = sum(t["words"] for t in turns if t["wpm"])
    ts = sum(t["speech_s"] for t in turns if t["wpm"])
    wpm = round(tw / ts * 60) if ts >= 5 else None

    # Громкость оператора относительно клиента (запись одна, так что сравнение честнее абсолютных цифр).
    op_db = float(np.median(db[op_mask])) if op_mask.sum() > 50 else None
    cl_db = float(np.median(db[cl_mask])) if cl_mask.sum() > 50 else None

    # Интонация: разброс высоты голоса оператора в полутонах.
    pt, f0 = _pitch(x, lambda t: op_mask[np.clip((t * 100).astype(int), 0, len(op_mask) - 1)])
    if len(f0) > 40:
        med = float(np.median(f0))
        st = 12 * np.log2(f0 / med)
        spread = float(np.std(st))  # разброс высоты голоса, полутоны
    else:
        med, spread = None, None

    # Тишина: длинные паузы без речи; предупредил ли оператор об ожидании перед ней.
    silences = []
    for a, b in _runs(~voiced):
        sa, sb = a / 100, b / 100
        if sb - sa >= LONG_SILENCE_S and sa > 1 and sb < dur - 1:
            before = [m for m in conv["messages"] if m["role"] == "operator" and m["t"] + best_off <= sa + 0.5]
            warned = bool(before and WAIT_WARNING.search(before[-1]["text"]))
            silences.append({"start": round(float(sa), 1), "end": round(float(sb), 1), "dur": round(float(sb - sa), 1),
                             "after_id": before[-1]["id"] if before else None, "warned": warned})

    # Волна для интерфейса: 1200 столбиков, 0–100.
    bins = 1200
    edges = np.linspace(0, len(x), bins + 1).astype(int)
    peaks = [float(np.abs(x[edges[i]:edges[i + 1]]).max()) if edges[i + 1] > edges[i] else 0.0 for i in range(bins)]
    top = np.percentile(peaks, 99) or 1.0
    peaks = [int(min(100, p / top * 100)) for p in peaks]
    pitch_line = []
    if med:
        step = max(dur / 600, 0.25)
        for i in range(int(dur / step)):
            sel = (pt >= i * step) & (pt < (i + 1) * step)
            pitch_line.append(round(float(np.median(f0[sel])), 1) if sel.sum() >= 3 else None)

    def label_wpm(v):
        return None if v is None else "медленно" if v < 110 else "быстро" if v > 175 else "обычный темп"

    def label_spread(v):
        return None if v is None else "ровная, почти монотонная" if v < 1.5 else "живая" if v < 4 else "очень подвижная"

    return {
        "duration": round(dur, 1), "peaks": peaks, "pitch": pitch_line,
        "pitch_step_s": round(max(dur / 600, 0.25), 3) if med else None,
        "segments": [{k: v for k, v in s.items() if k != "text"} for s in segs],
        "operator": {
            "wpm": wpm, "wpm_label": label_wpm(wpm), "turns": turns,
            "loudness_gap_db": round(op_db - cl_db, 1) if op_db is not None and cl_db is not None else None,
            "pitch_median_hz": round(med) if med else None,
            "intonation_range_st": round(spread, 1) if spread is not None else None, "intonation_label": label_spread(spread),
            "talk_share": round(float(op_mask.sum() / max(1, (op_mask | cl_mask).sum())), 2),
        },
        "silences": silences,
        "method": "Считает код: речь — по громкости кадров 10 мс, чья реплика — по меткам времени расшифровки "
                  "(точность около секунды), высота голоса — автокорреляцией. Справочно, в баллы чек-листа не входит.",
    }


def voice_report(conv: dict, src: str, public: Path, cache_dir: Path) -> dict:
    path = fetch_audio(src, public, cache_dir / "audio")
    key = hashlib.sha1((conv["conversation_id"] + str(path.stat().st_size) +
                        json.dumps([m["t"] for m in conv["messages"]])).encode()).hexdigest()[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{key}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    rep = analyze(conv, path)
    cached.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return rep
