# M2 — Lưu và phát lại bằng chứng trên máy chủ sở hữu

**Trạng thái: thử nghiệm, mặc định TẮT.** Chưa phải chứng nhận che ảnh cho mọi loại ứng dụng Windows, chưa thay thế nghiệm thu P4 trên máy chủ. PR #26 vẫn ở dạng Draft.

## Quy trình thực hiện

1. Vào **Thiết lập Robot Desktop → Bằng chứng ảnh riêng tư (thử nghiệm M2)**, bật lưu ảnh trước/sau. Restart app. Bản cài phải lấy từ build CI chứa thay đổi này.
2. Chỉ sử dụng trang test và ứng dụng có dữ liệu giả. Mọi bước Windows luôn bị giới hạn bởi allowlist, quyền foreground và chính sách phê duyệt. Những bước không tạo được ảnh che an toàn **dừng để chủ xử lý**, không tự click lại.
3. Trong Computer Console, chọn phiên và bấm **Xuất audit ZIP (đã che ảnh)**. Đây là hành động do chủ tự bấm, không tự gửi lên mạng.
4. Giải nén, mở `index.html` bằng trình duyệt trên chính máy này; đối chiếu `manifest.json` và từng `SHA-256`. Màn hình đánh dấu **Thiếu ảnh PRE/POST** nếu không có ảnh đã xác minh. Không suy ra thành công khi thiếu ảnh.
5. Có thể xuất qua dòng lệnh (không cần key): `python audit_replay.py <run-id> C:\\Users\\<user>\\Documents\\LHM-evidence.zip --db C:\\path\\to\\lhm.sqlite3`. Không ghi đè ZIP có sẵn.

## Cam kết kỹ thuật và những gì chưa chứng minh

- Runner có tool `browser_evidence` dùng Playwright mask các ô nhập, iframe, ảnh, vùng contenteditable và từ chối trang có dấu hiệu thông tin tài khoản/thanh toán ở phần văn bản; `windows_evidence` dùng UIA và từ chối khi không xác định vùng cần che. **Không khẳng định** các heuristic này ngăn được mọi biến thể secret trong ứng dụng tùy biến.
- Chỉ lưu ảnh có cờ `redacted: true`. Ảnh PNG được lưu riêng dưới `evidence/<run>/<step>-pre/post.png` với SHA-256; bước SQLite chỉ giữ đường dẫn/hash và metadata, không nhét ảnh base64.
- Gói ZIP chỉ bao gồm metadata tối thiểu và ảnh được xác minh hash; không chứa objective, tham số lệnh, API key hay toàn bộ kết quả AI. Giới hạn 64 MB, không thay thế ảnh thiếu bằng placeholder giả, không chấp nhận đường dẫn/symlink lệch gốc.
- **Phải tự xem ZIP trước khi chia sẻ**; gói không được mã hóa. Ảnh và thông tin giao diện dù đã che vẫn có thể nhạy cảm. Không tải artifact ZIP của phiên cá nhân lên GitHub, public storage hoặc gửi cho ai chưa được phép.
- Emergency Stop và rủi ro side effect vẫn phải nghiệm thu riêng. Trong CI, phím nóng toàn hệ thống `Ctrl+Alt+Pause` được bỏ qua vì API Windows runner treo trước khi app khởi động. Điều này **không chứng minh** phím nóng hoạt động ngoài thực tế; phải thử trên máy Windows đăng nhập của chủ.
- M2 chỉ đủ điều kiện merge khi các bài test bảo mật và các bài GUI trên Windows đều PASS, có kiểm tra trực tiếp P4/T5, và mọi hành động gửi/đăng/xóa/thanh toán vẫn xin duyệt.

Xem [Kế hoạch M0–M5](EXECUTION_M0_M5.md), [10 bài nghiệm thu P4](P4-ACCEPTANCE.md), [Issue #20](https://github.com/capquangfptminhlh/lhm-agent-ai/issues/20).
