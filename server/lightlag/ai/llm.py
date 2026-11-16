"""Minimal OpenAI-compatible chat client for LOCAL model servers.

Works with Ollama (`ollama serve`, base URL http://127.0.0.1:11434/v1) and
llama.cpp (`llama-server --jinja`, base URL http://127.0.0.1:8080/v1).

"Airplane mode": by default the client refuses any base URL that is not
loopback or a private LAN address, so a demo can never silently fall back
to a cloud API. `airplane_check()` additionally reports whether the machine
can reach the public internet at all - the demo shows it as a badge.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import time
from urllib.parse import urlparse

import httpx


class LLMUnavailable(RuntimeError):
    pass


def is_local_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
    except (socket.gaierror, ValueError):
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def airplane_check(timeout: float = 1.0) -> dict:
    """True 'offline' means no route to a public resolver."""
    online = False
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            with socket.create_connection((host, 443), timeout=timeout):
                online = True
                break
        except OSError:
            continue
    return {"internet_reachable": online, "airplane_mode": not online, "checked_at": time.time()}


class LocalLLM:
    def __init__(self, base_url: str, model: str, timeout: float = 20.0, allow_remote: bool = False):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        if not allow_remote and not is_local_url(self.base_url):
            raise LLMUnavailable(f"refusing non-local LLM endpoint {self.base_url} (airplane mode)")
        self._ok: bool | None = None
        self._checked = 0.0

    def available(self) -> bool:
        if self._ok is not None and time.time() - self._checked < 30:
            return self._ok
        try:
            r = httpx.get(self.base_url + "/models", timeout=2.0)
            ids = [m.get("id") for m in r.json().get("data", [])]
            self._ok = r.status_code == 200 and (not ids or any(self.model.split(":")[0] in (i or "") for i in ids))
        except Exception:
            self._ok = False
        self._checked = time.time()
        return self._ok

    def chat(self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2,
             max_tokens: int = 220) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                "stream": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        try:
            r = httpx.post(self.base_url + "/chat/completions", json=body, timeout=self.timeout)
            r.raise_for_status()
        except Exception as exc:
            self._ok = False
            raise LLMUnavailable(str(exc)) from exc
        msg = r.json()["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": tc.get("id", fn.get("name")), "name": fn.get("name"), "args": args})
        return {"content": (msg.get("content") or "").strip(), "tool_calls": calls}
