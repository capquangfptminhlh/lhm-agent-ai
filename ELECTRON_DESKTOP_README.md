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

## Cài đặt và dùng nhanh — bản 0.2.0

1. Tải đúng installer `LHM-Robot-Desktop-Setup-0.2.0-x64.exe` từ Windows GitHub Actions của commit mới, không dùng lại installer `0.1.0`.
2. Cài đặt và mở LHM Robot Desktop. Lần đầu, cửa sổ **Bắt đầu nhanh** tự mở; chỉ cần điền OpenRouter API key riêng của mình (được mã hoá bằng Windows secureStorage).
3. Bấm **Lưu & bắt đầu — cho phép 3 ứng dụng thử nghiệm**. Thao tác này là sự đồng ý cho phép `notepad.exe`, `explorer.exe`, `calc.exe`; không tự cấp quyền bất kỳ ứng dụng khác hoặc website nào.
4. Chọn **Khởi động lại** để backend/Worker/Controller nhận cấu hình mới. Trang chính vào **Máy tính Agent**. Chọn việc thử trong Notepad, theo dõi từng bước và duyệt các thao tác thay đổi máy.
5. Nếu tác vụ không chạy, mở **LHM Robot → Thiết lập Robot → Trạng thái phần mềm → Kiểm tra lại**. Mục nào có dấu **✕** sẽ cho biết: Backend 7770, Windows Controller 7782, Worker, OpenRouter key hay quyền ứng dụng/website.
6. Để kiểm chứng API, bấm **Thử gọi OpenRouter Free**. Nếu API báo 401/429/402 hoặc không có model Free khả dụng, robot **dừng và báo lỗi**, không tự chuyển model trả phí.
7. Các tính năng nâng cao (Vision, Tin cậy, Docker, thêm website/app) được thu gọn, không bắt buộc cho thử nghiệm Notepad. Website ngoài vẫn phải được phê duyệt theo tên miền trong phần nâng cao. Chế độ mặc định là Giám sát.

**Quan trọng:** Không tồn tại zero-setup AI hoàn toàn khi chưa có API key/tài khoản cấp quyền. Phiên bản này giảm cấu hình xuống một key và một thao tác đồng ý dùng app mẫu; không tuyên bố đạt GrokBot Parity+ hay xử lý được mọi phần mềm tự động. GitHub Windows CI chưa thay thế nghiệm thu trực tiếp trên máy người dùng.

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
