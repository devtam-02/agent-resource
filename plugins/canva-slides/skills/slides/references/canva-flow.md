# Canva flow

Canva MCP server chính thức: `https://mcp.canva.com/mcp` (HTTP, OAuth). Plugin này khai báo nó
trong `.mcp.json` với tên `canva`. Danh sách công cụ do Canva quyết định và có thể đổi; tên dưới
đây là tên đang thấy, luôn đối chiếu với công cụ thực có trong phiên.

## 1. Tìm công cụ Canva trong phiên

Công cụ Canva có thể đến từ hai nguồn:

| Nguồn | Tên công cụ |
|---|---|
| plugin này | `mcp__plugin_canva-slides_canva__<tool>` |
| connector Canva của claude.ai | `mcp__claude_ai_Canva__<tool>` |

Khi tài khoản claude.ai có connector Canva, Claude Code ẩn server của plugin vì trùng URL; khi đó
chỉ có `mcp__claude_ai_Canva__*`. Dùng nguồn nào đang kết nối được; chỉ dùng **một** nguồn cho cả
deck (hai nguồn có thể đăng nhập hai tài khoản Canva khác nhau).

Chưa kết nối (chỉ thấy công cụ `authenticate`/`complete_authentication`, hoặc gọi bị lỗi xác thực):
dừng phần Canva, nói người dùng gõ `/mcp`, chọn server Canva đang hiện (`claude.ai Canva` hoặc
`plugin:canva-slides:canva`) → Authenticate, đăng nhập Canva trên trình duyệt, rồi chạy lại. Trong
lúc chờ, outline vẫn dùng được.

## 2. Sinh deck

1. (Tuỳ chọn) `list-brand-kits` nếu người dùng muốn dùng brand kit. Lỗi hoặc rỗng (brand kit cần
   gói Enterprise) → bỏ qua, không hỏi lại.
2. Lấy prompt từ outline:

   ```bash
   python3 "<thư mục skill>/scripts/outline_check.py" --prompt slides/<file>.md
   ```

   Lệnh từ chối (exit 1) khi outline còn `error`. Prompt gồm tiêu đề, người nghe, mục tiêu, ngôn
   ngữ, và từng slide (tiêu đề + ý + gợi ý hình). Speaker notes không nằm trong prompt.
3. `generate-design` với loại `presentation` và prompt trên (thêm `brand_kit_id` nếu có). Công cụ trả
   về một job và vài candidate có ảnh xem trước/URL.
   - Nếu phiên có công cụ chuyên cho presentation (ví dụ tên chứa `outline` hoặc `presentation`),
     đọc mô tả của nó và ưu tiên dùng, vì nó giữ đúng cấu trúc slide hơn.
   - Prompt bị từ chối vì dài: rút ý (giữ tiêu đề slide), không cắt mất slide.
4. Cho người dùng xem các candidate (link xem trước) và để họ chọn.
5. `create-design-from-candidate` với job và candidate đã chọn → design id + link chỉnh sửa.

## 3. Đối chiếu và sửa

1. `get-design-pages` (số trang) và `get-design-content` (chữ trên từng trang). So với outline:
   slide thiếu, tiêu đề bị đổi nghĩa, số liệu sai, chữ tiếng Việt mất dấu.
2. Có bộ công cụ chỉnh sửa (`start-editing-transaction`, `perform-editing-operations`,
   `commit-editing-transaction`, `cancel-editing-transaction` hoặc tương tự):
   - mở transaction, sửa chữ cho khớp outline (và speaker notes nếu công cụ hỗ trợ);
   - liệt kê thay đổi cho người dùng, **commit** khi họ đồng ý; lỗi giữa chừng → **cancel**, không để
     transaction treo.
3. Không có công cụ chỉnh sửa: liệt kê chỗ cần sửa theo số trang để người dùng sửa trong Canva.
4. `get-presenter-notes` để kiểm tra speaker notes nếu cần.

## 4. Xuất

`get-export-formats` cho design → `export-design` với định dạng người dùng chọn (`pdf`, `pptx`,
`png`, ...). Công cụ trả về URL tải có hạn: tải ngay về máy (`curl -fL -o <file> "<url>"`), không
dán URL đó vào tài liệu hay git. Chi tiết: skill `export`.

## Giới hạn đã biết

- Sinh design có giới hạn tần suất (khoảng 20 lần/phút): không gọi lặp tự động khi lỗi.
- Resize cần Canva Pro trở lên; brand kit, brand template, autofill cần Enterprise; gói Free xuất
  chất lượng tiêu chuẩn.
- `upload-asset-from-url` và `import-design-from-url` cần URL công khai HTTPS; file trên máy không
  đưa lên trực tiếp được.
- Canva sinh nội dung theo cách của nó: luôn đối chiếu ở bước 3, đừng giả định nó giữ nguyên chữ.
