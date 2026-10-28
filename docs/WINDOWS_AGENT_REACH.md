# LHM Robot Desktop — Agent-Reach trên Windows

## Vì sao có bản này?

Bản trước chỉ đóng gói Agent-Reach trong Linux Computer Runner. Người sử
dụng Windows không cần Docker/WSL để *tìm kiếm công khai*. Windows Electron
giờ có `lhm-runtime.exe reach-worker` dùng một worker process riêng; đây
là worker tìm kiếm read-only, **không phải điều khiển máy tính toàn quyền**.

## Sử dụng

1. Cài LHM Robot Desktop mới từ artifact `LHM-Robot-Desktop-Windows-x64-Installer`
   trên GitHub Actions của chính commit đã qua CI, thay vì tải file lạ.
2. Mở `LHM Robot → Thiết lập Robot`, đánh dấu **Bật Agent-Reach tìm kiếm GitHub/YouTube công khai ngay trên Windows**.
3. Lưu rồi khởi động lại. Có OpenRouter Free key thì tạo Computer Run trong
   Work Order Content hoặc Quality, hoặc giao nghiên cứu cho Research.
4. Agent có công cụ `agent_reach_search` và chọn
   `{"channel":"github","query":"từ khóa"}` hoặc `youtube`. Native
   controller gọi worker đã đóng gói để tìm kiếm; kết quả JSON `items`
   được cắt ngắn và ghi thành quan sát *unverified*, không tự xác nhận sự
   thật hay đăng bài.
5. Trong Developer/source mode, người quản trị có thể tự cài bản từ GitHub
   release bằng `python scripts/install_agent_reach_windows.py`, cần Python
   3.10+ và internet. Chức năng production đóng gói đã bao gồm thư viện.

## Cách đóng gói và giới hạn quyền

- Windows CI tải `agent_reach-1.6.0-py3-none-any.whl` **từ GitHub release
  Panniantong/Agent-Reach**, kiểm SHA-256
  `79455eafd94302561aa5f52b0722a0fc359279c3170a27c6990486027fd56830`.
  Đó không phải tên package cùng tên không liên quan trên PyPI. PyInstaller
  `--collect-all agent_reach` đóng thư viện vào runtime.
- GitHub dùng backend Agent-Reach `github-api`. YouTube dùng API thư viện
  `yt-dlp` đóng gói trực tiếp trong worker vì bản frozen EXE không hỗ trợ
  `python -m yt_dlp` như Python thường. Cả hai chỉ tìm kiếm công khai;
  không cho AI chọn backend, arbitrary URL, user cookie hoặc lệnh cài/update.
- Mỗi vai trò/workspace có HOME/APPDATA/LOCALAPPDATA riêng. Child env không
  mang `OPENROUTER_API_KEY`, `LHM_COMPUTER_RUNNER_TOKEN`, `GH_TOKEN`.
- Windows process **vẫn chạy cùng tài khoản hệ điều hành** và có thể đọc
  những tài nguyên mà tài khoản đó truy cập được. Thư mục riêng không phải
  security sandbox hoặc VM. Nếu muốn cách ly OS thật, chọn Linux
  Computer Runner trong container/VM có egress policy riêng.
- Chỉ cho phép tìm kiếm công khai. Không tự lấy phiên Chrome đăng nhập; Facebook,
  Instagram, Zalo và các kênh yêu cầu đăng nhập chưa mở.
- Nếu Windows không có mạng, GitHub trả 403/429, YouTube chặn truy cập hoặc
  mô hình chưa được cấu hình, agent phải báo lỗi. `CI PASS` không đồng
  nghĩa kết nối bên ngoài luôn hoạt động.
- Trình duyệt Chrome/Edge nằm ở Computer tools riêng và vẫn có xác nhận
  trước các thao tác có tác động; Postiz API vẫn là đường xuất bản chính.

## Test

- Unit test Windows giả lập chứng minh toggle mặc định tắt, bảo vệ token,
  whitelist channel và lựa chọn worker command cố định.
- GitHub Windows CI chạy cài wheel từ release, kiểm source runtime
  `reach-worker` và worker đóng băng trong `lhm-runtime.exe`, tiếp theo
  kiểm runtime API, đóng gói installer và GUI.
- Chỉ sau khi Linux/Windows kiểm thử thành công và được merge vào main,
  bản EXE mới được coi là đã đóng gói chức năng này.
