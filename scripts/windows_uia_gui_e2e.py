"""Real Windows UI Automation smoke against a throwaway WinForms application.

Unlike mocked unit tests, this opens a native window on the GitHub Windows
desktop, observes its real accessibility tree, and clicks an observed
automation_id through LHM's own Windows controller. No model, passwords,
accounts, browser or external targets are used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE = r"""
using System;
using System.Drawing;
using System.Windows.Forms;

public class LhmUiaFixture : Form {
    public LhmUiaFixture() {
        Text = "LHM UIA BEFORE";
        Width = 450;
        Height = 250;
        StartPosition = FormStartPosition.Manual;
        Location = new Point(160, 130);
        var button = new Button {
            Name = "lhm_ci_action",
            AccessibleName = "LHM CI APPLY",
            Text = "LHM CI APPLY",
            Location = new Point(50, 55),
            Size = new Size(220, 65),
            UseVisualStyleBackColor = true
        };
        button.Click += (sender, args) => { Text = "LHM UIA AFTER"; };
        Controls.Add(button);
    }

    [STAThread]
    public static void Main() {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new LhmUiaFixture());
    }
}
"""


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Real UIA smoke requires a Windows CI runner")
    from fastapi import HTTPException
    import windows_native as native

    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    compilers = [
        windir / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        windir / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    ]
    csc = next((p for p in compilers if p.is_file()), None)
    if csc is None:
        raise AssertionError("Windows .NET Framework C# compiler unavailable")

    with tempfile.TemporaryDirectory(prefix="lhm-uia-real-") as directory:
        root = Path(directory)
        source = root / "LhmUiaFixture.cs"
        binary = root / "LhmUiaFixture.exe"
        source.write_text(SOURCE, encoding="utf-8")
        subprocess.run([
            str(csc), "/nologo", "/target:winexe",
            "/reference:System.Windows.Forms.dll",
            "/reference:System.Drawing.dll",
            "/out:" + str(binary), str(source),
        ], check=True, timeout=50, capture_output=True, text=True)

        prior_native = os.environ.get("LHM_DESKTOP_NATIVE")
        prior_apps = os.environ.get("LHM_DESKTOP_ALLOWED_APPS")
        os.environ["LHM_DESKTOP_NATIVE"] = "1"
        os.environ["LHM_DESKTOP_ALLOWED_APPS"] = "lhmuiafixture.exe"
        process = subprocess.Popen([str(binary)], cwd=str(root))
        try:
            deadline = time.monotonic() + 25
            hwnd = None
            while time.monotonic() < deadline:
                candidates = native.execute("windows_list", {})["windows"]
                matches = [w for w in candidates
                           if w["process"] == "lhmuiafixture.exe"
                           and w["title"] == "LHM UIA BEFORE"]
                if matches:
                    hwnd = matches[0]["hwnd"]
                    break
                time.sleep(0.25)
            if hwnd is None:
                raise AssertionError("Native WinForms fixture window not visible")

            snap = native.execute("windows_snapshot", {"hwnd": hwnd})
            matches = [item for item in snap["controls"]
                       if item["type"] == "Button"
                       and item["label"] == "LHM CI APPLY"
                       and item.get("automation_id")]
            if len(matches) != 1:
                raise AssertionError("One visible UIA Button with stable automation_id required: "
                                     + json.dumps(snap["controls"], ensure_ascii=False)[:1000])
            target = matches[0]
            if "center" not in target:
                raise AssertionError("UIA Button has no in-window click center")
            args = {
                "hwnd": hwnd,
                "automation_id": target["automation_id"],
                "control_type": target["type"],
                "label": target["label"],
            }
            invalid = dict(args, label="NOT A REAL BUTTON")
            try:
                native.execute("windows_click_element", invalid)
            except HTTPException as error:
                if error.status_code != 409:
                    raise
            else:
                raise AssertionError("Stale UIA label unexpectedly clicked")
            if native.execute("windows_snapshot", {"hwnd": hwnd})["window"]["title"] != "LHM UIA BEFORE":
                raise AssertionError("Stale label changed target window")

            receipt = native.execute("windows_click_element", args)
            if receipt.get("clicked") is not True or receipt.get("verified_effect") is not False:
                raise AssertionError("Unexpected native UIA click receipt")
            changed = False
            for _ in range(30):
                snap = native.execute("windows_snapshot", {"hwnd": hwnd})
                if snap["window"]["title"] == "LHM UIA AFTER":
                    changed = True
                    break
                time.sleep(0.2)
            if not changed:
                raise AssertionError("Real native UIA click did not update fixture window")
            print("REAL WINDOWS UIA PASS: observed element, denied stale label, clicked "
                  "unique real control, verified native window title changed")
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if prior_native is None:
                os.environ.pop("LHM_DESKTOP_NATIVE", None)
            else:
                os.environ["LHM_DESKTOP_NATIVE"] = prior_native
            if prior_apps is None:
                os.environ.pop("LHM_DESKTOP_ALLOWED_APPS", None)
            else:
                os.environ["LHM_DESKTOP_ALLOWED_APPS"] = prior_apps


if __name__ == "__main__":
    main()
