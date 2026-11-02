"""Operator-only Windows Android MCP diagnostics.

Performs actual read-only ggminh stdio MCP calls on the native Windows runner.
Never executes a gesture or sends XML, images or device content to an LLM.
"""
from __future__ import annotations

import os

from android_mcp_bridge import allowed_roles, allowed_serials, configured, execute
from android_mcp_actions import tap_enabled

SAFE_STATES={"device","offline","unauthorized"}
SAFE_KINDS={"emulator","physical"}


async def inspect_status() -> dict:
    roles=sorted(allowed_roles())
    serials=allowed_serials()
    enabled=os.getenv("LHM_ANDROID_MCP_ENABLED")=="1"
    permitted=next((role for role in roles if configured(role)),None)
    result={
        "enabled":enabled,
        "configured":bool(permitted),
        "ready":False,
        "connectionChecked":False,
        "mcpConnected":False,
        "avdConnected":False,
        "uiReadable":False,
        "uiActionableCount":0,
        "tapEnabled":bool(enabled and tap_enabled()),
        "allowedRoles":roles,
        "allowedDeviceCount":len(serials),
        "devices":[],
        "detail":"disabled" if not enabled else "configuration-incomplete"
    }
    if permitted is None:
        return result
    result["connectionChecked"]=True
    try:
        observed=await execute(permitted,"android_mcp_devices",{},run_id=None)
        found=observed.get("devices") if isinstance(observed,dict) else None
        if not isinstance(found,list):
            result["detail"]="invalid-mcp-device-response"
            return result
        # Only expose already allowlisted, non-sensitive device status.
        devices=[]
        for dev in found[:8]:
            if not isinstance(dev,dict) or dev.get("serial") not in serials:
                continue
            if dev.get("status") not in SAFE_STATES:
                state="unknown"
            else:
                state=dev["status"]
            devices.append({
                "serial":dev["serial"],"status":state,
                "kind":dev.get("kind") if dev.get("kind") in SAFE_KINDS else "unknown"
            })
        result["mcpConnected"]=True
        result["devices"]=devices
        target=next((d for d in devices
                     if d["kind"]=="emulator" and d["status"]=="device"),None)
        if target is None:
            result["detail"]="no-online-allowed-avd"
            return result
        result["avdConnected"]=True
    except Exception:
        # Do not reveal subprocess errors, paths, ADB stderr or text content.
        result["detail"]="mcp-device-read-failed"
        return result

    try:
        tree=await execute(permitted,"android_mcp_inspect",
                           {"serial":target["serial"]},run_id=None)
        valid=(isinstance(tree,dict) and tree.get("privacy")=="geometry-only"
               and tree.get("read_only") is True
               and type(tree.get("totalNodes")) is int
               and isinstance(tree.get("elements"),list))
        if not valid:
            result["detail"]="invalid-mcp-ui-response"
            return result
        # Downstream sees only counts; no UI hierarchy or screenshot.
        count=min(len(tree["elements"]),120)
        result["uiReadable"]=tree["totalNodes"]>0
        result["uiActionableCount"]=count
        result["ready"]=result["uiReadable"] and count>0
        result["detail"]="avd-ui-read-confirmed" if result["ready"] else "avd-ui-not-actionable"
        return result
    except Exception:
        result["detail"]="mcp-ui-read-failed"
        return result
