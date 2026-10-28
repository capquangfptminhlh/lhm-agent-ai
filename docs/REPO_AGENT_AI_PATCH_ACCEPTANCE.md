# AI đề xuất sửa code từ CI — tạo bản vá có duyệt

Luồng mới tại `/repo-agents/ui`: chọn đúng Agent/repo, chẩn đoán
GitHub CI, nhập **đường dẫn file nhỏ đang lỗi**, bấm **AI đề xuất bản vá**,
sau đó **Xem đề xuất AI**, kiểm tra diff và **Lưu bản vá AI để chủ duyệt**.

## 1. Backend thực thi

- `POST /workspaces/{ws}/repo-agents/{agent_id}/ai-suggestions` với
  `workspace`, `diagnosis_id`, `path` và `idempotency_key`.
- Chỉ nhận **báo cáo CI đã hoàn tất** cùng workspace và repository cố định.
- Đọc file UTF-8 từ GitHub `main` cùng SHA commit và SHA blob (GitHub
  read-only), chỉ chấp nhận file tối đa **8.500 ký tự**.
- Chặn path chứa bí mật, nhánh `.github`, file nguy hiểm và các mẫu
  credential phổ biến trước khi gửi nội dung cho model.
- Người dùng phải **xác nhận rõ** việc gửi mã nguồn được chọn cùng nội
  dung chẩn đoán đến OpenRouter hoặc model đã cấu hình. **Không làm tự động
  chỉ vì phát hiện lỗi CI**.
- Worker thật chạy một tác vụ AI theo nghề phù hợp, quota/budget hiện có.
  Không đưa credential GitHub vào prompt. Prompt đánh dấu source/log
  là untrusted data, không cho phép mệnh lệnh ẩn trong mã nguồn.
- Model phải xuất JSON đúng 4 khóa `search, replace, explanation, tests`.
  Chỉ thay một đoạn `search` khớp chính xác duy nhất trong nguồn, độ dài
  giới hạn. Sai JSON, nhiều match, trùng source, quá dài hoặc chứa mẫu
  credential → không tạo bản vá.
- `GET .../ai-suggestions` và `GET .../ai-suggestions/{id}`: trả
  trạng thái Worker, lý do lỗi và diff tạm. Đây chỉ là đề xuất AI,
  không phải xác nhận đã sửa code/đã chạy tests.
- `POST .../ai-suggestions/{id}/stage`: đọc lại HEAD/blob/source, chặn
  nếu main đã thay đổi; lưu **reviewable** `repo_agent_repairs`
  `awaiting_approval` trong SQLite. Chống gửi trùng.
- Cuối cùng, chủ máy xem diff rồi dùng nút duyệt PR nháp của PR #47.
  `LHM_ADMIN_TOKEN` + `LHM_GITHUB_WRITE_TOKEN` bắt buộc. Không
  có auto-merge, auto-retry khi không chắc GitHub đã nhận lệnh.

## 2. Các ranh giới còn lại

- AI hiện **đề xuất một thay đổi trong một file** chứ không tự sửa toàn
  repository hoặc chạy bộ test của repository mục tiêu.
- Kiểm thử CI của hệ thống LHM Agent và đóng gói Windows **không thay**
  kiểm thử thực tế bản vá của `crmminh`, `autotax`, `ggminh`...
- Không gọi GitHub write API trong tác vụ AI; chỉ stage/chờ duyệt.
- GitHub private repo cần read token backend và có thể chứa source nhạy
  cảm: chọn file rõ ràng, không gửi source hoặc secret khi chưa được phép.
- Chưa nghiệm thu GitHub write token sản xuất hay chạy Agent trên máy
  Windows riêng của người dùng.
- API đang trong local-admin mode, không phải tenant SaaS bảo mật đầy đủ.

## 3. Kiểm thử

Các bài kiểm tra dùng FastAPI + SQLite + Worker thực, mô phỏng chỉ HTTP
GitHub và model AI: kiểm tra nguồn đọc trước, prompt bound, tác vụ
tạo hàng đợi, parsed diff, stage có duyệt, tránh trùng, file/blob/main
thay đổi, JSON bị lỗi, đường dẫn bí mật, giới hạn dữ liệu và JavaScript.
CI Linux unit / Computer Runner / Chrome WebGL / Windows installer+GUI
phải thành công trước merge.
