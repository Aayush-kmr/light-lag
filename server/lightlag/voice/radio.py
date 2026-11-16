"""Voice pipeline: local speech-to-text, local text-to-speech, radio filter.

* STT: whisper.cpp CLI (`whisper-cli`, called `main` in older builds) with a
  ggml model, e.g. ggml-base.en.bin. Input is converted to 16 kHz mono WAV
  with ffmpeg first.
* TTS: Piper (`piper --model voice.onnx --output_file out.wav`).
* Radio filter: band-limit to the 300-3000 Hz voice channel, soft-clip like
  an overdriven transmitter, add a little hiss, and frame the clip with
  Quindar tones - the 2525 Hz (start) and 2475 Hz (end), 250 ms tones that
  keyed the Apollo transmitters and became the sound of spaceflight.

Everything degrades gracefully: missing binaries -> the client falls back
to typed messages and the browser's local speechSynthesis.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path

import numpy as np

SR = 16000


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    x = np.frombuffer(raw, dtype=np.int16 if sw == 2 else np.uint8).astype(np.float32)
    if sw != 2:
        x = (x - 128) * 256
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x / 32768.0, sr


def _write_wav(path: Path, x: np.ndarray, sr: int = SR) -> None:
    y = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())


def tone(freq: float, ms: float, sr: int = SR, amp: float = 0.35) -> np.ndarray:
    t = np.arange(int(sr * ms / 1000)) / sr
    env = np.minimum(1, np.minimum(t, t[::-1]) / 0.005)
    return amp * env * np.sin(2 * np.pi * freq * t)


def radio_filter(x: np.ndarray, sr: int = SR, seed: int = 0, quindar: bool = True) -> np.ndarray:
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    mask = 1.0 / (1 + (300 / np.maximum(f, 1)) ** 4) / (1 + (f / 3000) ** 4)
    y = np.fft.irfft(X * mask, n=len(x))
    y = np.tanh(3.0 * y / (np.max(np.abs(y)) + 1e-9)) * 0.6
    rng = np.random.default_rng(seed)
    y = y + 0.012 * rng.standard_normal(len(y))
    if quindar:
        gap = np.zeros(int(0.05 * sr))
        y = np.concatenate([tone(2525, 250, sr), gap, y, gap, tone(2475, 250, sr)])
    return y.astype(np.float32)


class VoiceStore:
    def __init__(self, cfg, directory: Path | None = None, max_clips: int | None = None):
        self.cfg = cfg
        self.dir = Path(directory or cfg.audio_dir)      # Phase 5: one folder per room, made on first upload
        self.max_clips = max_clips if max_clips is not None else int(getattr(cfg, "voice_max_clips", 50))

    def prune(self) -> list[str]:
        """Keep the newest `max_clips` clips; delete every file of the older ones."""
        clips: dict[str, float] = {}
        if not self.dir.exists():
            return []
        for f in self.dir.glob("*.*"):
            aid = f.name.split(".", 1)[0]
            clips[aid] = max(clips.get(aid, 0.0), f.stat().st_mtime)
        old = sorted(clips, key=clips.get, reverse=True)[self.max_clips:]
        for aid in old:
            for f in self.dir.glob(f"{aid}.*"):
                f.unlink(missing_ok=True)
        return old

    def capabilities(self) -> dict:
        return {"ffmpeg": bool(shutil.which("ffmpeg")),
                "whisper": bool(shutil.which(self.cfg.whisper_bin)) and Path(self.cfg.whisper_model).exists(),
                "piper": bool(shutil.which(self.cfg.piper_bin)) and Path(self.cfg.piper_voice_tern).exists()}

    def path(self, aid: str, kind: str = "radio") -> Path:
        return self.dir / f"{aid}.{kind}.wav"

    def ingest(self, data: bytes, filename: str = "clip.webm") -> dict:
        """Store an uploaded clip, transcribe it, and render the radio version."""
        aid = uuid.uuid4().hex[:12]
        caps = self.capabilities()
        self.dir.mkdir(parents=True, exist_ok=True)
        src = self.dir / f"{aid}.src{Path(filename).suffix or '.webm'}"
        src.write_bytes(data)
        self.prune()
        clean = self.path(aid, "clean")
        if caps["ffmpeg"]:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(SR), str(clean)],
                           check=False, timeout=60)
        if not clean.exists():
            return {"id": aid, "ok": False, "error": "could not decode audio (ffmpeg missing?)", "transcript": None}
        x, sr = _read_wav(clean)
        _write_wav(self.path(aid), radio_filter(x, sr))
        return {"id": aid, "ok": True, "seconds": round(len(x) / sr, 2), "bytes": len(data),
                "transcript": self.transcribe(clean) if caps["whisper"] else None,
                "stt": "whisper.cpp" if caps["whisper"] else "none (type the transcript)"}

    def transcribe(self, wav: Path) -> str | None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "t"
            cmd = [self.cfg.whisper_bin, "-m", self.cfg.whisper_model, "-f", str(wav), "-otxt", "-of", str(out), "-np"]
            try:
                subprocess.run(cmd, check=True, timeout=120, capture_output=True)
                return (out.with_suffix(".txt")).read_text().strip()
            except Exception:
                return None

    def speak(self, text: str) -> dict:
        """TERN's voice via Piper; returns an audio id or None."""
        if not self.capabilities()["piper"]:
            return {"id": None, "tts": "browser speechSynthesis"}
        aid = uuid.uuid4().hex[:12]
        self.dir.mkdir(parents=True, exist_ok=True)
        clean = self.path(aid, "clean")
        try:
            subprocess.run([self.cfg.piper_bin, "--model", self.cfg.piper_voice_tern, "--output_file", str(clean)],
                           input=text.encode(), check=True, timeout=60, capture_output=True)
            x, sr = _read_wav(clean)
            _write_wav(self.path(aid), radio_filter(x, sr, quindar=False), sr)
            return {"id": aid, "tts": "piper"}
        except Exception as e:
            return {"id": None, "tts": f"piper failed: {e}"}


def quindar_demo_wav() -> bytes:
    """A 1.5 s test clip: Quindar in, filtered noise 'voice', Quindar out."""
    rng = np.random.default_rng(1)
    t = np.arange(int(SR * 1.0)) / SR
    fake = 0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) + 0.05 * rng.standard_normal(len(t))
    buf = io.BytesIO()
    y = radio_filter(fake.astype(np.float32))
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()
