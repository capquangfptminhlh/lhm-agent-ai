"""Manual real Windows AVD → ggminh MCP → LHM Robot read-only acceptance.

This is NOT a unit test or a device controller. Uses ONLY ADB read commands;
no screen captures, taps, swipes, shell scripts or Android writes. An operator
must start a dedicated test AVD before running the acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Awaitable, Callable

from android_mcp_actions import _adb_binary
from android_mcp_bridge import execute as mcp_execute, validate as mcp_validate

EMULATOR_RE = re.compile(r"^emulator-[0-9]{4,5}$")
AVD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_HIERARCHY_TRIES = 3


def validate_inputs(serial: str, avd: str, ggminh: Path,
                    *, platform: str | None = None) -> Path:
    if (platform or os.name) not in ("nt", "win32"):
        raise ValueError("Real Android MCP acceptance requires Windows")
    if not EMULATOR_RE.fullmatch(serial):
        raise ValueError("Use one explicitly selected emulator-XXXX serial")
    if not AVD_RE.fullmatch(avd):
        raise ValueError("AVD name must be an exact safe test profile name")
    root = ggminh.resolve()
    entry = root / "bin" / "lhm-android-mcp.js"
    if not entry.is_file() or entry.is_symlink():
        raise ValueError("ggminh must contain bin/lhm-android-mcp.js")
    return entry


def adb_read(serial: str, *command: str) -> str:
    """Only these two fixed read-only ADB calls are legal here."""
    if tuple(command) not in (
        ("emu", "avd", "name"),
        ("shell", "getprop", "sys.boot_completed"),
    ):
        raise ValueError("Unsupported ADB read command in live acceptance")
    executable = _adb_binary()
    result = subprocess.run(
        [executable, "-s", serial, *command],
        capture_output=True, text=True, encoding="utf-8",
        timeout=18, shell=False, check=True
    )
    return result.stdout.strip()


def exact_avd_name(output: str) -> str:
    rows=[line.strip() for line in output.splitlines() if line.strip()]
    if len(rows)!=2 or rows[1]!="OK" or not AVD_RE.fullmatch(rows[0]):
        raise ValueError("ADB did not confirm the expected AVD name")
    return rows[0]


async def accept(serial: str,avd: str,ggminh: Path,
                 *,platform: str | None = None,
                 adb_fn: Callable[...,str] = adb_read,
                 execute_fn: Callable[...,Awaitable[dict]] = mcp_execute,
                 sleep_fn: Callable[[float],Awaitable[None]] = asyncio.sleep) -> dict:
    entry=validate_inputs(serial,avd,ggminh,platform=platform)
    node=shutil.which("node.exe" if (platform or os.name) in ("nt","win32") else "node")
    if not node or not Path(node).is_file():
        raise ValueError("Node.js is not installed for Windows MCP stdio")
    if exact_avd_name(adb_fn(serial,"emu","avd","name")) != avd:
        raise ValueError("Selected ADB serial belongs to a different AVD")
    if adb_fn(serial,"shell","getprop","sys.boot_completed").strip()!="1":
        raise ValueError("The selected test AVD has not completed Android boot")

    # Dedicated process-scoped permission: ONE test device, ONE read role,
    # Android writes forcibly disabled even if caller's env enabled them.
    os.environ.update({
        "LHM_DESKTOP_NATIVE":"1",
        "LHM_ANDROID_MCP_ENABLED":"1",
        "LHM_ANDROID_MCP_TAP_ENABLED":"0",
        "LHM_ANDROID_MCP_ALLOWED_ROLES":"research",
        "LHM_ANDROID_MCP_ALLOWED_SERIALS":serial,
        "LHM_ANDROID_MCP_ENTRY":str(entry),
        "LHM_ANDROID_MCP_NODE":node
    })

    status=await execute_fn("research","android_mcp_devices",{})
    devices=status.get("devices") if isinstance(status,dict) else None
    if (not isinstance(devices,list) or len(devices)!=1
            or devices[0].get("serial")!=serial
            or devices[0].get("kind")!="emulator"
            or devices[0].get("status")!="device"):
        raise ValueError("MCP did not return exactly the approved online AVD")
    # Prove default denial for a different serial; no ADB request may be sent.
    try:
        mcp_validate("research","android_mcp_inspect",{"serial":"emulator-99999"})
    except Exception as exc:
        from fastapi import HTTPException
        if not isinstance(exc,HTTPException) or exc.status_code!=403:
            raise ValueError("Unapproved emulator serial did not fail closed") from exc
    else:
        raise ValueError("Unapproved serial unexpectedly accepted")

    ui=None
    for attempt in range(MAX_HIERARCHY_TRIES):
        ui=await execute_fn("research","android_mcp_inspect",{"serial":serial})
        if (isinstance(ui,dict) and ui.get("privacy")=="geometry-only"
                and ui.get("read_only") is True
                and type(ui.get("totalNodes")) is int and ui["totalNodes"]>=1
                and isinstance(ui.get("elements"),list) and ui["elements"]):
            break
        if attempt+1<MAX_HIERARCHY_TRIES:
            await sleep_fn(2)
    else:
        raise ValueError("MCP could not observe actionable nodes on the test AVD")

    # Fail if any unexpected sensitive field is returned. We do NOT write the
    # snapshot to logs, artifacts or third-party providers.
    if set(ui) - {"privacy","read_only","totalNodes","elements"}:
        raise ValueError("MCP returned unexpected UI metadata fields")
    if len(ui["elements"])>120:
        raise ValueError("MCP exceeded the bounded UI node limit")
    permitted={"index","role","hint","bounds"}
    for element in ui["elements"]:
        if not isinstance(element,dict) or set(element)-permitted:
            raise ValueError("MCP returned unexpected UI node fields")
        bounds=element.get("bounds")
        if not isinstance(bounds,dict) or set(bounds)!={"x","y","width","height"}:
            raise ValueError("MCP returned unexpected node geometry")
    return {
        "status":"PASS","source":"live-windows-avd",
        "bridge":"lhm-robot-to-ggminh-stdio-to-adb",
        "avd_name_verified":True,"boot_verified":True,
        "allowlist_other_serial_denied":True,
        "permissions":"read-only","tap_enabled":False,
        "hierarchy_nodes":ui["totalNodes"],
        "interactive_elements":len(ui["elements"]),
        "actual_android_tap_tested":False
    }


def main() -> None:
    parser=argparse.ArgumentParser(description="Real Windows read-only Android MCP acceptance")
    parser.add_argument("--serial",required=True)
    parser.add_argument("--avd-name",required=True)
    parser.add_argument("--ggminh",type=Path,required=True)
    args=parser.parse_args()
    try:
        result=asyncio.run(accept(args.serial,args.avd_name,args.ggminh))
    except Exception as exc:
        # Intentionally hide paths, device IDs and raw ADB output.
        reason=type(exc).__name__
        print(json.dumps({"status":"FAIL","reason":reason,
                          "hint":"Check test AVD boot, SDK, Node and MCP permissions locally"}))
        raise SystemExit(1)
    print(json.dumps(result,separators=(",",":"),ensure_ascii=False))


if __name__=="__main__":
    main()
