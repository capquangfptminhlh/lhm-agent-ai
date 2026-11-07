"""Hosted Windows end-to-end: Agent uses real UIA/Win32 to type Vietnamese
and saves an exact UTF-8 file through a disposable WinForms application.

Only the LLM planner is scripted. Authenticated Runner, per-step approval,
real keyboard/mouse, WinForms file saving and SQLite auditing are real.
No owner machine or paid model is used; not P4 T01 OWNER_WINDOWS_PASS.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VIETNAMESE = (
    "Xin chào, đây là bài nghiệm thu số một. "
    "Tôi đang kiểm tra khả năng gõ tiếng Việt có dấu và lưu tệp."
)

SOURCE = r"""
using System;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;

public class LhmTextSaveFixture : Form {
    public LhmTextSaveFixture() {
        Text = "LHM QA UNSAVED";
        Width = 710;
        Height = 330;
        StartPosition = FormStartPosition.Manual;
        Location = new Point(170, 140);
        var editor = new TextBox {
            Name = "lhm_qa_editor",
            AccessibleName = "LHM QA EDITOR",
            Multiline = true,
            Location = new Point(28, 35),
            Size = new Size(630, 155),
            TabIndex = 0
        };
        var save = new Button {
            Name = "lhm_qa_save",
            AccessibleName = "LHM CI SAVE",
            Text = "LHM CI SAVE",
            Location = new Point(35, 210),
            Size = new Size(210, 58),
            TabIndex = 1,
            UseVisualStyleBackColor = true
        };
        save.Click += (sender, args) => {
            var target = Environment.GetEnvironmentVariable("LHM_QA_OUTPUT_PATH");
            if (String.IsNullOrEmpty(target)) throw new Exception("Missing QA path");
            File.WriteAllText(target, editor.Text, new UTF8Encoding(false));
            Text = "LHM QA SAVED";
        };
        Controls.Add(editor);
        Controls.Add(save);
        Shown += (sender, args) => editor.Focus();
    }

