"""Real Windows packaged-executable integration test (no mocked backend).

Launches the exact PyInstaller binary shipped inside the installer,
starts actual FastAPI and native processes and tests authenticated HTTP,
free-only status, repeated UI routes, pause, and process shutdown.
No OpenRouter requests or paid model credentials are made available.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "dist" / "lhm-runtime" / "lhm-runtime.exe"


def request(url: str, token: str = "", method: str = "GET", data=None, timeout=3):
    headers = {"Authorization": "Bearer " + token} if token else {}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=body, method=method, headers=headers), timeout=timeout) as resp:
            return resp.status, resp.read(200_000).decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, ""


def wait_up(url, token, process, seconds=45):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Runtime exited unexpectedly: {process.returncode}")
        try:
            code, payload = request(url, token)
            if code == 200:
                return
            last = f"HTTP {code}: {payload[:200]}"
        except (URLError, TimeoutError, OSError) as exc:
            last = str(exc)
        time.sleep(0.45)
    raise RuntimeError(f"Service not ready: {url} ({last})")


def stop(proc):
    if proc.poll() is not None:
        return
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   capture_output=True, timeout=12, check=False)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def main():
    if os.name != "nt" or not BINARY.is_file():
        raise RuntimeError("Require packaged runtime EXE on Windows runner")
    with tempfile.TemporaryDirectory(prefix="lhm-e2e-") as folder:
        directory = Path(folder)
        env = os.environ.copy()
        env.update({
            "LHM_DB_PATH": str(directory / "agent.sqlite3"),
            "LHM_ADMIN_TOKEN": "e2e-admin-token",
            "LHM_COMPUTER_RUNNER_TOKEN": "e2e-runner-token",
            "LHM_COMPUTER_HOME": str(directory / "files"),
            "LHM_LLM_PROVIDER": "openrouter",
            "OPENROUTER_MODEL": "openrouter/free",
            "LHM_OPENROUTER_FREE_ONLY": "1",
            "LHM_OPENROUTER_FREE_AUTO_SELECT": "1",
            "LHM_DESKTOP_NATIVE": "1",
            "OPENROUTER_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "LHM_ALLOW_PRO": "0",
        })
        processes = []
        handles = []
        try:
            for mode in ["native", "api"]:
                logfile = open(directory / f"{mode}.log", "wb")
                handles.append(logfile)
                processes.append(subprocess.Popen([str(BINARY), mode], env=env,
                                                 cwd=str(directory), stdout=logfile,
                                                 stderr=subprocess.STDOUT))
            native, api = processes
            wait_up("http://127.0.0.1:7782/health", "e2e-runner-token", native)
            wait_up("http://127.0.0.1:7770/health", "e2e-admin-token", api)
            paths = ["/health", "/office3d", "/team-chat", "/computer",
                     "/workflow-builder", "/tools/catalog",
                     "/agent-tools", "/llm/providers",
                     "/api/office3d/snapshot"]
            latencies = []
            for _ in range(3):
                for path in paths:
                    began = time.monotonic()
                    status, payload = request("http://127.0.0.1:7770" + path,
                                              "e2e-admin-token")
                    latencies.append(time.monotonic() - began)
                    assert status == 200, f"{path} => {status}: {payload[:300]}"
                    assert payload, f"empty response at {path}"
            status, body = request("http://127.0.0.1:7770/llm/providers",
                                   "e2e-admin-token")
            info = json.loads(body)
            assert info["active_model"] == "openrouter/free", info
            assert info["provider"] == "openrouter", info
            assert not info["providers"]["deepseek"]["configured"], info
            assert info["no_paid_fallback"] is True, info
            status, body = request("http://127.0.0.1:7782/health")
            assert status in (401, 403), "Native health exposed without token"
            status, body = request("http://127.0.0.1:7782/v1/pause",
                                   "e2e-runner-token", "POST", {"paused": True})
            assert status == 200 and json.loads(body).get("paused") is True, body
            assert max(latencies) < 5, "Local app route latency exceeded 5 seconds"
            print("PACKAGED WINDOWS E2E PASS: 2 real EXEs, 27 HTTP reads, "
                  "free-only, native auth and emergency pause. "
                  f"worst_route_ms={max(latencies)*1000:.0f}", flush=True)
            # Real GUI acceptance drives actual Notepad/Explorer through
            # the source runner on this same Windows desktop session.
            # Kept as a subprocess so its processes/env stay isolated
            # from the packaged runtime under test above.
            gui = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "windows_gui_acceptance.py")],
                cwd=str(ROOT), timeout=200, check=False)
            assert gui.returncode == 0, "Real Windows GUI acceptance failed"
        except Exception:
            for handle in handles:
                handle.flush()
            for mode in ["native", "api"]:
                log = directory / f"{mode}.log"
                if log.exists():
                    print(f"--- {mode} service log tail ---\n"
                          + log.read_text(encoding="utf-8", errors="replace")[-3000:],
                          flush=True)
            raise
        finally:
            for proc in reversed(processes):
                stop(proc)
            for handle in handles:
                handle.close()


if __name__ == "__main__":
    main()
