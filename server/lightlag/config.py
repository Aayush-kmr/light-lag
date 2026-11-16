"""Runtime configuration for the LIGHT-LAG universe server.

Every knob is an environment variable so the demo laptop can be configured
without editing code. See `.env.example` at the repo root for the full list.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _envb(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    # --- time & delay -------------------------------------------------------
    # DELAY_SCALE = wall-clock seconds a player waits per second of real light
    # time. 0.1 means a 12-minute light time is a 72-second wait. We implement
    # it by running the universe clock 1/DELAY_SCALE faster than the wall
    # clock, so physics (light, protons, dose) is never distorted - only
    # fast-forwarded. 1.0 = "True Delay" mode.
    delay_scale: float = field(default_factory=lambda: _envf("LL_DELAY_SCALE", 0.1))
    tick_hz: float = field(default_factory=lambda: _envf("LL_TICK_HZ", 10.0))
    broadcast_hz: float = field(default_factory=lambda: _envf("LL_BROADCAST_HZ", 4.0))

    # --- data ---------------------------------------------------------------
    kernel_dir: Path = field(default_factory=lambda: Path(_env("LL_KERNEL_DIR", str(DATA / "kernels"))))
    cache_dir: Path = field(default_factory=lambda: Path(_env("LL_CACHE_DIR", str(DATA / "cache"))))
    ephemeris: str = field(default_factory=lambda: _env("LL_EPHEMERIS", "auto"))  # auto|spice|kepler
    offline: bool = field(default_factory=lambda: _envb("LL_OFFLINE", False))
    nasa_api_key: str = field(default_factory=lambda: _env("NASA_API_KEY", "DEMO_KEY"))

    # --- scenario -----------------------------------------------------------
    scenario: str = field(default_factory=lambda: _env("LL_SCENARIO", "meridian_2020"))
    judge_mode: bool = field(default_factory=lambda: _envb("LL_JUDGE", False))
    # Crew AI (Phase 3): the four crew members act on their own. 0 turns it off.
    crew_ai: bool = field(default_factory=lambda: _envb("LL_CREW_AI", True))
    # Phase 4: the Flight Readiness Review between design and launch (LL_REVIEW=0 launches
    # straight after the vote, as before), and design-to-flight callouts in flight.
    review: bool = field(default_factory=lambda: _envb("LL_REVIEW", True))
    callouts: bool = field(default_factory=lambda: _envb("LL_CALLOUTS", True))
    # Phase 6 upgrades, each behind its own flag: the trans-Mars injection burn after a
    # review pass, "Meanwhile, in reality" lines, and the live light time on the landing page.
    launch_fx: bool = field(default_factory=lambda: _envb("LL_LAUNCH_FX", True))
    reality: bool = field(default_factory=lambda: _envb("LL_REALITY", True))
    right_now: bool = field(default_factory=lambda: _envb("LL_RIGHT_NOW", True))

    # --- network ------------------------------------------------------------
    host: str = field(default_factory=lambda: _env("LL_HOST", "0.0.0.0"))
    # LL_PORT, else PORT (Heroku sets it), else 8765
    port: int = field(default_factory=lambda: int(_envf("LL_PORT", _envf("PORT", 8765))))

    # --- rooms and public hosting (Phase 5) ----------------------------------
    # One universe per room. MAIN always exists (the old single game); others are
    # created from the landing page and evicted when nobody is connected.
    max_rooms: int = field(default_factory=lambda: int(_envf("LL_MAX_ROOMS", 8)))
    room_idle_min: float = field(default_factory=lambda: _envf("LL_ROOM_IDLE_MIN", 30.0))
    rooms_per_ip_hour: int = field(default_factory=lambda: int(_envf("LL_ROOMS_PER_IP_HOUR", 10)))
    # /api/admin/reset: needs this token (header X-Admin-Token). Unset: localhost only.
    admin_token: str = field(default_factory=lambda: _env("LL_ADMIN_TOKEN", ""))
    voice_max_bytes: int = field(default_factory=lambda: int(_envf("LL_VOICE_MAX_BYTES", 2 * 1024 * 1024)))
    voice_max_clips: int = field(default_factory=lambda: int(_envf("LL_VOICE_MAX_CLIPS", 50)))
    # LL_PUBLIC=1: the public-link preset. Local LLMs off unless LL_LLM is set
    # explicitly, and the admin reset needs a token even from localhost.
    public_mode: bool = field(default_factory=lambda: _envb("LL_PUBLIC", False))

    # --- local AI -----------------------------------------------------------
    # Any OpenAI-compatible server works: Ollama (http://127.0.0.1:11434/v1)
    # or llama.cpp's llama-server (http://127.0.0.1:8080/v1).
    tern_url: str = field(default_factory=lambda: _env("LL_TERN_URL", "http://127.0.0.1:11434/v1"))
    tern_model: str = field(default_factory=lambda: _env("LL_TERN_MODEL", "qwen3:4b"))
    fido_url: str = field(default_factory=lambda: _env("LL_FIDO_URL", "http://127.0.0.1:11434/v1"))
    fido_model: str = field(default_factory=lambda: _env("LL_FIDO_MODEL", "qwen3:8b"))
    llm_enabled: bool = field(default_factory=lambda: _envb("LL_LLM", True))
    llm_timeout_s: float = field(default_factory=lambda: _envf("LL_LLM_TIMEOUT", 20.0))
    allow_remote_llm: bool = field(default_factory=lambda: _envb("LL_ALLOW_REMOTE_LLM", False))

    # --- voice --------------------------------------------------------------
    whisper_bin: str = field(default_factory=lambda: _env("LL_WHISPER_BIN", "whisper-cli"))
    whisper_model: str = field(default_factory=lambda: _env("LL_WHISPER_MODEL", str(DATA / "models" / "ggml-base.en.bin")))
    piper_bin: str = field(default_factory=lambda: _env("LL_PIPER_BIN", "piper"))
    piper_voice_tern: str = field(default_factory=lambda: _env("LL_PIPER_VOICE_TERN", str(DATA / "models" / "piper" / "tern.onnx")))
    audio_dir: Path = field(default_factory=lambda: Path(_env("LL_AUDIO_DIR", str(DATA / "runtime" / "rooms" / "MAIN" / "audio"))))

    # --- anomaly model ------------------------------------------------------
    anomaly_model: Path = field(default_factory=lambda: Path(_env("LL_ANOMALY_MODEL", str(DATA / "models" / "anomaly.onnx"))))

    def __post_init__(self) -> None:
        if self.public_mode and "LL_LLM" not in os.environ:
            self.llm_enabled = False

    @property
    def time_rate(self) -> float:
        """Universe seconds per wall second at normal play speed."""
        return 1.0 / max(self.delay_scale, 1e-6)

    def public(self) -> dict:
        return {
            "delay_scale": self.delay_scale,
            "time_rate": self.time_rate,
            "true_delay_mode": abs(self.delay_scale - 1.0) < 1e-9,
            "scenario": self.scenario,
            "judge_mode": self.judge_mode,
            "crew_ai": self.crew_ai,
            "review": self.review,
            "callouts": self.callouts,
            "launch_fx": self.launch_fx,
            "reality": self.reality,
            "right_now": self.right_now,
            "offline": self.offline,
            "ephemeris": self.ephemeris,
            "public": self.public_mode,
            "max_rooms": self.max_rooms,
        }


CONFIG = Config()
