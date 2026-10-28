# LHM Robot Desktop — kết nối OpenCLI với Chrome riêng trên Windows

**Trạng thái:** adapter có mã thực thi và kiểm thử offline/CLI smoke; việc
Chrome profile thật kết nối và Agent thao tác website thật vẫn cần người
quản trị cấp quyền, thử trên máy Windows và kiểm chứng kết quả.

## Cách thiết lập trên Windows (không cần Linux, Docker, Node.js hay npm)

**Bộ cài LHM Robot bản mới đã kèm Node 24 và OpenCLI 2.0.2.**
Không cần mở PowerShell hoặc chạy lệnh npm để cài công cụ này.
Bản OpenCLI đi cùng ứng dụng không tự tải plugin hoặc cập nhật từ Internet.
Bộ cài vẫn là bản pilot không ký số; nên tải từ GitHub Actions của
chính repository và kiểm SHA-256 khi cần.

1. Tải installer `LHM-Robot-Desktop-Windows-x64-Installer` từ GitHub Actions
   của commit `main` đã PASS và cài LHM Robot.
2. Tạo **Chrome profile mới riêng cho LHM**, không sử dụng profile cá nhân
   chứa email, tài khoản ngân hàng hoặc hệ thống quản trị nhạy cảm.
3. Mở **LHM Robot → Thiết lập Robot → Cài tiện ích OpenCLI vào Chrome riêng**.
   Nút này mở đúng trang [Chrome Web Store](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk).
   Trong profile Chrome chuyên dụng, anh tự xác nhận quyền extension.
4. Đặt alias profile OpenCLI là `lhm-work`. Với bản chưa hỗ trợ danh sách
   profile trong giao diện, cách đặt tên từ CLI có thể thực hiện thủ công
   bằng `opencli profile list` và `opencli profile rename <contextId> lhm-work`
   nếu anh có CLI bên ngoài; nếu không, điền `contextId` thực tế
   tương thích vào ô profile (không dùng profile mặc định).
   Cấu hình Chrome profile là bước cần chủ máy thực hiện; không được
   tự lấy phiên đăng nhập hoặc cookie.
5. Trong Thiết lập Robot, nhập website HTTPS được phép (ví dụ
   `example.com`), bật OpenCLI, nhập tên profile và danh sách host OpenCLI
   là tập con của danh sách tổng. **Lưu và khởi động lại ứng dụng**.
6. Bấm **Kiểm tra kết nối OpenCLI / Chrome**. Chỉ tiếp tục khi CLI đã cài,
   Bridge đã hoạt động và đúng profile đang kết nối. Agent Content có thể
   đề xuất click/điền sau khi anh duyệt; QA/Sales chỉ mở và đọc trang.

**Chỉ code/CLI/installer được kiểm tra trên CI.** Chưa thể tự tuyên bố
đã đăng nhập và thao tác thành công trên Chrome profile của chủ máy.

## Những gì được phép

| Tool | Tham số | Duyệt |
|---|---|---|
| `opencli_open` | `{"url":"https://example.com/path"}` | HTTPS host phải có trong cả hai allowlist |
| `opencli_state` | `{}` | URL hiện tại phải được phép; dữ liệu DOM bị giới hạn |
| `opencli_click` | `{"ref":12,"url":"https://example.com/form","label":"Send"}` | Content; duyệt từng bước; ref phải xuất hiện trong state mới |
| `opencli_fill` | `{"ref":3,"url":"https://example.com/form","label":"Caption","text":"Nội dung"}` | Content; duyệt từng bước; không dùng cho mật khẩu |

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
- OpenCLI là công cụ bên thứ ba; **Node và OpenCLI CLI được kèm trong installer** và không tự cập nhật. Extension Chrome vẫn do chủ máy cài và cấp quyền. Test CI xác minh CLI được đóng gói, không chứng minh phiên Chrome đã đăng nhập.

## Nghiệm thu thực tế trên máy Windows

1. Cài CLI và extension vào **profile riêng** như phần trên; Chrome phải đang mở
   profile đó. Bật OpenCLI và nhập website rồi **Lưu, khởi động lại**.
2. Trong **Thiết lập Robot**, bấm **Kiểm tra kết nối OpenCLI / Chrome**.
   Chỉ xem là sẵn sàng khi bốn kiểm tra đều đạt: bật, đã cài đúng CLI,
   Bridge đang chạy và **đúng profile** đang kết nối. Người dùng không cần
   gửi ảnh chứa cookie/tokens để xử lý lỗi.
3. Giao Content Agent đọc `opencli_state`, quan sát dòng phần tử
   `[12] button Send` trên URL `https://example.com/form`.
   Để đề xuất click, Agent phải cung cấp **đủ**:
   `{"ref":12,"url":"https://example.com/form","label":"Send"}`.
   Fill tương tự với `text`. Agent không được chỉ đoán số thứ tự.
4. Nếu trang bị chuyển hướng hoặc phần tử thay tên trong thời gian
   đợi duyệt, thao tác sẽ trả 409 và **không click/điền**.
   Chủ máy xem lại trang và duyệt yêu cầu mới.
5. Click được OpenCLI báo thành công **không chứng minh** bài đăng/đơn
   hàng được nền tảng chấp nhận. Cần kiểm tra trạng thái trên website hoặc
   biên nhận chính thức; không tự retry với yêu cầu có thể tạo bản ghi trùng.

Các kiểm tra tự động trên GitHub không thể xác minh đăng nhập Chrome
profile cá nhân ở máy của chủ sở hữu. Chúng chỉ xác minh mã, Windows EXE,
CLI đã cài, giao diện và các hợp đồng giả lập.
