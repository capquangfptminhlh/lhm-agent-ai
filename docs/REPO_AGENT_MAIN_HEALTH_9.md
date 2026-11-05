# Kiểm tra CI 9 repository và giao Agent chẩn đoán lỗi

Giao diện: `/repo-agents/ui`.

## Mục đích

Mỗi Agent theo repo chỉ đọc GitHub Actions của repository cố định. Các run
được lọc trên nhánh `main`, tránh kết luận sai dựa trên một PR build.
**Một workflow Deploy PASS không thể che workflow SEO/QA FAIL**: hệ thống
chọn workflow khác tên gần nhất trong tối đa 10 run nhánh main và kiểm tra
job/step của tối đa 4 workflow; ưu tiên workflow báo lỗi.

Nút **Kiểm tra CI cả 9 repository** đọc đồng thời tối đa 3 repo và
trả kết quả từng repo độc lập. Repo private cần token GitHub chỉ đọc
(`LHM_GITHUB_READ_TOKEN`) với quyền Actions; không nhập token trên UI.

Nút **Giao AI chẩn đoán repo đang lỗi (tối đa 4)** chỉ hoạt động sau
xác nhận chủ máy, kiểm tra lại trạng thái và tạo tối đa 4 tác vụ AI chẩn
đoán từ repo có `failure_observed`. Tác vụ gắn duy nhất
`(workspace, agent, run_id)`; bấm lại không sinh việc trùng.
Việc đọc CI không bao giờ tạo commit hoặc Pull Request.

## Giới hạn bằng chứng

- `overall_health=failure_observed`: ít nhất một workflow main quan sát
  trong snapshot có kết luận lỗi, kể cả workflow gần nhất đã xanh.
- `running_or_queued`: có ít nhất một workflow đang chạy, không kết luận
  thành công.
- `sampled_workflows_green`: các workflow phân biệt trong *mẫu giới hạn*
  đều PASS và phần job được đọc đầy đủ. Không có nghĩa mọi workflow,
  máy tính cá nhân hoặc hành động kinh doanh đã nghiệm thu.
- `unverified`: không đủ dữ liệu; một workflow xanh không đủ để báo
  toàn repo đã khỏe.
- `job_details_complete=false`: dữ liệu job có thể phân trang/thiếu;
  không suy diễn không có job lỗi.
- Tối đa 4 workflow khác nhau và 10 run gần nhất, không tải log đầy đủ
  (có thể chứa bí mật). Người phụ trách cần mở URL GitHub để kiểm tra logs
  và bản vá chuyên môn.

## Acceptance

- Test fixture newest Deploy PASS + previous Editorial FAIL:
  `failure_observed`, ghi đúng run ID, job/step lỗi và URL trong repo.
- Test 9 repo song song có 1 repo không truy cập: không giả mạo "xanh".
- Test batch 5 repo lỗi: đúng 4 tác vụ xếp hàng, lần gọi thứ hai
  deduplicate, không tạo PR và không phát sinh external action.
- Chạy toàn bộ Python tests, Computer sidecar, Chrome/WebGL,
  Windows NSIS installer + Electron GUI; nghiệm thu máy thật vẫn
  cần kiểm tra đăng nhập Chrome/nhà cung cấp và API key.
