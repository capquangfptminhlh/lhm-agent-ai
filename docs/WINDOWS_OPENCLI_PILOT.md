# LHM Robot Desktop — kết nối OpenCLI với Chrome riêng trên Windows

**Trạng thái:** adapter có mã thực thi và kiểm thử offline/CLI smoke; việc
Chrome profile thật kết nối và Agent thao tác website thật vẫn cần người
quản trị cấp quyền, thử trên máy Windows và kiểm chứng kết quả.

## Cách thiết lập (không cần Linux, Docker)

1. Cài **Node.js >= 20.18.1** từ nhà cung cấp Node.js chính thức.
   Trong PowerShell do anh mở (không phải lệnh AI), chạy:
   `node --version` rồi `npm install -g @jackwener/opencli@2.0.2`.
   Đường npm global Windows thông thường phải có
   `%APPDATA%\\npm\\node_modules\\@jackwener\\opencli\\dist\\src\\main.js`.
   LHM gọi trực tiếp Node + file JS đó, không chạy `.cmd`, shell hay
   bất kỳ plugin/adapter bên ngoài nào.
2. Tạo **Chrome profile mới, riêng cho LHM**, không dùng tài khoản
   Chrome cá nhân mặc định. Chỉ đăng nhập trang web anh muốn LHM thao tác.
3. Trong Chrome profile này, cài **OpenCLI Browser Bridge** từ
   [Chrome Web Store](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk).
   Chính anh tự quyết định quyền mà extension yêu cầu. OpenCLI daemon
   dùng cổng localhost 19825 và có thể chạy lâu dài.
4. Trong PowerShell: `opencli doctor`, `opencli profile list`.
   Gán tên profile mới: `opencli profile rename <contextId> lhm-work`
   (thay `<contextId>` bằng đúng ID được liệt kê).
   Kiểm tra `opencli --profile lhm-work browser state --session lhm-manual-test`
   sau khi mở một tab thử. Không tự lấy cookies/profile mặc định.
5. Mở **LHM Robot → Thiết lập Robot**:
   - Điền danh sách `Website HTTPS được phép mở` (ví dụ `example.com`).
   - Bật **OpenCLI**, nhập `lhm-work` và danh sách website OpenCLI,
     là tập con của danh sách HTTPS bên trên.
   - Lưu và **khởi động lại LHM Robot**.
6. Tạo Computer Pilot/Work Order Content hoặc Quality. AI có thể mở
   trang và đọc state với tham chiếu `[1]`, `[2]` v.v. Content có
   thể đề xuất `opencli_click` và `opencli_fill` theo tham chiếu
   đó; **phải có phê duyệt từng bước**, kể cả Trusted mode.
   QA và Sales chỉ có quyền mở/đọc, không có quyền click/điền.

## Những gì được phép

| Tool | Tham số | Duyệt |
|---|---|---|
| `opencli_open` | `{"url":"https://example.com/path"}` | HTTPS host phải có trong cả hai allowlist |
| `opencli_state` | `{}` | URL hiện tại phải được phép; dữ liệu DOM bị giới hạn |
| `opencli_click` | `{"ref":12}` | Content; duyệt từng bước; ref phải xuất hiện trong state mới |
| `opencli_fill` | `{"ref":3,"text":"Nội dung"}` | Content; duyệt từng bước; không dùng cho mật khẩu |

Không có quyền `opencli eval`, JavaScript tùy ý, Chrome native API,
network sniff, đọc cookie, chuyển tab người dùng, tải file, plugin install,
đăng bài API ẩn, hoặc chạy lệnh terminal qua bridge.

## Quyền riêng tư và giới hạn

- OpenCLI extension **có khả năng truy cập phiên Chrome được kết nối**;
  việc đặt tên profile và dùng session riêng giảm nhầm lẫn, **không tạo
  ranh giới cách ly OS** giữa tài khoản Windows. Chỉ mở các trang tin cậy,
  không cấp profile cá nhân chứa Gmail/ngân hàng/CRM nhạy cảm.
- LHM chỉ kiểm tra URL trước và sau thao tác. **Không có firewall kiểm
  soát mọi redirect / mọi network request** bên trong extension.
  Domain allowlist là lớp kiểm tra ở ứng dụng, không phải cách ly mạng.
- Click/Fill qua giao diện có thể tạo tác động ngoài hệ thống ngay khi
  người dùng duyệt. Sau khi click thành công, **không tự coi là đã đăng
  bài, tạo đơn hoặc gửi tin**; cần bằng chứng nhà cung cấp độc lập.
- Các thao tác có kết quả không rõ ràng không được retry mù quáng. Nếu
  Chrome chuyển sang domain khác, UI trả 409 yêu cầu chủ máy kiểm tra.
- Không sao chép session Chrome cá nhân; không gửi API key OpenRouter,
  GitHub token hoặc bearer token sang process OpenCLI. Tuy nhiên nội dung
  trang mà Agent quan sát có thể được chuyển tới LLM theo thiết kế.
- Chức năng này chỉ gọi được từ **Windows native runner**; Linux
  container/remote worker trả 503, không có fallback đụng tài khoản cá nhân.
- OpenCLI là công cụ bên thứ ba; LHM **không tự cài, tự cập nhật hoặc bật
  extension**. Phiên bản API Browser Bridge có thể thay đổi. Test CI
  kiểm lệnh CLI và mã, chưa chứng minh profile đăng nhập thực tế hoạt động.