    [STAThread]
    public static void Main() {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new LhmTextSaveFixture());
    }
}
"""


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Real Windows GUI text/save acceptance needs a Windows runner")

    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    compilers = [
        windir / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        windir / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    ]
    csc = next((path for path in compilers if path.is_file()), None)
    if csc is None:
        raise AssertionError("Windows C# compiler missing")

    with tempfile.TemporaryDirectory(prefix="lhm-real-gui-vietnamese-") as tmp:
        base = Path(tmp)
        source = base / "LhmTextSaveFixture.cs"
        program = base / "LhmTextSaveFixture.exe"
        target = base / "bai1.txt"
        source.write_text(SOURCE, encoding="utf-8")
        subprocess.run(
            [str(csc), "/nologo", "/target:winexe",
             "/reference:System.Windows.Forms.dll",
             "/reference:System.Drawing.dll",
             "/out:" + str(program), str(source)],
            check=True, capture_output=True, text=True, timeout=100,
        )
        env = {
            "LHM_DESKTOP_NATIVE": "1",
            "LHM_DESKTOP_WORKSPACE": "demo",
            "LHM_DESKTOP_ALLOWED_APPS": "lhmtextsavefixture.exe",
            "LHM_ADMIN_TOKEN": "",
            "LHM_COMPUTER_RUNNER_TOKEN": "unicode-gui-ci-token",
            "LHM_COMPUTER_URL": "http://127.0.0.1:7782",
            "LHM_COMPUTER_HOME": str(base / "sandbox"),
            "LHM_COMPUTER_TRUSTED_ENABLED": "0",
            "LHM_COMPUTER_AUTO_WRITE": "0",
            "LHM_QA_OUTPUT_PATH": str(target),
        }
        with patch.dict(os.environ, env):
            proc = subprocess.Popen([str(program)], cwd=str(base))
            try:
                _accept(base, target)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def _accept(base: Path, output: Path) -> None:
    import httpx
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    import app
    import computer_agent as agent
    import computer_executor as executor
    import core

    previous_db = core.DATABASE
    core.DATABASE = base / "real-gui.sqlite3"
    executor.PAUSED = False
    try:
        core.setup()
        agent.prepare_db()
        api = TestClient(app.app)
        token = {"Authorization": "Bearer unicode-gui-ci-token"}

        async def real_runner(run: dict, tool: str, args: dict) -> dict:
            # Uses actual Windows native actions with Runner's auth and
            # workspace isolation. Only HTTP transport is in-process.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=executor.runner),
                base_url="http://local-runner",
            ) as client:
                response = await client.post(
                    "/v1/tool", headers=token,
                    json={"workspace_id": run["workspace_id"],
                          "role": run["role"], "run_id": run["id"],
                          "tool": tool, "args": args},
                )
            if response.status_code != 200:
                raise HTTPException(502, "Real Windows Runner rejected " + tool)
            return response.json()

        planned = []

        async def plan(run: dict, steps: list[dict]):
            n = len(steps)
            if n == 0:
                action = ("windows_list", {})
            elif n == 1:
                matches = [
                    w for w in steps[0]["result"]["windows"]
                    if w.get("process") == "lhmtextsavefixture.exe"
                    and w.get("title") == "LHM QA UNSAVED"
                ]
                assert len(matches) == 1, matches
                action = ("windows_snapshot", {"hwnd": matches[0]["hwnd"]})
            else:
                snapshot = steps[1]["result"]
                window = snapshot["window"]
                hwnd = window["hwnd"]
                controls = snapshot["controls"]
                editors = [c for c in controls
                           if c.get("type") == "Edit" and c.get("center")]
                saves = [c for c in controls
                         if c.get("type") == "Button"
                         and c.get("label") == "LHM CI SAVE"
                         and c.get("automation_id")]
                assert len(editors) == 1, editors
                assert len(saves) == 1, saves
                if n == 2:
                    action = ("windows_activate", {"hwnd": hwnd})
                elif n == 3:
                    action = ("windows_click", {"hwnd": hwnd,
                              **editors[0]["center"]})
                elif n == 4:
                    action = ("windows_type", {"hwnd": hwnd,
                                              "text": VIETNAMESE})
                elif n == 5:
                    save = saves[0]
                    action = ("windows_click_element", {
                        "hwnd": hwnd,
                        "automation_id": save["automation_id"],
                        "control_type": save["type"],
                        "label": save["label"]})
                elif n == 6:
                    action = ("windows_snapshot", {"hwnd": hwnd})
                elif n == 7:
                    latest = steps[6]["result"]
                    assert latest["window"]["title"] == "LHM QA SAVED", latest
                    action = ("finish", {
                        "summary": "Giao diện đã lưu; file thực cần được đối chiếu độc lập.",
                        "evidence": "WinForms title changed after approved UIA save"})
                else:
                    raise AssertionError("Unbounded model action sequence")
            planned.append(action[0])
            return action

        created = api.post("/workspaces/demo/computer/runs", json={
            "role": "research",
            "objective": "Gõ tiếng Việt đúng dấu qua GUI và lưu vào tệp QA",
            "mode": "supervised",
            "max_steps": 12,
            "idempotency_key": "real-gui-vietnamese-file-20261107",
        })
        assert created.status_code == 201, created.text
        rid = created.json()["id"]
        tick = "/computer/runs/" + rid + "/tick"
        action_order = [
            "windows_list", "windows_snapshot", "windows_activate",
            "windows_click", "windows_type", "windows_click_element",
            "windows_snapshot"
        ]
        receipts = []
        with patch.object(agent, "runner_call", new=real_runner), \
             patch.object(agent, "_next_action", new=plan):
            for tool in action_order:
                response = api.post(tick)
                assert response.status_code == 200, response.text
                payload = response.json()
                if payload["status"] == "awaiting_approval":
                    sid = payload["step_id"]
                    # Verify no output file can be created before save approval.
                    if tool == "windows_click_element":
                        assert not output.exists(), "File was saved without owner approval"
                    approved = api.post(f"/computer/runs/{rid}/steps/{sid}/approve")
                    assert approved.status_code == 200, approved.text
                    response = api.post(tick)
                    assert response.status_code == 200, response.text
                    payload = response.json()
                assert payload["last_tool"] == tool, (tool, payload)
                assert payload["status"] == "queued", (tool, payload)
                receipts.append(payload)
            finished = api.post(tick)
            assert finished.status_code == 200, finished.text
            assert finished.json()["status"] == "completed_unverified", finished.text

        deadline = time.monotonic() + 5
        while not output.is_file() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert output.is_file(), "Approved GUI Save did not create the expected file"
        actual = output.read_bytes()
        expected = VIETNAMESE.encode("utf-8")
        assert actual == expected, (
            "GUI file contents differ from exact Vietnamese UTF-8 text: "
            f"expected={len(expected)} bytes, actual={len(actual)} bytes"
        )
        view = api.get("/computer/runs/" + rid)
        assert view.status_code == 200
        data = view.json()
        assert [s["tool"] for s in data["steps"]] == action_order, data["steps"]
        assert all(s["status"] == "completed" for s in data["steps"])
        assert data["steps"][4]["result"]["typed"] is True
        assert data["steps"][5]["result"]["clicked"] is True
        assert data["steps"][5]["result"]["_verification"]["ok"] is None
        assert data["steps"][6]["result"]["window"]["title"] == "LHM QA SAVED"
        with core.db() as connection:
            audit = connection.execute(
                "SELECT COUNT(*) FROM events WHERE workspace_id=? AND task_id=?",
                ("demo", rid)).fetchone()[0]
        assert audit >= 4, "Missing persistent approval/action audit"
        assert planned == action_order + ["finish"], planned
        print(
            "REAL HOSTED WINDOWS UTF-8 GUI FILE E2E PASS: "
            "7 native/Runner steps, manual approvals, UIA Save click, "
            "exact Vietnamese UTF-8 bytes on disk, SQLite audit, "
            "completed_unverified (NOT owner P4 T01)"
        )
    finally:
        core.DATABASE = previous_db
        executor.PAUSED = False


if __name__ == "__main__":
    main()
