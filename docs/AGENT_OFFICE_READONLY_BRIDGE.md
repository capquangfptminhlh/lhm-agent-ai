# Agent Office → LHM bridge (Windows, read-only pilot)

Upstream: https://github.com/AgentSystemLabs/agent-office (MIT).
This integration is LHM-owned code; we **do not** bundle, fork, install or run the upstream application.

## What is actually implemented

- `GET /agent-office`: local worker monitoring UI; link from `/office3d` and the Electron navigation.
- `GET /api/agent-office/workers`: fetches **only one authorized Agent Office floor** through upstream's documented internal hook route `GET /office/workers?worker=<id>`.
- Reports real worker `working`, `needs_input`, `offline` etc., with floor/repo/branch; returns explicit `not_configured`, `incomplete_configuration`, `invalid_configuration`, `authorization_failed`, `unavailable` states.
- Only whitelisted status fields are shown; no shell transcript, token, user account or cross-floor worker secrets. Result is separate from LHM's SQLite task completion.
- No process spawn, install script, agent hire, task send, shell, PR merge, publishing, payments, token spending or other mutation.
- No claim that Agent Office workers are LHM's 16 fixed-role agents, or that any external work has been verified.
- Does not change the existing OpenRouter Free-only policy or any LHM security gate.

## Requirements

1. On **Windows**, install Node.js 20+, Git, GitHub CLI and an agent CLI only if you independently want to run the upstream product. Review its install code before use. Official upstream install options are in its README; cloning and reviewing source before `npm install` is preferable in a trusted development environment.
2. Run Agent Office and create an authorized **agent** worker on the desired project floor. Agent Office exposes its read/write worker control hook to that worker's identity with 3 environment values: `AGENT_OFFICE_HOOK_URL`, `AGENT_OFFICE_WORKER_ID`, and `AGENT_OFFICE_HOOK_TOKEN`.
3. Configure **only the LHM backend process environment**, with corresponding `LHM_AGENT_OFFICE_HOOK_URL`, `LHM_AGENT_OFFICE_WORKER_ID`, `LHM_AGENT_OFFICE_HOOK_TOKEN`. These are sensitive per-worker credentials. Do **not** commit them, paste into the chat, place in a browser query string, or expose in the Electron renderer. LHM never displays the token. This manual credential exchange is a pilot limitation; normal LHM starts without any of these values and shows `not_configured`.
4. Start the LHM backend and open `http://127.0.0.1:7770/agent-office` from its authenticated local Electron session. Press **Kiểm tra lại**. The remote Agent Office hook is a different loopback port from LHM and may change on restart.
5. If Agent Office's worker is stopped, revoked, or changes floor, connection status must be treated as unverified until access is re-established.

### Security / upstream compatibility

- The bridge **only** accepts `http://127.0.0.1:<port>` where port is 1024–65535, with no path/query/credentials/fragment. It rejects external IPs, DNS names, `localhost`, HTTPS redirects and malformed URL inputs. It does **not** follow redirects or proxies.
- Upstream's hook token is powerful enough to **control** workers even though LHM uses it only for GET. Never copy a privileged user session into an untrusted application or share it across tenants.
- A user with permission to change backend process environment may gain control of the upstream Office. This is local, single-owner pilot software, not a safe hosted multi-tenant connector.
- Backend requests are limited to a 3-second timeout and 256 KB response, and render at most 64 workers. Frontend uses `textContent`, not `innerHTML`.
- LHM's own API middleware still enforces local access/admin auth.
- No usage or price estimate is transferred; Agent Office has its own provider-specific metering, not necessarily free-only. Do not assume that spinning up workers would cost $0.

## Acceptance

```bash
python -m unittest tests.test_agent_office_bridge -v
```

Automated tests use an HTTP mock. They verify fail-closed config, SSRF guard, per-worker bearer authorization, projection without sensitive fields, upstream 401/503/302, frontend-only read path, and LHM auth. They do not prove a live Agent Office installation or an end-to-end Windows execution.

**Next separate phase:** negotiate a narrow bidirectional integration contract and owner approval before allowing worker creation/task handoff. Require: full owner-configured provider cost policy, workspace separation, per-action scopes, audit receipts, one-worktree-per-coding-task, idempotency, and Windows owner-machine acceptance. Do not piggyback on the broad Agent Office hook token for an unrestricted CEO agent.
