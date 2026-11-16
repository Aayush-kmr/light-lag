# Hosting LIGHT-LAG on a public link

The game is one Python process. Every mission is a **room** with a four-letter
code, so several groups can play at once without touching each other.
None of this is needed for a LAN game: `python run.py` works as before.

Plan: the always-on Mac mini runs the server, a Cloudflare named tunnel
publishes it at `lightlag.byaayushkumar.com`, and Heroku is the backup.

## Mac mini plus Cloudflare Tunnel (primary)

No open ports, free TLS, WebSockets work through the tunnel by default.

1. **Code and Python.** Python 3.11 or newer.

   ```
   cd ~ && git clone https://github.com/Aayush-kmr/light-lag.git && cd light-lag
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Data, once, while online.** NAIF SPICE kernels (about 35 MB), the DONKI
   storm cache and a DSN Now snapshot; then the checklist.

   ```
   python -m tools.fetch_kernels && python -m tools.fetch_donki && python -m tools.fetch_dsn_now
   python -m tools.preflight
   ```
3. **Try it by hand**, bound to loopback so only the tunnel can reach it:

   ```
   LL_PUBLIC=1 LL_HOST=127.0.0.1 LL_ADMIN_TOKEN=<long random string> python run.py
   curl -s http://127.0.0.1:8765/api/health | python -m json.tool
   ```
4. **Tunnel.** `brew install cloudflared`, then:

   ```
   cloudflared tunnel login
   cloudflared tunnel create lightlag            # prints the tunnel id and writes its credentials file
   cloudflared tunnel route dns lightlag lightlag.byaayushkumar.com
   cp deploy/cloudflared-config.example.yml ~/.cloudflared/config.yml   # put in the tunnel id and your user name
   cloudflared tunnel run lightlag               # test; Ctrl-C when the site loads
   ```
5. **Run both as launchd services** that start at login and restart on
   failure (`KeepAlive`). Edit the paths, user name and `LL_ADMIN_TOKEN` in
   `com.lightlag.server.plist` first; check `which cloudflared` matches the
   path in `com.lightlag.tunnel.plist` (`/opt/homebrew/bin` on Apple silicon).

   ```
   cp deploy/com.lightlag.server.plist deploy/com.lightlag.tunnel.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.lightlag.server.plist
   launchctl load ~/Library/LaunchAgents/com.lightlag.tunnel.plist
   ```
   Logs: `/tmp/lightlag.server.log` and `/tmp/lightlag.tunnel.log`. Stop with
   `launchctl unload` on the same files. Keep the Mac awake (System Settings,
   Energy: prevent automatic sleeping).
6. **Check.** Open `https://lightlag.byaayushkumar.com/` and press
   **Watch the 7-minute demo**; `https://lightlag.byaayushkumar.com/api/health`
   should answer `"ok": true`.

**Update after a `git pull`:**

```
cd ~/light-lag && git pull && source .venv/bin/activate && pip install -r requirements.txt
launchctl kickstart -k gui/$(id -u)/com.lightlag.server
```

The tunnel needs no restart. Rooms live in memory, so a restart ends games in
progress; do it between sessions.

**Why loopback matters.** The per-visitor limit on new missions trusts
Cloudflare's `cf-connecting-ip` header. With the server on `127.0.0.1` only the
tunnel can reach it, so nobody can fake that header.

## Heroku (backup)

`Procfile` (`web: LL_PUBLIC=1 python run.py`) and `runtime.txt` are at the repo
root; `run.py` binds to Heroku's `$PORT`. Then:

```
heroku create lightlag-backup
heroku config:set LL_ADMIN_TOKEN=<long random string>
git push heroku main
```

Run one dyno only: rooms live in memory. Heroku's disk is wiped on restart, so
SPICE kernels are not kept; the game then uses its analytic ephemeris and says
so on screen. To keep SPICE, commit the kernels to a deploy branch or fetch them
in a release step.

## Production settings

`LL_PUBLIC=1` is the public preset: local LLMs off unless `LL_LLM=1` is set
explicitly (the deterministic grounded responder answers instead), and the
admin reset always needs the token. Demo rooms run the scripted mission; the
`MAIN` room and new missions are normal games.

| Variable | Default | What it does |
|---|---|---|
| `LL_PUBLIC` | `0` | The public preset above |
| `LL_HOST` | `0.0.0.0` | `127.0.0.1` behind the tunnel |
| `LL_MAX_ROOMS` | `8` | Rooms alive at once, including `MAIN`. When full, the oldest empty room closes; if none is empty, new missions get "All mission rooms are busy, try again in a few minutes." |
| `LL_ROOM_IDLE_MIN` | `30` | Minutes a room may sit with nobody connected before it closes |
| `LL_ROOMS_PER_IP_HOUR` | `10` | New missions one visitor may start per hour |
| `LL_ADMIN_TOKEN` | empty | Header `X-Admin-Token` for `/api/admin/reset`. Empty: reset works from localhost only, and never with `LL_PUBLIC=1` |
| `LL_VOICE_MAX_BYTES` | `2097152` | Largest voice clip (larger: 413) |
| `LL_VOICE_MAX_CLIPS` | `50` | Voice clips kept per room; the oldest are deleted |

## Monitoring

`GET /api/health` returns `ok`, `rooms.live` against `rooms.max`, `rooms.tick_ms`
(the cost of one tick of every room; it must stay far below 100 ms), connected
clients and each room's phase. Point any uptime checker at it. Measured: four
rooms in flight cost about 2 ms a tick.

Reset the shared room from anywhere:

```
curl -X POST -H "X-Admin-Token: $LL_ADMIN_TOKEN" "https://lightlag.byaayushkumar.com/api/admin/reset?judge=1"
```
