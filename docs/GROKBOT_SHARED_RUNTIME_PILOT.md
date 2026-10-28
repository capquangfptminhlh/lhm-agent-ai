# GrokBot-style shared runtime — 3-agent pilot (Content, Sales, Quality)

**Release level:** local pilot. This is real backend/worker/compiler integration and deterministic provider read-back, **not** a proven Facebook/Zalo live publishing demo. The rest of the 16 roles still use the existing fixed-work-order + Tool Hub pipeline and are not automatically Computer-RBAC-bound.

## Design

- One **shared Computer Agent** (existing Planner → tool request → runner action → observation → next step), not 16 LLM server processes.
- A fixed **Work Order** is bound to exactly one Computer Run; role is taken from the database, not supplied by the model. Pilot agents: `content`, `sales`, `quality`.
- A bounded **tool capability allowlist** is injected into the model prompt **and validated at executor time** both before waiting for approval and immediately before performing a previously approved step.
- Browser profiles and files remain `workspace/role` scoped in the Computer Runner. For pilot browsers, requested URLs and all subresource loads must be on both the **global allowlist** and the **role allowlist**. This is logical separation, **NOT** a VM/container isolation guarantee. The same runner process retains OS-level access to its mounted volume.
- Browser click/fill always require per-step human approval; changing content or posting to a social site never becomes automatic simply because a role is called `content`.
- Exact-repeat browser writes that were already attempted/completed are blocked; failed read-only observations are limited to **two retries** before escalation.
- Shared Work Order reporting is derived from real task, QA, Computer Run and Tool Job records. This does not yet implement full automatic job chaining on every failure or a dedicated durable execution state/events table.

## Role permissions

| Role | Computer capabilities in this pilot | Other systems |
| --- | --- | --- |
| Content | Browser navigate/snapshot/scroll/screenshot/click/fill; list/read role files | Postiz via approved Tool Hub, with per-channel whitelist; WordPress via existing Hub |
| Sales | Browser navigate/snapshot/scroll/screenshot; list/read role files | Twenty CRM via existing approved Tool Hub |
| Quality | Browser navigate/snapshot/scroll/screenshot; list/read role files | QA review and provider read-back; no browser mutation |

All **three** profiles have read-only file access. None can execute terminal commands, use Windows full desktop, write files or submit arbitrary HTTP calls through the bound Computer Pilot. Credentials remain outside prompts and outside Browser payloads.

## Setup on the owner's local machine

1. Start the standard LHM Agent OS desktop Backend + Worker and supply the model key through existing local configuration.
2. Set `LHM_COMPUTER_ALLOWED_HOSTS` to the approved HTTPS destinations, e.g. `example.com`. Configure role-specific lists too: `LHM_COMPUTER_CONTENT_ALLOWED_HOSTS`, `LHM_COMPUTER_SALES_ALLOWED_HOSTS`, `LHM_COMPUTER_QUALITY_ALLOWED_HOSTS`. The allowed set is the **intersection**. For deployment, put the same `.env` settings in the API service AND Computer Runner/Windows local process.
3. Configure the existing Postiz account under `LHM_POSTIZ_API_KEY` or self-host its API. Use `GET /tools/postiz/channels` to find **real connected channel IDs**. Grant only approved targets by setting `LHM_CONTENT_POSTIZ_ALLOWED_CHANNEL_IDS` in the API service `.env`. Never paste API keys into chat or the web UI.
4. Open **Runtime 3 Agent Pilot** in Electron or `http://127.0.0.1:7770/agents/runtime-pilot/ui`.
5. Create a Content Work Order. The existing Worker will create the AI draft if the model is configured. For computer-based execution start its bounded Computer Run; inspect and approve each sensitive Computer step on the Computer screen.
6. Once the draft is **actually completed**, request independent Quality review and wait for a recorded pass on the exact draft digest. Choose real Postiz channel IDs and media upload IDs and create the **social-handoff** Tool Job. The backend refuses unknown channel IDs and foreign-role Work Orders.
7. The owner approves the Tool Job. The separate Execute action calls Postiz **once**. The response is **`accepted_unverified`**, not “published”. On an ambiguous timeout, inspect Postiz before retrying to avoid posting twice.
8. Press **Verify**. The read-only verifier calls the Postiz `GET /posts` API, matches **every original post ID**, checks each is `PUBLISHED` and has a valid HTTPS `releaseURL`. Only then does Tool Hub record `verified_published`. If any channel is absent, DRAFT, ERROR, has the wrong channel, or lacks a release URL, it remains unverified.

**Evidence quality:** Postiz read-back is verified independently of AI claims, but is **not equivalent** to anonymously opening each actual social-platform URL and checking images. These deeper checks and automated screenshots require further authorized browser access/QA. WordPress already has a stricter authenticated-status + anonymous-same-origin HTTP 200 verification path.

## API and status

- `GET /agents/runtime-pilot` — capability policy; no credentials.
- `GET /agents/runtime-pilot/ui` — operator screen.
- `POST /workspaces/{ws}/agent-work-orders/{id}/computer-pilot` — deduplicated scoped Computer Run (supervised mode).
- `POST /workspaces/{ws}/agent-work-orders/{id}/social-handoff` — Content only, whitelist channel(s), existing QA-digest check, owner approval not bypassed.
- `GET /workspaces/{ws}/agent-work-orders/{id}/execution` — real source/computer/provider statuses, one derived lifecycle stage: `queued | planning | executing | verifying | needs_human | completed | failed`.
- `POST /tool-jobs/{id}/approve` and `/execute` — existing Tool Hub human gate and one-shot external call.
- `POST /tool-jobs/{id}/verify` — WordPress or Postiz read-back, no AI generated verdict.

## Acceptance and limitations

Offline CI mocks the external Postiz network boundary but uses real route handlers, SQLite, QA rows, Tool Hub approval transitions and Computer policy. Verify that unit tests, Docker smoke, Windows package and local authenticated Postiz smoke checks pass before calling this production ready.

Not yet present: a real Postiz credential/account to test live, an existing Chrome social session imported into the role browser, a per-agent encrypted credential vault, strict tenant VM isolation, autonomous API-vs-browser planner with complete fallback, screenshot-on-every-action, automated all-channel anonymous reachability checks, automatic retry orchestration for 10 posts, SaaS billing and 16-role Computer policy. **Never state that these are completed until live evidence is available.**
