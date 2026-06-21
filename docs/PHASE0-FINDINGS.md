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

## Chat / streaming — WebSocket JSON-RPC (NOT SSE)
There is **no** `/api/chat/start` or `/api/chat/stream`. Chat runs over a
WebSocket:

- URL: `ws(s)://<host>/api/ws?token=<session_token>` (or `?ticket=<ticket>`).
- Protocol: **JSON-RPC 2.0**. Requests:
  `{jsonrpc:"2.0", id, method, params}`. Observed request methods:
  `session.create` (`{close_on_disconnect, source, profile?}`), `session.info`,
  `model.options`, `config.set`.
- Server pushes **notifications** as `{method:"event", params:{type, payload}}`.
  Observed event `type`s: `dashboard.new_session_requested`, `tool.start`
  (`{tool_id, name, context}`), `tool.progress` (`{name, preview}`),
  `tool.complete` (`{tool_id, …}`). (Assistant text / token + terminal events
  exist too; full enumeration TODO from the live socket.)
- Also: `/api/events?...&channel=` (event feed) and a PTY WebSocket (terminal).

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
