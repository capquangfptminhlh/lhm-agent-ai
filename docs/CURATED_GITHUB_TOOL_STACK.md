# Curated open-source GitHub tool stack — 16 LHM agents

**Audit date: 2026-10-28.** This is a sourced selection of 19 repositories spanning social publishing, public research, logged-in browser automation, workflow orchestration, memory, observability, computer vision, CRM, support and coding. It is not a claim that all GitHub repositories were inspected or all selected tools are installed. The GitHub catalog lives at `templates/github_tool_stack.json`.

## What is actually shipped in this update?

- **New:** `GET /agents/github-stack` and `/agents/github-stack/ui`, a view of 19 curated repositories and their explicit roles and implementation maturity. Electron includes a new **Kho công cụ GitHub** page.
- **New:** a bounded **Agent-Reach v1.6 public-search pilot** tool, `agent_reach_search`, in the shared Computer Agent plan/act loop. Tool args are exactly `{"channel":"github|youtube|bilibili","query":"2–180 characters"}`.
- **Enforced:** disallowed roles and channels fail in model-time/tool-time and again in the isolated Computer Runner. Bound Work Orders for Content and Quality may use the new read-only tool; Sales cannot. Non-pilot Research/Strategy/Lead/Ads/Analytics/Compliance may use it when appropriately configured.
- **Safety:** no `bash` / `sh`, no arbitrary CLI flags, URLs, login, cookies, install/update, web writes, credentials or GitHub repository mutations from this tool. Uses `asyncio.create_subprocess_exec`, 25-second cap, bounded JSON, a minimal environment without OpenRouter/runner tokens, and a distinct runtime HOME per workspace/role. The provider output is explicitly **unverified outside information**, not instructions or a completed Work Order.
- **Pinned Linux Runner installation:** the Computer Runner Docker image now installs the official Agent-Reach v1.6.0 GitHub wheel (SHA-256 verified against GitHub's release asset digest), with a separate unprivileged workspace/role HOME. It never imports existing Chrome sessions or runs `agent-reach setup`. Windows native runtime still rejects the third-party CLI to protect the user's desktop.
- **Existing adapters reused, not forked/duplicated:** Postiz publisher/provider read-back, WordPress, Activepieces workflows, Crawl4AI, ComfyUI, Twenty CRM, Chatwoot and Playwright.
- **Selected but not connected in this update:** OpenCLI (logged-in browser), Browser Use (fallback), LangGraph (durable execution), Mem0 (scoped memory), MCP Context Forge (authorization). Build each behind explicit owner configuration, secrets, provider verification and CI rather than enabling arbitrary subprocesses.

## How to use Agent-Reach public search

1. Build the separate Linux Runner with `docker compose --profile computer build computer`. `Dockerfile.computer` downloads the official **[Agent-Reach v1.6.0](https://github.com/Panniantong/Agent-Reach/releases/tag/v1.6.0)** wheel with pinned SHA-256 `79455eafd94302561aa5f52b0722a0fc359279c3170a27c6990486027fd56830`, then installs it. This is NOT the unrelated PyPI project of the same name. If source/digest differs the image build fails.
2. In the isolated non-root runner, run `agent-reach version` and `agent-reach channels`; CI now validates these inside the built Docker image with **no network**. A real online GitHub/YouTube/Bilibili search still depends on access to the external platform, rate limits and whether the selected public backend works; a Docker build passing is not proof of all three live searches. The pilot never imports Chrome session cookies.
3. Start Computer Runner, API backend, Worker and the model. The Computer Agent `TOOLS` list exposes `agent_reach_search` only to supported roles. For public search the model may choose `agent_reach_search` through the existing Computer Run flow; it receives a bounded JSON observation and must check original sources before reporting evidence.
4. For a fixed Content/Quality Work Order, start its Computer Pilot as documented in `docs/GROKBOT_SHARED_RUNTIME_PILOT.md`. Continue following all sensitive action approval and publication verification rules.
5. Inspect `/agents/github-stack/ui` for selected tools and maturity. Adding a repo to the catalog is **never equivalent** to executing/connecting/deploying it.

### Decision: OpenCLI versus Agent-Reach

- **Agent-Reach** is a CLI packaging read/search actions on multiple platforms. It explicitly does **not** post/comment/like/message. This makes it an early candidate for narrowly scoped research.
- **OpenCLI** supports a Browser Bridge and interacting with an authorized logged-in Chrome profile, including clicks/fills. That has much greater privileges. It remains **selected_not_connected** until a dedicated profile, per-action human approval, channel restrictions, audit/screenshot policy and desktop owner test exist.
- **Postiz** remains the preferred direct API/official OAuth publication path. It is already connected at *code* level in LHM, but actual accounts/credentials are not verified. Public publishing always requires the owner's approval, provider identity check and an independent read-back receipt.

### Rights and security

- Always review each repository's actual source files and **current** licensing terms; license notes like `Check source edition` and model weights mean they have NOT been cleared for commercial embedding. Postiz is AGPL-3.0; integrating its API does not mean copying its server source into LHM.
- Public GitHub star count is not a security signal. Do not run README one-liner remote installers as root or agent-supplied untrusted commands.
- A shared Computer Runner process with workspace/role directories is **not VM-level tenant isolation**. For strict commercial isolation use separate containers/VMs with network egress policies and separate secrets/profile mounts.
- Platform readback is not proof that anonymous users can see a post, and an API HTTP 200 is not the same as published. Verification must be grounded per provider/URL.

## Next integration gates (ranked)

1. **Agent-Reach live smoke in the separately deployed Linux runner.** Public search with real process output, no credentials.
2. **Postiz live authorized Content→QA→publish→verify** on one connected owner-selected channel and media.
3. **OpenCLI** in a dedicated browser profile, explicitly authorized, without sharing the owner's default Chrome cookies; supervised browser action trace.
4. **LangGraph** only if current SQLite Work Order recovery proves insufficient; preserve existing DB audit semantics rather than rewrite architecture.
5. **Mem0 + Langfuse** after scoped memory consent/retention rules, per-Agent cost attribution and data hygiene have been validated.
6. Expand capability binding and isolation to the remaining 13 roles, with objective verifier and provider-specific action receipts.

## 2026-10-28 operational hardening of Agent-Reach

- Previously the LHM bridge tried to read `result` from an Agent-Reach 1.6 **search** envelope. The real schema uses `items`, so content was silently `null` even with a successful subprocess. Fixed: require exact schema/channel/action, retain at most five sanitized entries and discard `raw` payloads to keep the Computer SQLite observation valid.
- An empty role-specific browser hostname allowlist now explicitly denies browser navigation, even if the global list contains that host; this repairs a sidecar fail-open condition. Work Orders that only search public GitHub/YouTube/Bilibili can start without any browser hostname permission.
- The Linux runner health endpoint reports `agent_reach_cli_installed`. This says only whether the executable is present, not that a specific provider is up or that a social account is connected.
- **No live account tests yet:** the build/install and API readback are verified in CI, but the owner has not connected real social accounts or run an end-to-end public search with external network evidence here. These are separate release gates.
