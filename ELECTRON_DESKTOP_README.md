# LHM Robot Desktop: Electron + native Windows controller

The LHM Agent AI user interface is now hosted by Electron on the owner's
Windows PC. The previous HTML dashboard remains the UI technology, NOT the
execution engine. Three local Python processes run the backend, worker and
native Windows desktop controller. No cloud computer is required.

## Features
- Windows NSIS x64 installer built by GitHub Actions on windows-latest.
- Three supervised local runtime modes bundled with PyInstaller.
- Local controller on 127.0.0.1:7782, local FastAPI on 127.0.0.1:7770.
- Native HWND enumeration, UI Automation labels, selected-window screenshot,
  activate, click, Unicode typing (including Vietnamese) and scrolling.
- Real Computer step approvals before focus/click/type/scroll.
- Per-role file/browser profiles and existing Agent Team Chat; one shared
  interactive Windows desktop, NOT 16 independent VMs or cursors.
- Electron Settings: model API keys protected by safeStorage when available,
  explicit approved EXE names and HTTPS hostnames. Defaults deny.
- Emergency Stop in Electron menu stops worker and pauses NEW mutations.
  It cannot undo a mouse event or external action already in progress.
- Chrome/Edge browser automation is optional; Windows native automation can
  operate an ordinary allowed application without a website API.

## How to install and run
1. Open the Windows Electron Robot Desktop workflow in GitHub Actions.
2. Choose a successful workflow run and download its
   LHM-Robot-Desktop-Windows-x64-Installer artifact.
3. Install the unsigned pilot setup using a normal Windows user account.
4. Launch LHM Robot Desktop from Start Menu; Electron launches the local
   FastAPI server, Agent worker and native Windows runner.
5. Use LHM Robot > Thiết lập Robot to enter OpenRouter or DeepSeek key,
   allowed apps (example: notepad.exe) and browser domains.
6. Save, restart the application, then open Agent Computer.
7. Choose a role, give an objective and inspect actions. Windows mouse/keyboard
   actions require human approval. The Windows preview panel can list
   allowlisted app windows and capture their actual pixels.

No Docker Desktop is required for native GUI automation in this mode.
However, do NOT run model-generated Python or arbitrary terminal commands on
the Windows host: this runner explicitly disables host run_command.
A separate Docker/VM sandbox is required for safe code execution.

## Security and limitations
The renderer has no Node integration, is sandboxed, and only navigates to the
local LHM backend. Permissions, new windows and external navigation are denied.
The native controller has an ephemeral local bearer token, receives NO cloud
model secrets and binds only loopback. The controller enumerates only explicitly
approved process names. Windows command shells and system utilities are
blocked even when added by mistake.

The Windows UI runs as the logged-in user and shares the physical cursor.
Do not whitelist banking, payment, password or other sensitive apps.
An overlapping window may appear in the selected-window screenshot.
No Windows Service, admin elevation, invisible background spying or
unrestricted OS access is installed.

This is a native pilot, NOT full Codex or Grok Bot parity.
Still missing: per-Agent VM isolation, robust vision feedback loop, native
interactive acceptance on the owner's logged-in Windows PC, signed installer,
secure auto-updates and hardened tenant-specific permission management.

The CI verifies Python imports, controller fail-closed behavior, Electron
security options, backend/docker tests, actual Chrome browser functions and
builds a Windows NSIS installer. CI cannot confirm real screen interaction
with Notepad on the owner's machine, and must not claim otherwise.

Installer signing: this pilot is unsigned. Windows SmartScreen might warn.
Do not override antivirus/security prompts blindly; only install from a
trusted verified build artifact.
