# P4 — Nghiệm thu 10 bài trên máy Windows thật của chủ sở hữu

Chuẩn GrokBot Parity+ (Issue #20) chỉ được công nhận khi bộ bài này chạy
trên **máy Windows 10/11 thật, đã đăng nhập**, bằng bản cài đóng gói từ CI
của PR chứa thay đổi — không tính kết quả CI headless, không tính máy ảo
của đội phát triển.

## Chuẩn bị (làm 1 lần)

1. Cài bản Windows từ artifact CI mới nhất của nhánh/PR cần nghiệm thu.
2. Mở Cài đặt của app và đặt:
   - OpenRouter API key của chủ; model giữ `openrouter/free`.
   - Ứng dụng được phép: `notepad.exe, explorer.exe, calc.exe, msedge.exe`
     (thêm `code.exe` nếu muốn thử với editor thật).
   - Website được phép: `example.com` (bài 4 dùng trang công khai vô hại).
   - Bật **Nhận dạng giao diện (vision)** và khai 1 model `:free` có ảnh —
     chỉ cần cho bài 10 (grounding).
   - Bật **Ảnh toàn desktop** — chỉ cần cho bài 8 (tọa độ desktop).
   - **Tin cậy: để TẮT** trong vòng nghiệm thu đầu (mọi bài chạy Giám sát,
     duyệt từng bước như thiết kế).
3. Tạo sẵn thư mục trống `C:\Users\<bạn>\Documents\LHM-P4` để làm bài 2/9.
4. Quy tắc ghi kết quả: mỗi bài ghi PASS/FAIL + 1 ảnh chụp màn hình kết quả
   + số bước Agent dùng. FAIL thì ghi nguyên văn lỗi/bước chết, không sửa
   bài để "cho qua".

## 10 bài (dán nguyên văn vào ô Mục tiêu của Computer Console)

| # | Mục tiêu dán vào Console | Điều kiện PASS |
|---|---|---|
| 1 | Mở Notepad, gõ dòng "LHM P4 bai 1" rồi lưu thành file `p4-bai1.txt` trong thư mục Documents\LHM-P4 bằng hộp thoại lưu của Notepad. | File tồn tại đúng đường dẫn, nội dung đúng 1 dòng; Agent tự thao tác hộp thoại lưu, không dùng terminal thay thế. |
| 2 | Trong Explorer tại thư mục Documents\LHM-P4: tạo thư mục `thu-muc-moi`, đổi tên thành `da-doi-ten`, rồi di chuyển `p4-bai1.txt` vào trong thư mục đó. | Cây thư mục cuối đúng cả 3 thao tác; thao tác kéo-thả hoặc menu chuột phải đều tính, miễn là qua GUI Explorer. |
| 3 | Mở Calculator, tính 123 + 456 bằng cách bấm nút trên giao diện, và cho biết kết quả hiển thị. | Kết quả báo về = 579 và khớp số trên màn hình Calculator trong ảnh nộp kèm. |
| 4 | Mở trình duyệt tới https://example.com, chụp lại tiêu đề trang và đoạn chữ đầu tiên của trang vào file `p4-bai4.md` trong sandbox của Agent. | File có tiêu đề "Example Domain" và đoạn chữ khớp trang thật; URL ngoài allowlist bị chặn nếu Agent thử (ghi lại). |
| 5 | Mở Notepad và Calculator cùng lúc, dùng Alt+Tab để chuyển qua lại giữa hai ứng dụng ít nhất 2 lần, kết thúc ở Notepad và gõ thêm dòng "da chuyen app". | Cả hai lần chuyển đều bằng Alt+Tab (thấy trong nhật ký bước là `windows_hotkey` ALT+TAB); file Notepad có thêm dòng chữ. |
| 6 | Trong Notepad đang mở: chọn toàn bộ văn bản (Ctrl+A), sao chép (Ctrl+C), xuống dòng mới và dán (Ctrl+V) để văn bản được nhân đôi. | Nội dung cuối = nội dung đầu × 2; nhật ký bước thể hiện đúng 3 phím tắt. |
| 7 | Chuột phải vào một file bất kỳ trong Explorer để mở menu ngữ cảnh, đọc các mục trong menu rồi đóng menu bằng Esc mà không chọn mục nào. | Agent báo đúng ít nhất 5 mục menu nhìn thấy; không có thao tác nào được thực thi từ menu. |
| 8 | Bật quyền toàn desktop trước bài này. Đóng tất cả cửa sổ ứng dụng bằng thao tác trên desktop (không dùng Task Manager/terminal) để lộ màn hình desktop, rồi chụp ảnh toàn desktop. | Ảnh nộp kèm là desktop trống (thấy hình nền + icon), không còn cửa sổ ứng dụng; bước dùng công cụ tọa độ desktop `windows_desktop_*`. |
| 9 | Việc dài: trong Documents\LHM-P4, tạo 5 file `n1.txt`…`n5.txt` bằng Notepad, mỗi file chứa đúng dòng "file so N" (N là số của file), lưu và đóng Notepad sau mỗi file. | Cả 5 file đúng tên + đúng nội dung; bài chạy liên tục ≥ 15 bước không kẹt ở trạng thái needs_review. |
| 10 | Dùng `vision_locate` để tìm nút "Save" (hoặc biểu tượng lưu) trong cửa sổ Notepad đang mở và bấm vào đúng vị trí đó để lưu file hiện tại. | Nhật ký có bước `vision_locate` trả `found: true`, bước click sau đó dùng tọa độ gần khớp kết quả locate (sai lệch ≤ 40px), và file được lưu thật (thời gian sửa file thay đổi). |

## Sau khi chạy

- Điền bảng kết quả (PASS/FAIL từng bài + link ảnh) vào Issue #20.
- Điều kiện công nhận Parity ở tầng thao tác: **≥ 9/10 PASS**, và 2 bài
  bắt buộc phải PASS là bài 1 (lưu file qua GUI) và bài 9 (việc dài).
- Mọi FAIL đều mở việc sửa riêng; không hạ chuẩn, không sửa bài thi.
- Chỉ sau khi bộ bài này PASS trên bản cài đó, vòng nghiệm thu Tin cậy
  (P2 gate trong Issue #20) mới bắt đầu: chạy lại bài 1, 6, 9 ở chế độ
  Tin cậy và kiểm tra checkpoint bước 25 + các điểm dừng nhạy cảm.
