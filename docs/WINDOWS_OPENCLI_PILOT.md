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
4. Trong **Thiết lập Robot**, chọn **Khởi chạy Browser Bridge và tìm Chrome profile**. Đây là hành động chủ động của anh, chạy daemon OpenCLI trên máy cục bộ (localhost), có thể duy trì chạy sau khi đóng giao diện. Nếu daemon đã chạy, LHM không tự khởi động lại. Không tự đăng nhập Chrome.
   Phần mềm chỉ hiển thị các profile có extension OpenCLI đang kết nối,
   không tự chọn profile cá nhân. Anh chọn **đúng profile riêng của LHM**;
   ID profile sẽ tự điền vào ô. Không cần lệnh `opencli profile rename`.
5. Nhập website HTTPS được phép (ví dụ `example.com`), bật OpenCLI
   và danh sách host OpenCLI là tập con của danh sách tổng.
   **Lưu và khởi động lại ứng dụng**.
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

## Kiểm tra hợp đồng CLI (bản sửa Windows)

OpenCLI v2.0.2 khai báo `--profile` và `--session` trực tiếp
ở **lệnh con trình duyệt**. LHM gọi CLI theo thứ tự:

```text
node.exe <installed-opencli-main.js> browser state --profile <approved-profile> --session <run-session>
node.exe <installed-opencli-main.js> browser open https://example.com --profile <approved-profile> --session <run-session>
```

LHM không chạy shell; các tham số được truyền dưới dạng argv cố định.
GitHub Windows CI kiểm tra rằng các option này được bản Node/OpenCLI **trong
bộ cài** nhận dạng, chứ không chỉ kiểm tra lệnh `--help` chung.

Mỗi Computer Run được gắn **một session Chrome riêng theo UUID chạy việc**,
tránh trường hợp hai Work Order cùng Content Agent dùng nhầm tab lẫn nhau.
Tuy nhiên các session vẫn hoạt động trong Chrome profile chuyên dụng do
chủ máy chọn: chúng không phải các máy ảo tách biệt, và vẫn có thể chia sẻ
cookie/tài khoản đăng nhập bên trong cùng profile. Mọi click/fill tiếp tục
chờ phê duyệt và kiểm lại URL/nhãn phần tử.

## Website theo từng Agent — cấu hình trực tiếp trên Windows

Trong **Thiết lập Robot → Windows Controller**, nhập danh sách HTTPS tổng
(ví dụ `example.com,crm.example.com`) rồi khai báo riêng:

- **Content Agent:** những trang tạo/sửa nội dung, có thể đề xuất click/fill
  nhưng luôn cần duyệt từ anh.
- **Sales Agent:** website CRM/kinh doanh, chỉ mở và đọc ở Computer Pilot.
- **Quality Agent:** website cần đối chiếu, chỉ mở và đọc ở Computer Pilot.

Danh sách từng vai trò phải là **tập con** của website tổng; để trống
nghĩa là **không cấp quyền truy cập** cho vai trò đó. Cấu hình được
truyền tới cả Windows Native Runner (Playwright + Edge) và OpenCLI;
OpenCLI cũng kiểm tra lại URL sau điều hướng và sau thao tác.
Phải lưu và khởi động lại Robot để kích hoạt. Các role khác chỉ dùng
quyền chung được thiết kế riêng, không thừa hưởng quyền Content/Sales/Quality.

Windows CI chạy kiểm thử `browser_snapshot` với **Edge thực** từ EXE
đóng gói, trên trang trống `about:blank`, không đăng nhập hay mở
Internet. Việc vào website đăng nhập của chủ máy vẫn cần nghiệm thu trên máy thật.
