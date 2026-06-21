# Hermes Dispatch Bridge

The **mobile bridge sidecar** for [Hermes Dispatch](https://github.com/adebnar/hermes-dispatch-android).
It sits next to your self-hosted Hermes agent (fronting the **hermes-agent
dashboard** — REST + JSON-RPC-over-WebSocket; verified against v0.16.0, see
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
- **Mobile-shaped `/v1` API** — stable shape decoupled from dashboard internals.

> Status: early development. The `/v1` API and the dashboard adapter are
> validated end-to-end against a live hermes-agent v0.16.0 dashboard (REST +
> WebSocket chat). Pure logic (cron classification, event mapping, push) is
> unit-tested.

## API (`/v1`, Bearer-authenticated)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness (+ whether push/auth are configured) |
| GET | `/v1/auth/check` | Validate the bridge token |
| GET | `/v1/tasks` | List tasks (sessions) |
| POST | `/v1/tasks` | Start a task → one-shot run (held server-side) **or** a cron |
| GET | `/v1/tasks/{stream_id}/events` | Foreground SSE for a held run |
| POST | `/v1/tasks/{stream_id}/cancel` | Cancel a running task |
| POST | `/v1/tasks/{stream_id}/steer` | Send a mid-run message |
| GET | `/v1/schedules` | List scheduled jobs |
| POST | `/v1/schedules/{id}/{pause\|resume\|run\|delete}` | Control a job |
| POST | `/v1/push/register` | Register this device's push endpoint (ntfy URL) |
| POST | `/v1/push/unregister` | Remove a device endpoint |
| GET | `/v1/mcp` | List connected MCP servers |

Background push fans out to every **registered device endpoint** plus an
optional statically-configured `NTFY_TOPIC`.

## Setup

Split into **🧑 what a human does** and **🤖 what you can ask your Hermes agent to build**.

### 🧑 Human
1. Have the **hermes-agent dashboard** running (`hermes dashboard` / the gateway
   web UI, default port 9119), reachable by this bridge.
2. Copy `.env.example` → `.env` and fill in: `HERMES_URL`, `HERMES_TOKEN`
   (the dashboard's `window.__HERMES_SESSION_TOKEN__`), a strong random
   `BRIDGE_TOKEN`, and (for push) `NTFY_BASE_URL` + `NTFY_TOPIC`.
3. Run it:
   ```bash
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8099   # or: docker compose up -d
   ```
4. In the Android app, point the bridge URL at this service and use the `BRIDGE_TOKEN`.

### 🤖 Ask Hermes
> "Generate a strong random `BRIDGE_TOKEN` and an unguessable `NTFY_TOPIC`, and write them into a `.env` for hermes-dispatch-bridge alongside my `HERMES_URL`/`HERMES_TOKEN`."
>
> "Add this bridge to my existing docker-compose so it starts alongside my Hermes dashboard and ntfy, on the same network, with a healthcheck on `/health`."
>
> "Improve the cron classifier: create a Hermes skill `classify_schedule` that, given a task description, returns `{is_cron, cron}` and call it from the bridge instead of the regex heuristic."

## Develop

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
uv run ruff check .
uv run pytest -q
```

## License
[Apache-2.0](LICENSE).
