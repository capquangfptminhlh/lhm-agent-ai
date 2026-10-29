# PR CI annotations — bằng chứng GitHub thực cho Agent sửa lỗi

Từ giao diện `/repo-agents/ui` → chọn repository → **Bản vá & PR nháp**
→ **Kiểm tra CI của PR trên GitHub** → **Giao Agent phân tích CI lỗi của PR**.
Người dùng xác nhận việc gửi **dữ liệu lỗi được giới hạn** cho model AI
đã cấu hình (ưu tiên OpenRouter Free theo chế độ test).

## Khắc phục thiếu sót

PR #50–51 chỉ đưa tên các check thất bại vào báo cáo Worker.
Mã mới bổ sung **Check Runs Annotations** của GitHub, thường gồm file,
số dòng, mức lỗi và thông báo kiểm thử do GitHub Actions công bố. Những
dữ liệu này giúp chẩn đoán có căn cứ hơn, nhưng **không phải full log**.

* Chỉ truy vấn check-run ID **có head_sha trùng chính xác HEAD của PR đã xác minh**.
  Không nhận URL hoặc ID do người dùng/model tự cung cấp.
* Chỉ gọi API đọc `/repos/{fixed_repo}/check-runs/{id}/annotations` với
  `follow_redirects=False`, `trust_env=False`, giới hạn 3 check thất bại,
  tối đa 6 annotation an toàn mỗi check.
* Chỉ giữ file path thuộc allowlist source, số dòng hợp lệ,
  severity và thông báo tối đa 350 ký tự; bỏ annotation chứa mẫu secret,
  đường dẫn `.github`, `.env` và những file bị chặn.
* Tên check/context cũng được lọc các mẫu token trước khi cho Worker;
  annotation được nhắc rõ là **dữ liệu không tin cậy** (chống prompt injection).
* Nếu GitHub trả lỗi/missing scopes/không có annotation, job triage vẫn
  có thể chạy với tên check, nhưng phải ghi **thiếu bằng chứng**, không
  được suy diễn đã có full log hay khẳng định nguyên nhân.
* Giao diện hiển thị file/dòng lỗi quan sát được và cảnh báo thiếu dữ liệu.
* Mỗi PR+head SHA chỉ có một task triage. Một commit mới tạo vòng mới
  nếu vẫn có lỗi CI. Chỉ sửa cùng PR khi anh xem diff và cho phép.

## Phạm vi nghiệm thu

Các integration test FastAPI/SQLite/Worker và GitHub HTTP mock bao gồm
annotation có dòng/file hợp lệ, redaction thông tin nhạy cảm, sai SHA
không được dereference, lỗi quyền 403 không được giả làm PASS.
Bộ test Linux, Docker smoke, Computer Runner, Chrome/WebGL, Electron GUI
và installer Windows tiếp tục là gate trước merge.

**Giới hạn:** GitHub Checks Annotations có thể không có thông tin hoặc
bị phân trang; không thay thế Actions job logs, chưa thực thi unit test
của repository đích trong sandbox và chưa kiểm nghiệm token sản xuất.
