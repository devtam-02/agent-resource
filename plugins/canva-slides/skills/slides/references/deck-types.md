# Deck types

Khung gợi ý; bỏ slide không có nội dung thật, không thêm slide cho đủ khung.

## `sprint-review` — tổng kết sprint / demo

Nguồn: JSON của `dev-cli --jira-tool scan-issues` (sprint đang chạy), git log của sprint.

1. Tiêu đề: tên sprint, ngày, nhóm.
2. Mục tiêu sprint và kết quả một câu (đạt / đạt một phần / chưa đạt).
3. Đã xong: gom theo `parent` (epic/story), không liệt kê từng issue nhỏ. Số `Done`/tổng từ `summary.by_status_category`.
4. Demo: 1–3 tính năng, mỗi cái một slide, có `Visual:` là ảnh màn hình/luồng.
5. Thời gian: ước lượng vs đã log từ `summary.time`; issue `over_estimate` đáng nói.
6. Chưa xong và lý do: issue còn mở, `overdue`, `stale`.
7. Rủi ro / vướng mắc cần hỗ trợ.
8. Sprint tới: mục tiêu, việc chuyển sang.

## `tech-design` — trình bày thiết kế / xin chốt phương án

Nguồn: spec, HLD/LLD, ADR, C4.

1. Tiêu đề.
2. Vấn đề và tác động (có số: độ trễ, lỗi, chi phí, thời gian làm tay).
3. Ràng buộc: NFR, hệ thống liên quan, những gì không được đổi.
4. Bối cảnh hệ thống (C4 mức 1, rút gọn).
5. Các phương án (2–3) so sánh trên cùng tiêu chí: bảng.
6. Phương án chọn và lý do.
7. Kiến trúc / luồng chính (C4 mức 2 hoặc sequence rút gọn).
8. Dữ liệu và giao dịch: thay đổi schema, idempotency, rollback.
9. Rủi ro và cách giảm.
10. Kế hoạch triển khai: giai đoạn, feature flag, đo lường.
11. Cần chốt: quyết định, người duyệt, hạn.

## `tool-intro` — giới thiệu công cụ / đề xuất

1. Tiêu đề. 2. Việc đang làm tay và tốn bao lâu. 3. Công cụ làm gì (một câu + demo).
4. Cách dùng: 2–3 lệnh thật. 5. Cách hoạt động (sơ đồ đơn giản). 6. An toàn: dữ liệu, secret, quyền.
7. Cài đặt. 8. Lộ trình. 9. Cần gì từ người nghe.

## `incident` — sự cố / postmortem (không đổ lỗi cá nhân)

1. Tiêu đề, mức độ. 2. Tác động: thời gian, phạm vi người dùng/giao dịch. 3. Dòng thời gian
(phát hiện → xử lý → khôi phục). 4. Nguyên nhân gốc. 5. Vì sao không phát hiện sớm hơn.
6. Đã sửa gì. 7. Phòng ngừa: việc, người, hạn.

## `knowledge` — chia sẻ kiến thức / đào tạo

1. Tiêu đề. 2. Vì sao cần biết (một tình huống thật). 3–5. Khái niệm chính, mỗi khái niệm một
slide có ví dụ. 6. Lỗi hay gặp. 7. Bài tập hoặc checklist. 8. Tài liệu đọc thêm.

## `status` — báo cáo tiến độ cho quản lý

1. Tiêu đề. 2. Tình trạng tổng: xanh/vàng/đỏ, một câu. 3. Tiến độ so với kế hoạch (mốc, %).
4. Đã xong kỳ này. 5. Rủi ro, vấn đề và đề xuất. 6. Cần quyết định/hỗ trợ. 7. Kỳ tới.
