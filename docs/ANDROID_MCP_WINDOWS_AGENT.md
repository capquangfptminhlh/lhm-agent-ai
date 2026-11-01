# Android MCP trong LHM Robot Desktop (Windows)

Luồng tích hợp thật: `LHM Robot Desktop` (Electron cấu hình) → `computer_agent.py` (chọn công cụ theo role) → `computer_executor.py` (Windows native runner) → `android_mcp_bridge.py` (MCP stdio) → `ggminh/bin/lhm-android-mcp.js` → Android SDK/ADB.

## Cách bật

1. Cài phiên bản `ggminh` có PR #83 hoặc mới hơn trên ổ đĩa Windows. Cài Android SDK Platform Tools và tạo một AVD kiểm thử. Khởi chạy AVD, dùng `adb devices -l` để đọc serial chính xác.
2. Mở **LHM Robot → Thiết lập Robot → Tùy chỉnh nâng cao → Android MCP**. Bật tính năng; nhập thư mục `ggminh` trên ổ Windows (ví dụ `C:\LHM\ggminh`), serial như `emulator-5554`, và vai trò như `research,quality`.
3. Nhấn **Lưu cấu hình**, khởi động lại phần mềm, sau đó **Kiểm tra Android MCP**. Chỉ tiếp tục khi Runner xác nhận đã cấu hình. Trạng thái cấu hình không chứng minh emulator đang online: hãy thử một tác vụ Computer Agent có nhu cầu đọc danh sách Android.
4. Tại màn hình **Máy tính Agent**, chọn đúng vai trò được cấp và giao việc đọc máy Android. Agent có thể gọi `android_mcp_devices` để liệt kê máy đã cấp quyền, và `android_mcp_inspect` với serial hợp lệ để lấy nút bấm theo hình học.

## Phạm vi quyền

- Mặc định Android MCP **TẮT** và không có serial/vai trò được cấp.
- Có thể chỉ định chính xác các vai trò trong 16 Agent, tối đa 8 serial, không wildcard. Mọi quyết định đều được kiểm tra lại trong Windows runner; AI không được đổi đường dẫn, executable hoặc danh sách serial qua tham số công cụ.
- Hai tool của Agent đều **chỉ đọc**. Không có click, tap, swipe, nhập chữ, screenshot, APK, tài khoản, shell hay gửi tin. Các hành động đó vẫn nằm trong LHM Browser và phải được anh duyệt riêng qua Electron.
- Dữ liệu công cụ trả về có thể đi vào ngữ cảnh mô hình AI: serial đã cấp quyền và loại nút, vai trò, tọa độ, gợi ý điều hướng. Không dùng với thiết bị có dữ liệu riêng tư dù bridge đã loại bỏ nhãn chữ, mật khẩu và ảnh.
- Bridge chạy trong tiến trình phụ Node.js từ bản `ggminh` trên máy. Không mở cổng HTTP/MCP công khai. Tiến trình vẫn dùng quyền Windows hiện tại, **không phải sandbox cách ly**.

## Kiểm thử

- `python -m unittest tests.test_android_mcp_bridge` — phân quyền role/serial, dữ liệu nhạy cảm, truyền thông MCP mô phỏng và chặn cấu hình sai.
- `cd desktop && npm test` — cấu hình, validation Windows, nguồn Node cố định và kiểm tra khác của Electron.
- Kiểm thử trên GitHub chỉ chứng minh mã/đóng gói với mock. **Chưa được coi là Android thật hoạt động** khi workflow AVD tự-host `ggminh` chưa PASS.

## Giới hạn hiện tại

Đây là tích hợp **Computer Agent** cho các vai trò được chọn, không có nghĩa cả 16 Agent được cấp quyền mặc định hoặc mỗi Agent có một VM Android riêng. Không có AI semantic vision, luồng phê duyệt hành động MCP, tự cài Android SDK, test GPU đa máy hoặc kiểm chứng thương mại. Cần test thực tế trên Windows 10/11 sau khi cấp quyền.
