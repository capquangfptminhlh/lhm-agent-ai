# Cập nhật trạng thái tích hợp (2026-10-27)

Đã viết, kết nối vào Tool Hub và test offline thêm 4 adapter:
**Crawl4AI, Twenty CRM, Chatwoot, ComfyUI**. Với ba adapter có từ trước
(Postiz, WordPress, Activepieces), tổng cộng **7 nhóm adapter có mã nguồn**.
**Chưa có nghĩa đã cài cả repo hoặc kết nối tài khoản thật.**

Agent có thể gắn một nhiệm vụ `completed` vào specialist tool theo quyền
vai trò tại `/agent-tools`. Lệnh được lưu riêng và luôn chờ người duyệt.
Không có “autonomous full-access tool calling” hoặc tự gọi tất cả 19 repo.
Các hàng “Đề xuất” bên dưới phản ánh báo cáo kiểm kê ban đầu, không
phản ánh đầy đủ trạng thái sau bản cập nhật này. Trạng thái từng connector
đang lấy từ `templates/tool_catalog.json` và `/tools/specialists/readiness`.

---

# Báo cáo kiểm kê toàn bộ đầu việc Agent và công cụ phù hợp

**Phạm vi:** 16 vai trò, 8 phòng ban, 66 đầu mục, 19 repo/công cụ. Bản đồ khả năng không khẳng định tất cả đã cài.

## Cách hiểu trạng thái

- `adapter_built`: đã viết adapter HTTP tương ứng trong LHM; cần token, quyền truy cập và nghiệm thu tại môi trường thật. KHÔNG lấy mã nguồn repo này về cài tự động.
- `recommended`: đã chọn giải pháp tham khảo, chưa cài hoặc đồng bộ vào LHM.
- `official_api_required`: bắt buộc đăng ký, xác thực và được nền tảng cấp quyền API.
- `existing_partial`: văn phòng 2D LHM đã gắn trạng thái; phần Hermes3D 3D chưa nghiệm thu end-to-end.

## Ma trận 16 Agent × công việc × giải pháp

