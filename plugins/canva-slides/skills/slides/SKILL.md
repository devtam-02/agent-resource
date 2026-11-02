---
name: slides
description: >
  Build a presentation deck: brief → slide-by-slide outline with speaker notes (a Markdown file,
  checked by a script) → Canva presentation through the Canva MCP server → optional PDF/PPTX
  export. Sources can be a topic, Markdown specs/ADRs, a dev-cli jira-tool JSON export or a git
  range. Use when the user asks for slides, a deck or a presentation, or says "tạo slide",
  "làm slide thuyết trình", "slide sprint review", "slide demo", "slide trình bày thiết kế",
  "làm bài thuyết trình trên Canva".
argument-hint: "[topic | source files] [--type sprint-review|tech-design|tool-intro|incident|knowledge|status] [--minutes N] [--lang vi|en] [--outline-only]"
---

# Slides: outline trước, Canva sau

Mục tiêu: một bộ slide **mỗi slide một thông điệp**, đúng thời lượng, nội dung lấy từ nguồn thật
(không bịa số liệu), dữ liệu nhạy cảm không rời máy. Outline là bản gốc nằm trên máy; Canva chỉ là
nơi dàn trang.

## Đầu vào

Tham số khi gọi bằng `/canva-slides:slides`: `$ARGUMENTS`

- Chủ đề tự do, hoặc đường dẫn nguồn: file `.md` (spec, ADR, knowledge-base), JSON của
  `dev-cli --jira-tool scan-issues`, một git range (`v1.2.0..HEAD`).
- `--type`: loại deck, xem `references/deck-types.md`. Không có → tự suy từ nguồn và mục đích.
- `--minutes N`: thời lượng nói. Không có → 10 phút.
- `--lang vi|en`: ngôn ngữ slide. Không có → ngôn ngữ của yêu cầu (tiếng Việt nếu người dùng viết tiếng Việt). Thuật ngữ kỹ thuật, tên class/API, mã Jira giữ nguyên tiếng Anh.
- `--outline-only`: chỉ làm outline, không gọi Canva (dùng khi nội dung nội bộ, hoặc chưa kết nối Canva).
- Được Claude tự gọi → lấy các thông tin trên từ yêu cầu của người dùng.

## Quy trình

1. **Brief.** Chốt 5 điều: người nghe, mục tiêu (sau buổi này họ quyết định/làm gì), thời lượng,
   ngôn ngữ, loại deck. Suy từ yêu cầu và nguồn trước; chỉ hỏi những điều còn thiếu *và* làm đổi nội
   dung, gộp vào **một** lần hỏi. Không hỏi lại điều người dùng đã nói.
2. **Đọc nguồn**, chỉ phần cần cho mục tiêu. Mọi con số trên slide phải truy được về nguồn; thiếu số
   thì ghi `[cần số liệu: ...]` thay vì đoán.
   - JSON jira-tool: dùng `summary` (`by_status_category`, `by_status`, `by_assignee`, `time`),
     `flags` (`overdue`, `due_soon`, `unassigned`, `stale`, `over_estimate`) và `issues`
     (`key`, `summary`, `status`, `parent`, `worklog`). Không đưa `jira_base_url` hay URL issue lên slide.
   - Git range: `git log --no-merges --format='%h %s' <range>`, gom theo scope/tính năng, không liệt kê từng commit.
3. **Viết outline** theo `references/outline-template.md` và quy tắc trong `references/writing.md`.
   Lưu vào đường dẫn người dùng chỉ; không có thì `slides/<YYYYMMDD>-<slug>.md` dưới thư mục hiện
   tại. Không commit file này nếu người dùng không yêu cầu.
4. **Kiểm tra** bằng script đi kèm skill này (thư mục `scripts/` cạnh file SKILL.md):

   ```bash
   python3 "<thư mục skill>/scripts/outline_check.py" slides/<file>.md
   ```

   Sửa tới khi không còn `error`; `warn` thì sửa hoặc nói rõ lý do giữ. Lỗi `sensitive` (secret,
   email, số điện thoại, số thẻ, IP/host nội bộ, ...) **bắt buộc** xử lý trước bước 6. Script chỉ in
   loại và số dòng, không in giá trị; khi báo lại cho người dùng cũng không chép giá trị.
5. **Cho người dùng xem** danh sách tiêu đề slide (kèm số slide, thời lượng) và đường dẫn file outline.
   Với `--outline-only` dừng ở đây.
6. **Xin xác nhận trước khi gửi nội dung lên Canva.** Canva là dịch vụ bên ngoài: nội dung gửi đi
   rời khỏi máy. Nói rõ sẽ gửi gì (tiêu đề + nội dung các slide). Bỏ qua bước hỏi chỉ khi người
   dùng đã nói rõ là làm luôn trên Canva *và* bước 4 sạch `sensitive`. Nếu nguồn là tài liệu nội bộ
   (thiết kế chưa công bố, số liệu kinh doanh, dữ liệu khách hàng), hỏi lại dù đã được bảo làm luôn.
7. **Tạo trên Canva** theo `references/canva-flow.md`: kiểm tra kết nối → lấy prompt bằng
   `outline_check.py --prompt` → `generate-design` (presentation) → cho người dùng chọn một candidate →
   `create-design-from-candidate` → đối chiếu nội dung với outline → sửa nếu có công cụ chỉnh sửa.
8. **Xuất file** nếu người dùng muốn (PDF mặc định, PPTX nếu cần sửa tiếp bằng PowerPoint): làm như
   skill `export` của plugin này.
9. **Báo cáo** ngắn: link Canva, số slide, những chỗ Canva làm khác outline (và đã sửa hay chưa),
   đường dẫn file outline và file đã xuất, các `[cần số liệu: ...]` còn mở.

## Không làm

- Không bịa số liệu, tên khách hàng, kết quả demo, mốc thời gian.
- Không gửi secret, token, mật khẩu, dữ liệu khách hàng, số tài khoản/thẻ, URL hay host nội bộ lên Canva.
- Không gọi `generate-design` quá 2 lần cho một deck mà không hỏi người dùng (mỗi lần là một lượt
  sinh, có giới hạn tần suất).
- Không tự chọn candidate thay người dùng, trừ khi người dùng đã nói "chọn giúp".
- Không đưa ảnh/sơ đồ nội bộ lên một host công khai chỉ để lấy URL cho `upload-asset-from-url`.
- Không tự gửi file đã xuất đi đâu (Telegram, email, git) nếu người dùng không yêu cầu.
