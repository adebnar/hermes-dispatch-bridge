# Phase 0 — live API capture (2026-06-21)

Captured against a real instance: **hermes-agent v0.16.0** built-in dashboard
(`http://<tailscale-ip>:9119`). This is the agent's own dashboard, **not**
nesquena/hermes-webui — and its API differs from the contract the bridge + app
were initially built against. Source of truth: the live `/openapi.json` (title
"Hermes Agent", v0.16.0) plus the dashboard JS bundle.

## Auth
- Loopback/embedded: a session token is injected into the dashboard HTML
  (`window.__HERMES_SESSION_TOKEN__`) and sent as header
  `X-Hermes-Session-Token` (also accepted as `?token=` on the WS).
- When `__HERMES_AUTH_REQUIRED__` is true: `POST /api/auth/ws-ticket` → ticket,
  used as `?ticket=` on the WS; browser password login is `POST /auth/password-login`.
- `GET /api/auth/me`, `GET /api/auth/providers`.

## REST (real shapes)
- `GET /api/sessions?limit&offset&order` → `{sessions:[{id, source, user_id,
  model, model_config, system_prompt, …}], total, limit, offset}` — **wrapped +
  paginated**, key is `id` (not `session_id`).
- `GET /api/sessions/{id}/messages`, `GET /api/sessions/{id}`, search/stats/export.
- `GET/POST /api/cron/jobs`; `GET/PUT/DELETE /api/cron/jobs/{id}`;
  `…/{id}/pause|resume|trigger|runs`; `…/blueprints`. (Job: `{id, name, prompt,
  …schedule…}` — bare list.) **Not** `/api/crons`.
- `GET/POST /api/mcp/servers` → `{servers:[{name, transport, url, command, args,
  env, enabled, tools}]}` — wrapped; `…/{name}/enabled|test`, `…/catalog`.
- Models: `GET /api/model/info` → `{model, provider, *_context_length,
  capabilities}`; `/api/model/options`, `POST /api/model/set`. **Not** `/api/models`.
- Audio: **`POST /api/audio/transcribe` (STT!)**, `POST /api/audio/speak` (TTS).
- Profiles: `GET/POST /api/profiles`, `/api/profiles/active`.
- Skills, files/fs, config, memory, messaging platforms, kanban plugin, etc.

## Chat / streaming — WebSocket JSON-RPC (NOT SSE) — CONFIRMED LIVE
There is **no** `/api/chat/start` or `/api/chat/stream`. Chat runs over a
WebSocket, backed by `tui_gateway/server.py` (the TUI+dashboard gateway).
Verified end-to-end against v0.16.0 with a live prompt.

- URL: `ws(s)://<host>/api/ws?token=<session_token>` (or `?ticket=<ticket>`).
- Protocol: **JSON-RPC 2.0**: `{jsonrpc:"2.0", id, method, params}`.
- On connect the server pushes a `gateway.ready` notification.

**Flow (verified):**
1. `session.create {source, title, model?, provider?, profile?, reasoning_effort?, messages?(seed), cwd?}`
   → result `{session_id, stored_session_id, message_count, messages, info}`.
2. `prompt.submit {session_id, text, truncate_before_user_ordinal?}`
   → result `{status:"streaming"}`. **This is how a user turn is sent.**
3. Mid-run inject: `session.steer {session_id, text}` → `{status:"queued|rejected"}`.
4. Cancel: `session.interrupt`. Others: `session.resume/list/info/history/
   activate/title/close/delete/undo/compress/branch`.

**Event notifications** — `{"jsonrpc":"2.0","method":"event",
"params":{"type":<t>,"session_id":<sid>,"payload":{…}}}` (envelope from
`_emit()`, server.py:802). Types seen live:
| type | payload | meaning |
|---|---|---|
| `gateway.ready` | `{skin,…}` | connection ready |
| `session.info` | `{model, provider, reasoning_effort, tools, …}` | run started/info |
| `message.start` | `null` | assistant turn begins |
| `thinking.delta` | `{text}` | thinking indicator text |
| `reasoning.delta` | `{text}` | reasoning tokens |
| `reasoning.available` | `{text}` | reasoning summary |
| `message.delta` | `{text}` | **assistant text tokens** |
| `message.complete` | `{text, usage{input,output,…}}` | **terminal: done** |
| `tool.start` / `tool.complete` / `tool.generating` | `{tool_id,name,preview,…}` | tool activity |

- Auxiliary: `/api/events?...&channel=` (event feed) and a PTY WebSocket.

## Adapter mapping (dashboard → bridge `/v1`, app unchanged)
- `message.delta` → `token`; `tool.start/complete/generating` → `tool`/`status`;
  `session.info`/`thinking.delta` → `status`; `message.complete` → `completed`;
  errors/`session.interrupt` → `error`/`interrupted`. (Drop reasoning from push.)
- `stream_id` (app-facing) = the dashboard `session_id`; the hub opens one WS per
  run, does `session.create`(or resume)+`prompt.submit`, and fans out mapped
  frames + push. cancel→`session.interrupt`, steer→`session.steer`.
- REST: `/api/cron/jobs` (+pause/resume/trigger), `/api/mcp/servers` (unwrap),
  `/api/model/info`, and **`/api/audio/transcribe`** gives us a server STT route.

## Implication
The bridge's `HermesClient` (REST + SSE, `/api/sessions` bare array,
`/api/chat/{start,stream}`, `/api/crons`) does **not** match this instance. The
SSE event names the app/bridge parse *do* loosely resemble the `tool.*` payloads,
but transport (WS JSON-RPC vs SSE) and most endpoints differ.

### Two paths (decision required)
1. **Adapt the bridge to the hermes-agent dashboard API** (what the user already
   runs). Rewrite `HermesClient` for the real REST shapes + a JSON-RPC-over-WS
   chat client; map `/api/cron/jobs`, unwrap `{sessions}`/`{servers}`, use
   `/api/audio/transcribe` for STT. No extra service to run; uses the real setup.
2. **Run nesquena/hermes-webui** as a separate front-end to the same agent and
   keep the bridge as-is (it already targets that REST+SSE contract). Faster to a
   working state; costs an extra service.

Recommendation: **Path 1** — it targets the user's actual deployment and unlocks
server STT + profiles for free, at the cost of a WS JSON-RPC client.