### Điều hành (`director`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Phân rã mục tiêu doanh thu | Lập mục tiêu, KPI, phân công có hạn chót | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |
| Theo dõi việc đang tắc | Lấy trạng thái công việc thật | [Hermes3D](https://github.com/iamlukethedev/Hermes3D) | Đề xuất |
| Duyệt chi tiêu/công bố | Phê duyệt từng hành động rủi ro | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |
| Báo cáo tiến độ toàn công ty | Tổng hợp công việc và doanh thu | [Metabase](https://github.com/metabase/metabase) | Đề xuất |

### Nghiên cứu (`research`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Tìm xu hướng theo ngành | Đọc dữ liệu công khai có nguồn | [Crawl4AI](https://github.com/unclecode/crawl4ai) | Đề xuất |
| Theo dõi đối thủ và bảng giá | So sánh theo ngày và nguồn | [Crawl4AI](https://github.com/unclecode/crawl4ai) | Đề xuất |
| Nghiên cứu thị trường địa phương | Xác thực khu vực/giá/cơ sở pháp lý | [Crawl4AI](https://github.com/unclecode/crawl4ai) | Đề xuất |
| Phân tích tài liệu ngành | Đọc và luân chuyển báo cáo | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |

### Chiến lược (`strategy`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Phân tích thị trường mục tiêu | Tổng hợp dữ liệu và giả định | [Crawl4AI](https://github.com/unclecode/crawl4ai) | Đề xuất |
| Đề xuất ngân sách đa kênh | Phân bổ theo ROI, CAC, biên lợi nhuận | [Metabase](https://github.com/metabase/metabase) | Đề xuất |
| Thiết kế kế hoạch quý | Chuyển mục tiêu thành kế hoạch | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |
| Thử nghiệm thông điệp | Lập thử nghiệm nội dung có duyệt | [Postiz](https://github.com/gitroomhq/postiz-app) | Đã có adapter HTTP |

### Tìm khách (`lead`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Tìm doanh nghiệp phù hợp | Thông tin doanh nghiệp công khai; đúng điều khoản | [Crawl4AI](https://github.com/unclecode/crawl4ai) | Đề xuất |
| Thu lead tự nguyện qua biểu mẫu | Nhận webhook/form đã được đồng ý | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |
| Kiểm tra nguồn và chất lượng lead | Chấm điểm có nguồn gốc | [Twenty CRM](https://github.com/twentyhq/twenty) | Đề xuất |
| Phân loại và giao cho Sales | Đẩy lead, chống trùng và đặt SLA | [Twenty CRM](https://github.com/twentyhq/twenty) | Đề xuất |

### Nội dung (`content`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Viết SEO và bài blog | Soạn → duyệt → lưu nháp/đăng | [WordPress REST API](https://github.com/WordPress/wordpress-develop) | Đã có adapter HTTP |
| Viết caption đa nền tảng | Tối ưu theo Facebook/Instagram/TikTok/LinkedIn | [Postiz](https://github.com/gitroomhq/postiz-app) | Đã có adapter HTTP |
| Lên lịch bài social | Lên lịch qua API chính thức sau khi duyệt | [Postiz](https://github.com/gitroomhq/postiz-app) | Đã có adapter HTTP |
| Thiết kế banner và hình ảnh | Cần media API/model riêng | [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | Đề xuất |
| Dựng video và kịch bản | Cần bổ sung render video cụ thể | [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | Đề xuất |

### Quảng cáo (`ads`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Lấy dữ liệu Google Ads | Đọc hiệu suất khi được cấp OAuth | [Google Ads API client](https://github.com/googleads/google-ads-python) | Đề xuất |
| Lấy dữ liệu Meta Ads | Đọc số liệu theo quyền | [Meta Business SDK](https://github.com/facebook/facebook-python-business-sdk) | Đề xuất |
| Đánh giá ngân sách và CAC | So sánh lead chất lượng với chi phí | [Metabase](https://github.com/metabase/metabase) | Đề xuất |
| Đề xuất thay đổi quảng cáo | Duyệt trước mọi thay đổi ngân sách | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |

### CRM (`crm`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Nhập và chống trùng lead | Phân biệt lead nguồn và consent | [Twenty CRM](https://github.com/twentyhq/twenty) | Đề xuất |
| Theo dõi trạng thái pipeline | Giai đoạn bán hàng, giá trị dự kiến | [Twenty CRM](https://github.com/twentyhq/twenty) | Đề xuất |
| Nuôi dưỡng khách có đồng ý | Email theo hành vi hợp lệ | [Mautic](https://github.com/mautic/mautic) | Đề xuất |
| Đồng bộ chat vào hồ sơ khách | Lưu tương tác theo quyền và mục đích | [Chatwoot](https://github.com/chatwoot/chatwoot) | Đề xuất |

### Kinh doanh (`sales`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Xác minh nhu cầu và ngân sách | Ghi câu hỏi tư vấn | [Twenty CRM](https://github.com/twentyhq/twenty) | Đề xuất |
| Đặt lịch xem nhà/gặp khách | Đồng bộ lịch và người phụ trách | [Cal.com](https://github.com/calcom/cal.com) | Đề xuất |
| Soạn báo giá/đề xuất | Dựa trên tồn kho và bảng giá thật | [ERPNext](https://github.com/frappe/erpnext) | Đề xuất |
| Theo dõi cơ hội và chốt đơn | Đối soát doanh thu, không tự ký | [Twenty CRM](https://github.com/twentyhq/twenty) | Đề xuất |

### Kiểm duyệt (`quality`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Kiểm tra lỗi sự thật | Đối chiếu nguồn | [Crawl4AI](https://github.com/unclecode/crawl4ai) | Đề xuất |
| Kiểm tra bài trước đăng | Chặn công bố khi thiếu phê duyệt | [Postiz](https://github.com/gitroomhq/postiz-app) | Đã có adapter HTTP |
| Kiểm soát tính nhất quán thương hiệu | Checklist phê duyệt | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |
| Kiểm tra chất lượng output AI | Lưu đánh giá/tracing | [Langfuse](https://github.com/langfuse/langfuse) | Đề xuất |

### Phân tích (`analytics`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Tổng hợp số liệu marketing | Leads, traffic, CPA, CAC, conversion | [Metabase](https://github.com/metabase/metabase) | Đề xuất |
| Báo cáo Search Console | Query, impressions, CTR, position | [Google Search Console API](https://github.com/googleapis/google-api-python-client) | Đề xuất |
| Báo cáo social post | Lượt đăng và số liệu hỗ trợ | [Postiz](https://github.com/gitroomhq/postiz-app) | Đã có adapter HTTP |
| Đo chi phí và lỗi AI | Token, tỷ lệ fail, latency | [Langfuse](https://github.com/langfuse/langfuse) | Đề xuất |
| A/B testing và cảnh báo | Đặt ngưỡng và thông báo | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |

### Vận hành (`operations`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Tạo SOP và checklist | Quy trình theo phòng ban | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |
| Theo dõi tiến độ thực thi | Trạng thái Agent và nhiệm vụ | [Hermes3D](https://github.com/iamlukethedev/Hermes3D) | Đề xuất |
| Tự chạy tác vụ định kỳ | Webhook/scheduler từ tool | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |
| Phối hợp bàn giao giữa phòng ban | Workflow, retry, biên nhận | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |

### Tài chính (`finance`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Đọc doanh thu và chi phí | Báo cáo từ sổ sách có xác minh | [ERPNext](https://github.com/frappe/erpnext) | Đề xuất |
| Phân tích công nợ | Số liệu kế toán thật | [ERPNext](https://github.com/frappe/erpnext) | Đề xuất |
| Dự báo dòng tiền | Kịch bản và cảnh báo | [Metabase](https://github.com/metabase/metabase) | Đề xuất |
| Đề xuất ngân sách | Duyệt mọi khoản chi | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |

### Nhân sự (`hr`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Xây JD và tiêu chí tuyển dụng | Mô tả và quy trình duyệt | [Frappe HRMS](https://github.com/frappe/hrms) | Đề xuất |
| Theo dõi pipeline tuyển dụng | Bảo vệ dữ liệu ứng viên | [Frappe HRMS](https://github.com/frappe/hrms) | Đề xuất |
| Lập kế hoạch đào tạo | Theo năng lực cần đạt | [Frappe HRMS](https://github.com/frappe/hrms) | Đề xuất |
| Đánh giá hiệu quả nhóm | KPI có giải thích, tránh quyết định tự động | [Metabase](https://github.com/metabase/metabase) | Đề xuất |

### Mua hàng (`procurement`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Tìm nhà cung cấp | Bằng chứng công khai, không spam | [Crawl4AI](https://github.com/unclecode/crawl4ai) | Đề xuất |
| So sánh báo giá và MOQ | Chuẩn hóa đơn giá, thuế, vận chuyển | [ERPNext](https://github.com/frappe/erpnext) | Đề xuất |
| Đề xuất đặt hàng | Dựa trên tồn kho và dự báo | [ERPNext](https://github.com/frappe/erpnext) | Đề xuất |
| Tạo yêu cầu mua hàng | Phải duyệt trước khi đặt thật | [ERPNext](https://github.com/frappe/erpnext) | Đề xuất |

### Chăm sóc khách (`support`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Nhận chat đa kênh | Hộp thư tập trung | [Chatwoot](https://github.com/chatwoot/chatwoot) | Đề xuất |
| Phân loại và gán yêu cầu | SLA và tuyến xử lý | [Chatwoot](https://github.com/chatwoot/chatwoot) | Đề xuất |
| Soạn phản hồi theo KB | Con người duyệt ca nhạy cảm | [Chatwoot](https://github.com/chatwoot/chatwoot) | Đề xuất |
| Theo dõi khiếu nại và tái liên hệ | Trạng thái và phản hồi hợp lệ | [Twenty CRM](https://github.com/twentyhq/twenty) | Đề xuất |

### Tuân thủ (`compliance`)

| Công việc | Đầu ra cần xác nhận | Repo / tool | Mức tích hợp |
|---|---|---|---|
| Kiểm tra quyền liên hệ lead | Consent/mục đích/nguồn | [Twenty CRM](https://github.com/twentyhq/twenty) | Đề xuất |
| Kiểm duyệt quảng cáo và tài liệu | Checklist ngành và luật | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |
| Quản lý duyệt thay đổi có rủi ro | Audit trail, quyền người duyệt | [Activepieces Community](https://github.com/activepieces/activepieces) | Đã có adapter HTTP |
| Kiểm tra log và dữ liệu | Giới hạn thông tin cá nhân trong trace | [Langfuse](https://github.com/langfuse/langfuse) | Đề xuất |

## Danh sách repo, tình trạng và bản quyền

| Repo | Giấy phép đã ghi nhận | Ưu tiên | Trạng thái |
|---|---|---|---|
| [Hermes3D](https://github.com/iamlukethedev/Hermes3D) | MIT | optional | existing_partial |
| [Postiz](https://github.com/gitroomhq/postiz-app) | AGPL-3.0 | P0 | adapter_built |
| [Activepieces Community](https://github.com/activepieces/activepieces) | MIT core | P0 | adapter_built |
| [WordPress REST API](https://github.com/WordPress/wordpress-develop) | GPL-2.0-or-later | P0 | adapter_built |
| [Twenty CRM](https://github.com/twentyhq/twenty) | AGPL-3.0/core; some commercial | P1 | recommended |
| [Chatwoot](https://github.com/chatwoot/chatwoot) | MIT core | P1 | recommended |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | Apache-2.0 | P1 | recommended |
| [Mautic](https://github.com/mautic/mautic) | GPL-3.0 | P2 | recommended |
| [ERPNext](https://github.com/frappe/erpnext) | GPL-3.0 | P2 | recommended |
| [Frappe HRMS](https://github.com/frappe/hrms) | GPL-3.0 | P2 | recommended |
| [Metabase](https://github.com/metabase/metabase) | AGPL-3.0/core; enterprise separate | P2 | recommended |
| [Langfuse](https://github.com/langfuse/langfuse) | MIT core; enterprise separate | P1 | recommended |
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | GPL-3.0 | P2 | recommended |
| [Cal.com](https://github.com/calcom/cal.com) | Mixed/license review | P2 | recommended |
| [Documenso](https://github.com/documenso/documenso) | Mixed/license review | P3 | recommended |
| [Google Ads API client](https://github.com/googleads/google-ads-python) | Apache-2.0 | P1 | official_api_required |
| [Meta Business SDK](https://github.com/facebook/facebook-python-business-sdk) | BSD-3-Clause | P1 | official_api_required |
| [Google Search Console API](https://github.com/googleapis/google-api-python-client) | Apache-2.0 | P1 | official_api_required |
| [Zalo Official Account APIs](https://developers.zalo.me/) | Developer terms | P2 | official_api_required |

**Lưu ý pháp lý:** thông tin giấy phép là sơ bộ theo repo hoặc bản công khai; rà lại LICENSE chính xác tại commit đang phát hành. AGPL/GPL hoặc Open Core có nghĩa vụ riêng; ưu tiên giao tiếp API với bản triển khai ngoài thay vì sao chép mã nguồn vào sản phẩm đóng. Riêng nền tảng Zalo/Google/Meta cần tuân thủ điều kiện sử dụng API.

## Thiết kế 1 DeepSeek API + 3 lớp thực thi

1. **AI:** 16 role prompts dùng chung một model DeepSeek; chỉ phát sinh token khi xử lý tác vụ.
2. **LHM Tool Registry:** ánh xạ từng đầu việc sang công cụ được cấp quyền, kiểm tra đầu vào, trạng thái, idempotency, phê duyệt.
3. **Các hệ thống chuyên dụng:** Postiz/WordPress/Activepieces là P0; Twenty/Chatwoot/Analytics/ERP là mô-đun tùy doanh nghiệp, không bắt buộc cài toàn bộ.

## Mốc nghiệm thu P0

- [x] Đăng ký công cụ trong `/tools/catalog`; ma trận 16 vai trò và 66 đầu việc.
- [x] Postiz: adapter lấy kênh và gửi lệnh tạo bài cho nhiều kênh, có `now`, `draft`, `schedule`.
- [x] WordPress REST: adapter tạo post draft hoặc publish theo người duyệt.
- [x] Activepieces: adapter kích hoạt một webhook workflow đã cấu hình.
- [x] Chặn tác vụ ghi ra ngoài khi chưa duyệt, idempotency theo doanh nghiệp, không gửi lại khi timeout không rõ kết quả.
- [x] Chuyển nội dung từ nhiệm vụ AI đã hoàn thành vào yêu cầu xuất bản (vẫn cần người duyệt).
- [x] Snapshot văn phòng AI bao gồm số tool đang chờ và trạng thái thực.
- [ ] Test OAuth/API key thật với tài khoản Postiz/WordPress/Activepieces của chủ sở hữu.
- [ ] Đối soát công khai URL và phản hồi callback của nền tảng.
- [ ] Bảo mật SaaS đa tenant, encryption/RBAC, backup PostgreSQL.
- [ ] Đưa P1, P2 lên thành API adapter thật từng bộ phận.