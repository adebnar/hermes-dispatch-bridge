# Security Policy

## Reporting a vulnerability
Report privately via GitHub Security → Advisories ("Report a vulnerability") on
this repository, or by email to the maintainer. Please don't open public issues
for vulnerabilities. We aim to acknowledge within 72 hours.

## Security posture
The bridge sits between a phone and a self-hosted Hermes agent that can read
mail, files, and more, so it is a sensitive trust boundary.

- **Credential separation.** The webui password lives only in the bridge's
  server-side config (`.env`); the phone holds a separate `BRIDGE_TOKEN`. Rotate
  the token to revoke a device without touching the webui password.
- **Constant-time auth.** Bearer tokens are compared with `secrets.compare_digest`.
- **Auth required by default in practice.** If `BRIDGE_TOKEN` is unset the bridge
  logs a loud warning and runs open — only acceptable on a trusted loopback /
  during development. Always set a token before exposing the bridge.
- **No secrets in the repo.** `.env`, tokens, and keys are git-ignored;
  `.env.example` ships placeholders only.
- **Transport.** Terminate TLS in front of the bridge (Tailscale Serve / reverse
  proxy). Don't expose plain HTTP to the internet.
- **Runs as non-root** in the container; minimal base image.
- **Push is best-effort and one-way** (publish-only to ntfy); a delivery failure
  never affects an in-flight run.

## Scope
Securing the upstream hermes-webui and the Hermes agent (their auth, TLS, and
network exposure) is the operator's responsibility.
