# LHM Robot Desktop — Vision, Sandbox, Parallel Agents

Independent implementation based on general desktop-agent architecture.
No Grok Bot proprietary code, native addon, key, protocol or asset is copied.

## 1. Vision: observe → reason → act → verify

The Windows Electron settings page adds a default-off checkbox authorizing
selected-window screenshots to be sent to a configured OpenRouter free vision
model. The owner must provide a specific model name ending in :free that
actually supports images, plus their own OpenRouter API key. Save and
restart the application.

A Computer Agent proposes a visual observation of an approved browser
page or a selected, allowlisted Windows HWND. The action always enters
the existing human per-step approval queue. After approval, a screenshot
is obtained from the real local runner, sent to the free vision model,
and its bounded description is recorded as an unverified observation.
The next Agent step can reason from that observation.

Screenshots are NEVER stored in the database or audit trail. They
are sent to the provider, which has its own retention policies.
Do not use vision on windows with passwords, payment details,
private contact lists, sensitive customer data or privileged accounts.
Screenshots can be misinterpreted; model supports vary, and offline
CI cannot prove a given model understands vision. Free attempt quotas
and positive-cost guardrails still apply. No paid fallback.

## 2. Isolated code execution: optional Docker

The Electron settings page adds a default-off local Docker sandbox option.
The owner installs Docker Desktop separately and pre-pulls an approved
Linux Python image using the following command:

    docker pull python:3.12-slim

The Agent can propose a sandbox_run action with a bounded argv array.
The action always requires approval. The local runner starts an
ephemeral Docker container with network disabled, --pull never,
read-only root filesystem, cap-drop ALL, no-new-privileges, CPU/RAM/
PID quotas and an isolated Agent files directory as its ONLY bind.
No LHM model, CRM or social credentials are injected. The Docker
image name is fixed; arbitrary image/mount/daemon/socket options
are never accepted from the model.

The host Windows run_command is STILL DISABLED. This sandbox is an
optional coding feature; browser and native UI control work without
Docker. A Docker container is not a microVM. Production should
pin the image digest and validate the Docker security boundary.

## 3. Multiple coworkers in parallel

Electron Settings now lets the owner choose 1, 2, 3 or 4 concurrent
independent AI tasks. Default remains 1 to conserve API quota.
The worker selects one ready task per workspace+role, checks provider
configuration, and dispatches model calls concurrently. Each task
reserves its own budget and free-model attempts under SQLite transactions.

This is PARALLEL AI THINKING/TASK EXECUTION, not four independent
Windows desktops. All Agents share one real mouse and keyboard,
and desktop changes remain step-approved/serialized by the runner.
The multi-Agent chat workflow still hands work between team members
in bounded turns, with real tasks and stored messages.

## 4. Inspecting runtime state

The Computer Console calls the capabilities endpoint, which reports
whether vision opt-in, Docker sandbox, Windows native tools and
parallel worker limit are set, without returning any keys.

## Safety and acceptance

- No screenshot exfiltration without both Electron opt-in and a
  user approval for that specific vision step.
- No shell/Python execution on the Windows host.
- No external website access outside the browser allowlist.
- Model observations are unverified and cannot bypass action approvals.
- Emergency pause blocks NEW mutations, but cannot undo an in-flight action.
- A same-directory Chrome profile is not a separate protected VM.
- No claim of complete Grok Bot, Codex, or commercial SaaS feature parity.
- Automated tests use a synthetic PNG and mocked HTTP provider, not a
  real OpenRouter key or the owner's interactive Windows.
- Windows installer CI validates packaging; native interactive acceptance
  on the owner's logged-in Windows device is still mandatory.
