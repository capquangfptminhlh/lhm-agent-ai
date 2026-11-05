#!/usr/bin/env python3
"""Opt-in REAL Windows Android Emulator acceptance for LHM Robot's MCP Agent.

Requires a dedicated self-hosted Windows GitHub runner with SDK + WHPX/AVD.
Never runs as part of generic CI. No private device, account, UI strings,
screenshots or serial numbers are printed/uploaded. A write check needs
LHM_ANDROID_LIVE_BACK_TAP=YES and ONLY targets an exact safe "back" hint.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

AVD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SERIAL = re.compile(r"^emulator-[0-9]{4,5}$")
MAX_BOOT_SECONDS = 360
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def configured_avd(value: str | None) -> str:
    if not isinstance(value, str) or not AVD_NAME.fullmatch(value):
        raise RuntimeError("A safe, already installed LHM_ANDROID_AVD is required")
    return value


def sdk_directory(env: dict[str, str] | None = None) -> Path:
    variables = os.environ if env is None else env
    choices = [
        variables.get("ANDROID_SDK_ROOT"), variables.get("ANDROID_HOME"),
        str(Path(variables["LOCALAPPDATA"]) / "Android" / "Sdk")
        if variables.get("LOCALAPPDATA") else None,
    ]
    for choice in choices:
        if not choice:
            continue
        root = Path(choice)
        if root.is_absolute() and (root/"platform-tools"/"adb.exe").is_file() \
                and (root/"emulator"/"emulator.exe").is_file():
            return root
    raise RuntimeError("Android SDK adb.exe and emulator.exe not found in standard locations")


def screenshot_metadata(png: bytes) -> dict[str, int]:
    if not isinstance(png, bytes) or len(png) < 24 or \
            not png.startswith(PNG_SIGNATURE) or png[12:16] != b"IHDR":
        raise RuntimeError("Android screenshot did not contain a PNG header")
    width, height = struct.unpack(">II", png[16:24])
    if not (0 < width <= 8192 and 0 < height <= 8192):
        raise RuntimeError("Android screenshot dimensions are invalid")
    return {"width": width, "height": height}


class RealWindowsAvd:
    """Own one test emulator subprocess, refuse preexisting ADB devices."""
    def __init__(self, sdk: Path):
        self.adb = str(sdk/"platform-tools"/"adb.exe")
        self.emulator = str(sdk/"emulator"/"emulator.exe")
        self.process: subprocess.Popen | None = None
        self.serial: str | None = None
        self.name: str | None = None

    def command(self, argv: list[str], *, timeout: int = 25,
                binary: bool = False) -> str | bytes:
        result = subprocess.run(
            argv, check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError("Android SDK/ADB operation returned an error")
        return result.stdout if binary else result.stdout.decode("utf-8", "replace")

    def _devices(self) -> list[tuple[str, str]]:
        output = self.command([self.adb, "devices"], timeout=20)
        devices = []
        for line in output.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                devices.append((parts[0], parts[1]))
        return devices

    def start(self, name: str) -> None:
        self.name = configured_avd(name)
        installed = self.command([self.emulator, "-list-avds"], timeout=25).splitlines()
        if name not in installed:
            raise RuntimeError("Requested isolated test AVD is not installed")
        if self._devices():
            raise RuntimeError("Dedicated test runner has existing ADB devices; refusing to touch them")
        self.process = subprocess.Popen(
            [self.emulator, "-avd", name, "-no-window", "-no-audio",
             "-no-snapshot", "-no-boot-anim"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=0x08000000 if os.name == "nt" else 0,
        )

    def wait_boot(self) -> str:
        if not self.process or not self.name:
            raise RuntimeError("AVD was not launched by this test")
        deadline = time.monotonic() + MAX_BOOT_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Test AVD exited before boot completed")
            try:
                connected = [(s, state) for s, state in self._devices()
                             if SERIAL.fullmatch(s) and state == "device"]
                if len(connected) == 1 and len(self._devices()) == 1:
                    serial = connected[0][0]
                    actual = self.command(
                        [self.adb, "-s", serial, "emu", "avd", "name"], timeout=15,
                    ).splitlines()
                    if not actual or actual[0].strip() != self.name:
                        raise RuntimeError("AVD identity mismatch")
                    boot = self.command(
                        [self.adb, "-s", serial, "shell", "getprop", "sys.boot_completed"],
                        timeout=15,
                    ).strip()
                    if boot == "1":
                        self.serial = serial
                        return serial
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                # The Android emulator is often temporarily unavailable during boot.
                pass
            time.sleep(3)
        raise RuntimeError("Test AVD did not finish boot within six minutes")

    def open_settings(self, serial: str) -> None:
        if serial != self.serial:
            raise RuntimeError("AVD ownership mismatch")
        self.command([self.adb, "-s", serial, "shell", "am", "start",
                      "-a", "android.settings.SETTINGS"], timeout=25)
        time.sleep(3)

    def screenshot(self, serial: str) -> dict[str, int]:
        if serial != self.serial:
            raise RuntimeError("AVD ownership mismatch")
        png = self.command([self.adb, "-s", serial, "exec-out", "screencap", "-p"],
                           timeout=25, binary=True)
        if len(png) > 20_000_000:
            raise RuntimeError("Android screenshot exceeds safe size")
        return screenshot_metadata(png)

    def stop(self) -> None:
        proc = self.process
        if not proc:
            return
        # The AVD was spawned by this process; never issue a command to other
        # Android devices if an identity check did not succeed.
        if self.serial and SERIAL.fullmatch(self.serial):
            try:
                self.command([self.adb, "-s", self.serial, "emu", "kill"], timeout=15)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                pass
        try:
            proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if proc.poll() is None:
            raise RuntimeError("Could not stop isolated test emulator")
        if self.serial:
            # The subprocess exited; confirm its serial is no longer attached.
            try:
                if any(serial == self.serial for serial, _ in self._devices()):
                    raise RuntimeError("Test emulator is still listed by ADB after shutdown")
            except subprocess.TimeoutExpired:
                raise RuntimeError("Could not verify Android ADB cleanup")


async def run_live(host: Any, bridge: Any, name: str, *,
                   allow_back_tap: bool = False) -> dict[str, Any]:
    """Runs an actual MCP read, optionally one restricted tap on a test AVD."""
    name = configured_avd(name)
    start = time.monotonic()
    serial = None
    try:
        host.start(name)
        serial = host.wait_boot()
        if not isinstance(serial, str) or not SERIAL.fullmatch(serial):
            raise RuntimeError("Only a dedicated Android emulator can be tested")
        host.open_settings(serial)
        screen = host.screenshot(serial)

        # Import actual read-only ggminh MCP over stdio through this project.
        devices = await bridge.execute("research", "android_mcp_devices", {})
        entries = devices.get("devices")
        if not isinstance(entries, list) or len(entries) != 1 or \
                entries[0].get("serial") != serial or entries[0].get("status") != "device":
            raise RuntimeError("LHM Robot MCP did not see precisely its approved AVD")
        run_id = str(uuid.uuid4()) if allow_back_tap else None
        result = await bridge.execute(
            "research", "android_mcp_inspect", {"serial": serial}, run_id=run_id,
        )
        if result.get("privacy") != "geometry-only" or \
                not isinstance(result.get("elements"), list):
            raise RuntimeError("LHM Robot did not return sanitized UI geometry")
        nodes = result["elements"]
        if not nodes or len(nodes) > 120:
            raise RuntimeError("Android Settings produced no bounded interactive UI")
        # Double-check exact output shape. Future bridge changes must not
        # accidentally expose app label text, contents or screenshots.
        safe_keys={"index","role","hint","bounds"}
        safe_bounds={"x","y","width","height"}
        if any(not isinstance(node,dict) or
               not set(node).issubset(safe_keys) or
               not isinstance(node.get("bounds"),dict) or
               not set(node["bounds"]).issubset(safe_bounds)
               for node in nodes):
            raise RuntimeError("Android UI metadata was not fully redacted")

        tap = "NOT_REQUESTED"
        if allow_back_tap:
            # An explicit workflow dispatch enables only a navigation-back tap,
            # never an arbitrary control, payment, message, login, or install.
            targets = [node for node in nodes if
                       node.get("role") == "button" and node.get("hint") == "back"]
            if len(targets) != 1 or not result.get("observation_id"):
                raise RuntimeError("No unique verified Back button; refusing a substitute tap")
            target=targets[0]
            bounds=target["bounds"]
            # MCP reports tap CENTER, not top-left. The test AVD screenshot
            # supplies independent display dimensions; refuse ambiguous or
            # off-screen navigation even when the node claims a 'back' hint.
            if (type(target.get("index")) is not int or
                    not 1 <= target["index"] <= 120 or
                    any(node is not target and node.get("index")==target["index"]
                        for node in nodes) or
                    any(type(bounds.get(key)) is not int for key in
                        ("x","y","width","height")) or
                    bounds["width"] <= 0 or bounds["height"] <= 0 or
                    not 0 <= bounds["x"] < screen["width"] or
                    not 0 <= bounds["y"] < screen["height"]):
                raise RuntimeError("Back target is ambiguous or outside the test AVD screen")
            tap_result = await bridge.execute(
                "research", "android_mcp_tap",
                {"serial":serial,"observation_id":result["observation_id"],
                 "index":targets[0]["index"]},
                run_id=run_id,
            )
            if tap_result.get("state") != "tap_sent" or \
                    tap_result.get("requires_owner_reconciliation") is not True or \
                    tap_result.get("external_effect_verified") is not False:
                raise RuntimeError("LHM Robot tap receipt did not require owner reconciliation")
            tap = "SENT_UNVERIFIED_NEEDS_OWNER_RECONCILIATION"

        return {
            "runtime":"REAL_WINDOWS_AVD",
            "read":"PASS",
            "tap":tap,
            "screenshot":screen,
            "total_nodes":len(nodes),
            "elapsed_seconds":round(time.monotonic()-start, 1),
            "external_effect_verified":False,
        }
    finally:
        # No results are printed until the test emulator has been terminated.
        host.stop()


def main() -> int:
    if os.name != "nt":
        print("ANDROID LIVE ACCEPTANCE: Windows-only", file=sys.stderr)
        return 2
    try:
        name = configured_avd(os.environ.get("LHM_ANDROID_AVD"))
        repo = Path(os.environ.get("LHM_GGMINH_REPO",""))
        entry = repo / "bin" / "lhm-android-mcp.js"
        if not repo.is_absolute() or not entry.is_file() or entry.is_symlink():
            raise RuntimeError("A local ggminh source checkout with MCP is required")
        sdk = sdk_directory()
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js not found on the test runner")
        import android_mcp_bridge as bridge
        decision=os.environ.get("LHM_ANDROID_LIVE_BACK_TAP","NO")
        if decision not in ("YES","NO"):
            raise RuntimeError("Live Android write consent must be explicitly YES or NO")
        is_write = decision == "YES"
        host = RealWindowsAvd(sdk)
        # Set only the native bridge's fixed local configuration. ADB serial
        # is populated only after the owned emulator is verified at boot.
        async def test():
            from unittest.mock import patch
            # The allowlist is injected ONLY after the owned AVD identity and
            # boot completion have been validated.
            class BoundHost:
                def start(self, avd):
                    host.start(avd)
                def wait_boot(self):
                    serial = host.wait_boot()
                    os.environ["LHM_ANDROID_MCP_ALLOWED_SERIALS"] = serial
                    return serial
                def open_settings(self, serial):host.open_settings(serial)
                def screenshot(self, serial):return host.screenshot(serial)
                def stop(self):host.stop()
            with patch.dict(os.environ,{
                "LHM_DESKTOP_NATIVE":"1",
                "LHM_ANDROID_MCP_ENABLED":"1",
                "LHM_ANDROID_MCP_TAP_ENABLED":"1" if is_write else "0",
                "LHM_ANDROID_MCP_ENTRY":str(entry.resolve()),
                "LHM_ANDROID_MCP_NODE":node,
                "LHM_ANDROID_MCP_ALLOWED_ROLES":"research",
                "LHM_ANDROID_MCP_ALLOWED_SERIALS":"",
                "ANDROID_SDK_ROOT":str(sdk),
            }):
                return await run_live(BoundHost(),bridge,name,allow_back_tap=is_write)
        summary = asyncio.run(test())
        # Never print filenames, host paths, UI text, device serial or images.
        print(json.dumps(summary,sort_keys=True))
        return 0
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
        print("ANDROID LIVE ACCEPTANCE: FAIL — check isolated AVD / SDK / MCP setup",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
