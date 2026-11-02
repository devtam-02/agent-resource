---
name: export
description: >
  Export an existing Canva design (usually a presentation) to PDF, PPTX or PNG through the Canva
  MCP server and save it locally. Use when the user asks to export or download a Canva deck, or
  says "xuất slide ra PDF", "tải slide Canva về", "export pptx".
argument-hint: "[design name | Canva link | design id] [pdf|pptx|png] [output path]"
---

# Export a Canva design

Tham số khi gọi bằng `/canva-slides:export`: `$ARGUMENTS`

Kết nối và tên công cụ: như mục 1 của `../slides/references/canva-flow.md` (chưa kết nối thì nói
người dùng chạy `/mcp` → Authenticate rồi dừng).

1. **Tìm design.**
   - Link Canva (`.../design/<ID>/...`) hoặc id → `get-design`. Link rút gọn → `resolve-shortlink` trước.
   - Tên → `search-designs`. Một kết quả khớp → dùng; nhiều kết quả → cho người dùng chọn (tên,
     ngày sửa, số trang); không có → báo và dừng.
   - Không có tham số → design vừa tạo trong phiên này, nếu có; không thì hỏi.
2. **Định dạng.** `get-export-formats` cho design đó. Định dạng mặc định `pdf`. Người dùng xin định
   dạng không được hỗ trợ → nói các định dạng có và hỏi.
3. **Xuất.** `export-design`. Nếu công cụ trả về job đang chạy, chờ/kiểm tra theo mô tả của công cụ,
   không gọi xuất lại liên tục.
4. **Tải về** ngay khi có URL (URL có hạn):

   ```bash
   mkdir -p "<thư mục đích>" && curl -fL --retry 2 -o "<đường dẫn file>" "<url>"
   ```

   Đích mặc định: `slides/<YYYYMMDD>-<slug-tên-design>.<định dạng>` dưới thư mục hiện tại. File đã
   tồn tại → hỏi trước khi ghi đè. Xuất PNG nhiều trang có thể trả nhiều URL hoặc một file zip: lưu
   tất cả vào một thư mục.
5. **Kiểm tra** file: tồn tại, kích thước > 0, đúng loại (`file <đường dẫn>`).
6. **Báo** đường dẫn file, kích thước, số trang nếu biết. Không in lại URL tải. Không gửi file đi
   đâu khác nếu người dùng không yêu cầu.
