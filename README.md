# Hermes Dispatch Bridge

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=for-the-badge)](LICENSE)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20the%20project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/andrew65386)

> 💜 **Find this useful?** [**☕ Support development on Ko-fi →**](https://ko-fi.com/andrew65386)

The **mobile bridge sidecar** for [Hermes Dispatch](https://github.com/adebnar/hermes-dispatch-android).
It sits next to your self-hosted Hermes agent (fronting the **hermes-agent
dashboard** — REST + JSON-RPC-over-WebSocket; verified against v0.17.0, see
[`docs/PHASE0-FINDINGS.md`](docs/PHASE0-FINDINGS.md)) and gives the Android app
what a phone can't do alone:

- **Server-held runs** — the bridge owns the agent's SSE stream, so a task keeps
  running and reporting even when your phone is asleep or offline.
- **Background push** — progress and completion are POSTed to an **ntfy** topic
  (UnifiedPush), driving the app's lock-screen "live update".
- **Cron classification** — a new task that "sounds recurring" (e.g. *"every
  Monday at 9am…"*) is turned into a scheduled job instead of a one-shot run.
- **One token for the phone** — the bridge holds the dashboard session token
  server-side; the app only carries a `BRIDGE_TOKEN`.
- **Document attach** — proxies a picked file to the dashboard's managed-files
  area and references its path in the prompt, so the agent can read PDFs/CSVs/text.
- **Mobile-shaped `/v1` API** — stable shape decoupled from dashboard internals.

> Status: early development. The `/v1` API and the dashboard adapter are
> validated end-to-end against a live hermes-agent v0.17.0 dashboard (REST +
> WebSocket chat). Pure logic (cron classification, event mapping, push) is
> unit-tested.

## API (`/v1`, Bearer-authenticated)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness (+ whether push/auth are configured) |
| GET | `/v1/auth/check` | Validate the bridge token |
| GET | `/v1/info` | Bridge + gateway (Hermes) versions, for the app's About panel |
| GET | `/v1/tasks` | List tasks (sessions), each with a derived run-state |
| POST | `/v1/tasks` | Start a task → one-shot run (held server-side) **or** a cron; accepts `images` + `attachments` |
| GET | `/v1/tasks/{id}/events` | Foreground SSE for a held run (buffered + replayed) |
| GET | `/v1/tasks/{id}/live` | Whether a run is actively streaming (lets the app reattach) |
| GET | `/v1/tasks/{id}/messages` | Conversation history for a task |
| POST | `/v1/tasks/{id}/{cancel\|steer\|approve\|clarify}` | Control a running task |
| POST | `/v1/files/upload` | Upload a document to the dashboard's managed files; returns its path |
| GET | `/v1/models` · POST `/v1/models/set` | List / set the profile's model |
| GET | `/v1/schedules` | List scheduled jobs |
| POST | `/v1/schedules/{id}/{pause\|resume\|run\|delete}` | Control a job |
| POST | `/v1/push/register` · `/v1/push/unregister` | Manage a device's push endpoint |
| GET | `/v1/mcp` | List connected MCP servers |

Background push fans out to every **registered device endpoint** plus an
optional statically-configured `NTFY_TOPIC`.

## Setup

Split into **🧑 what a human does** and **🤖 what you can ask your Hermes agent to build**.

### 🧑 Human
1. Have the **hermes-agent dashboard** running (`hermes dashboard` / the gateway
   web UI, default port 9119), reachable by this bridge.
2. Copy `.env.example` → `.env` and fill it in. **Generate the secrets yourself**
   (locally) — don't have an assistant create or store them:
   - `HERMES_URL` — your dashboard, e.g. `http://127.0.0.1:9119`.
   - `HERMES_TOKEN` — **leave empty** for a local dashboard (the bridge auto-reads
     `window.__HERMES_SESSION_TOKEN__`); set it only for a remote/authenticated one.
   - `BRIDGE_TOKEN` — what the app authenticates with. Generate a strong one:
     ```bash
     python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # or: openssl rand -base64 32
     ```
   - `NTFY_BASE_URL` + `NTFY_TOPIC` (for push) — use an **unguessable** topic:
     ```bash
     echo "hermes-$(python3 -c "import secrets; print(secrets.token_hex(8))")"
     ```
3. Run it:
   ```bash
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8099   # or: docker compose up -d
   ```
4. In the Android app, point the bridge URL at this service and use the `BRIDGE_TOKEN`.

### 🤖 Ask Hermes
> ⚠️ **Generate your own secrets.** `BRIDGE_TOKEN` and `NTFY_TOPIC` are yours to
> create (step 2) — don't ask the agent to generate or write them. The prompts
> below are safe because they don't touch your secrets.

> "Add this bridge to my existing docker-compose so it starts alongside my Hermes dashboard and ntfy, on the same network, with a healthcheck on `/health`. Leave my `.env` and secrets to me."
>
> "Improve the cron classifier: create a Hermes skill `classify_schedule` that, given a task description, returns `{is_cron, cron}` and call it from the bridge instead of the regex heuristic."

## The bridge URL your phone uses

### HTTPS (recommended) — Tailscale Serve
Give the bridge a real TLS cert (valid Let's Encrypt, auto-renewed) on your tailnet:
```bash
./scripts/enable-https.sh
# or directly:  tailscale serve --bg 8099
```
This proxies `https://<your-machine>.<tailnet>.ts.net` → `http://127.0.0.1:8099`.
Use that **https URL** in the app — the phone validates the cert with the system
trust store (no special config). Requires "HTTPS Certificates" enabled for your
tailnet in the Tailscale admin console. To turn it off: `tailscale serve --https=443 off`.

### Plain HTTP (LAN / Tailscale IP)
The bridge listens on **port 8099**. If you skip Serve, point the app at `http://<this-machine>:8099`:
```bash
tailscale ip -4          # encrypted transport → http://100.x.y.z:8099
ipconfig getifaddr en0   # macOS LAN  → http://192.168.x.y:8099
hostname -I              # Linux LAN  (first address)
ipconfig                 # Windows LAN → the IPv4 Address of your active adapter
```

The token to enter in the app is your `BRIDGE_TOKEN`:
```bash
grep BRIDGE_TOKEN .env
```

## Run on boot

Ready-to-use templates are in [`deploy/`](deploy/). The cross-platform option is
**Docker** — `docker compose up -d` (with `restart: unless-stopped`) starts the
bridge on boot on any OS; the native per-OS recipes below are for non-Docker setups.

### macOS (launchd)
```bash
sed "s|__BRIDGE_DIR__|$(pwd)|g" deploy/co.hermesdispatch.bridge.plist \
  > ~/Library/LaunchAgents/co.hermesdispatch.bridge.plist
launchctl load ~/Library/LaunchAgents/co.hermesdispatch.bridge.plist
launchctl list | grep hermesdispatch    # status 0 = healthy
```
Runs `scripts/run-bridge.sh` (uvicorn on `0.0.0.0:8099`), restarts on crash, starts
at login. Logs to `bridge.log`.

### Linux (systemd)
A user service template is in [`deploy/hermes-dispatch-bridge.service`](deploy/hermes-dispatch-bridge.service):
```bash
mkdir -p ~/.config/systemd/user
sed "s|__BRIDGE_DIR__|$(pwd)|g" deploy/hermes-dispatch-bridge.service \
  > ~/.config/systemd/user/hermes-dispatch-bridge.service
systemctl --user daemon-reload
systemctl --user enable --now hermes-dispatch-bridge
systemctl --user status hermes-dispatch-bridge      # active = healthy
loginctl enable-linger "$USER"                       # start at boot without login
journalctl --user -u hermes-dispatch-bridge -f       # logs
```
`run-bridge.sh` calls `uv` from your `PATH`; if `uv` lives in `~/.local/bin` and
isn't on the service PATH, set `Environment=UV_BIN=%h/.local/bin/uv` in the unit
(a commented line is included). Prefer a **system** service (`/etc/systemd/system/`,
with `User=`)? Drop the `--user`/linger steps and run as root.

### Windows
The simplest path is **Docker Desktop** or **WSL2** (use the Linux steps inside
WSL). To run natively without Docker:
1. Install Python 3.12 + `uv`, then in the repo: `uv venv && uv pip install -e .`
2. Start it: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8099`
3. Auto-start on boot — either:
   - **Task Scheduler** → *Create Task* → trigger "At log on" / "At startup",
     action `uv` with args `run uvicorn app.main:app --host 0.0.0.0 --port 8099`,
     *Start in* = the repo folder; or
   - **[NSSM](https://nssm.cc)** to run it as a real Windows service:
     `nssm install HermesDispatchBridge uv "run uvicorn app.main:app --host 0.0.0.0 --port 8099"`
     then set *Startup directory* to the repo and start the service.

> The `scripts/*.sh` helpers (`enable-https.sh`, `run-bridge.sh`) are bash and run on
> macOS/Linux/WSL. On native Windows use the commands directly, and for HTTPS run
> `tailscale serve --bg 8099` (the Tailscale client is cross-platform).

## Develop

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
uv run ruff check .
uv run pytest -q
```

Branches: **`main`** is stable; **`development`** is active work (paired with the app's beta pre-releases). PRs target `development`.

## Support this project
If this is useful to you, please consider supporting development:

**[☕ Buy me a coffee on Ko-fi →](https://ko-fi.com/andrew65386)** — thank you! 🙏

## License
[GPL-3.0](LICENSE) © Andrew Debnar. Free software under the GNU General Public License v3.
