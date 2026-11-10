---
name: review-pr
description: >
  Review a Spring Boot pull request from a Bitbucket repository's local git range: for each commit,
  what it does; how far the change reaches (impact); which config keys and database migrations it
  adds, changes or removes; and code findings (bugs, performance, conventions). Read-only: it never
  checks out, merges or pushes. Writes the report from a fixed template to a Markdown file and can
  send a short Telegram version through telegram-send, only when asked. Use when the user asks to
  review a PR or a branch range of a Spring Boot service, or says "review PR", "review pull request",
  "rà PR", "review nhánh staging với feature", "PR này có migration không", "PR này đổi config gì",
  "gửi kết quả review lên telegram".
argument-hint: "--base <nhánh đích> --head <nhánh nguồn> [--send-telegram]"
effort: medium
---

# Review PR Spring Boot

Ba câu hỏi bắt buộc, theo đúng thứ tự:

1. **Mỗi commit làm gì** — liệt kê theo commit, không gộp.
2. **Ảnh hưởng đến đâu** — API, nghiệp vụ, dữ liệu, runtime, khả năng rollback.
3. **Có đổi config hoặc database không** — thêm, sửa, bỏ key; migration nào; migration cũ có bị sửa không.

Sau đó mới tới code (bug, performance, convention). Template nằm ở `references/report-template.md`,
checklist ở `references/checklist.md`.

## Bước 1 — Xác định phạm vi

- Cần hai đầu: **đích** (`--base`, ví dụ `staging`, `release`, `master`) và **nguồn** (`--head`,
  ví dụ nhánh feature, hoặc `HEAD`).
- Người dùng đưa link Bitbucket PR: lấy tên nhánh nguồn và đích từ người dùng; nếu họ chưa nói, hỏi
  đúng một câu. Không đoán nhánh.
- Đang đứng trong repo nào? Nếu chưa rõ, kiểm tra `git rev-parse --show-toplevel` và nói tên repo.
- Đảm bảo có ref: `git fetch origin <nhánh>` chỉ cập nhật remote-tracking ref, không đổi working tree.
  Nếu ref vẫn không có, báo lại, không tự tạo.

## Bước 2 — Quét git range

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/review-pr/scripts/pr_scan.py"
REPORT_DIR="$(pwd)/review-reports"   # hoặc thư mục người dùng chỉ định
mkdir -p "$REPORT_DIR"
python3 "$S" --base origin/<đích> --head origin/<nguồn> --patch-file "$REPORT_DIR/.pr-<nguồn>.patch" > "$REPORT_DIR/.pr-<nguồn>.json"
python3 "$S" --base origin/<đích> --head origin/<nguồn> --format text
```

- `${CLAUDE_PLUGIN_ROOT}` rỗng: `find ~/.claude/plugins -path '*spring-pr-review*/scripts/pr_scan.py' | head -1`.
- Script trả JSON: `range`, `commits` (có `flags` và `categories` cho từng commit), `files`,
  `findings` (đã sắp theo mức), `config_changes`, `migrations`, `dependencies`.
- Script không in giá trị secret; nó chỉ báo tên key và số dòng. Muốn xem ngữ cảnh thì đọc file.
- Patch đầy đủ (`-U5`) ghi vào file, đọc có chọn lọc, không nạp cả file vào hội thoại.
- Exit 2: đọc dòng lỗi, báo người dùng; không tự sửa ref.

## Bước 3 — Đọc có chọn lọc

Không đọc toàn bộ diff khi diff lớn. Ưu tiên:

1. **Mọi migration** trong `migrations`: đọc đầy đủ file (SQL ngắn, đọc được hết).
2. **Mọi file config** có `config_changes` hoặc cờ `ddl-auto`, `actuator-exposed`, `config-secret-value`.
3. **Commit có cờ**: `git show <sha> -- <đường dẫn>` cho đúng file có cờ.
4. **Controller và entity** đổi: đọc phần đổi (`git show -U5 <sha> -- <file>`).
5. Phần còn lại: lướt theo tên file, chỉ đọc khi cần để kết luận.

Nếu tổng diff trên ~500 dòng: nói với người dùng là review tập trung vào mục 1–4, phần còn lại lướt.

Không dùng `git checkout`, `git stash`, `git reset`, `git merge`, `git push`. Chỉ đọc.

## Bước 4 — Viết kết quả

Theo đúng `references/report-template.md` (bố cục giống báo cáo PR #13 của dự án promotion):

- **Đầu báo cáo**: bảng metadata (PR, nhánh, phạm vi, tác giả, merge, CI) và một đoạn tóm tắt.
- **1. Tác dụng của từng commit** — nhóm commit theo chủ đề, mỗi commit một dòng: tác dụng (1 câu, nói
  nghiệp vụ hoặc kỹ thuật), mã yêu cầu nếu có. Không chép commit message nguyên văn.
- **2. Đánh giá ảnh hưởng** — chia mức theo tiêu đề: 🔴 rủi ro lớn, 🟡 trung bình (kèm mục hợp đồng API),
  🟢 điểm tốt, ⚪ ghi chú nhỏ. Mục code (bug, performance, convention) đặt ở đây, mỗi mục có file:dòng
  hoặc tên method, lý do, gợi ý sửa. Mỗi mục 🔴 có "Cần làm trước khi deploy".
- **3. Config và database migration** — 3.1 migration (bảng file / nội dung / rủi ro, chi tiết migration
  rủi ro cao, câu SQL kiểm dữ liệu trước khi chạy), 3.2 config (diff key, bảng đối chiếu theo môi trường),
  3.3 việc cần làm ngoài repo (bảng # / việc / nơi). Không có thì ghi rõ là không có, không bỏ mục.
- **Kết luận** — một đoạn đánh giá, kết luận Approve / Request Changes / Needs Discussion, liệt kê việc
  phải chốt trước khi merge/deploy. Nếu chưa build hoặc chưa chạy test thì nói rõ.

Quy tắc chấm:

- Migration cũ bị sửa hoặc xóa (`migration-existing-changed`) là 🔴, luôn luôn.
- `DROP`, `TRUNCATE`, `RENAME`, đổi kiểu cột, `DELETE`/`UPDATE` không có `WHERE` (đã xác nhận) là 🔴
  hoặc ít nhất là 🟡 kèm điều kiện cần kiểm tra. Cờ chỉ là gợi ý; xác nhận bằng đọc câu SQL.
- `ddl-auto=create|create-drop`, `actuator-exposed`, secret viết thẳng đã xác nhận là 🔴.
- Cờ `medium` khác: kiểm tra rồi mới xếp; nếu không có bằng chứng sai, đừng báo như lỗi.
- Mỗi kết luận phải có bằng chứng: file, dòng, hoặc tên key. Không đoán nội dung nằm ngoài diff.
- Giọng xây dựng, nói về code, không nói về người viết.

**Lưu file**: `review-reports/review-<nguồn>-<đích>-<YYYYMMDD>.md` trong repo (hoặc thư mục người dùng
chỉ định). Ghi toàn bộ báo cáo như đã hiện trong chat, kèm header metadata. Báo cho người dùng đường dẫn
đầy đủ. Xóa file `.pr-*.json` và `.patch` tạm nếu người dùng không cần; nếu giữ, nói rõ.

## Bước 5 — Gửi Telegram (chỉ khi người dùng yêu cầu)

Không tự gửi. Chỉ khi người dùng nói "gửi telegram", "gửi kết quả", hoặc truyền `--send-telegram`:

1. Viết bản rút gọn của báo cáo: văn bản thuần (không Markdown, không bảng), dưới ~1500 ký tự. Gồm:
   tiêu đề và kết luận; số commit, file, dòng; mỗi commit một dòng tác dụng; mức ảnh hưởng; config
   có/không kèm key quan trọng; database có/không kèm migration và rủi ro; các mục 🔴; đường dẫn file
   báo cáo đầy đủ. Không đưa giá trị secret.
2. Gửi qua skill `telegram-send:send`, đúng một lần:
   ```bash
   printf '%s' "$TIN" | python3 "<đường dẫn telegram-send>/scripts/send_telegram.py" message --yes --stdin
   ```
   hoặc dùng subagent `telegram-send:telegram-sender` với nội dung đã viết và câu xác nhận người dùng yêu cầu.
3. Báo "đã gửi" chỉ khi lệnh thoát 0 và in `sent.`. Nếu bị từ chối (ví dụ nội dung giống credential),
   không dùng `--force`; sửa tin nếu thật sự chứa thông tin nhạy cảm, hoặc hỏi lại người dùng.
4. Gửi file báo cáo `.md` lên Telegram chỉ khi người dùng nói rõ là gửi file.

## Không làm

- Không checkout, merge, rebase, push, comment lên Bitbucket, approve PR, hay sửa code trong repo.
- Không in giá trị config, secret, token, hay URL có mật khẩu vào chat, file báo cáo, hay Telegram.
- Không kết luận "có migration nguy hiểm" chỉ vì có cờ; đọc câu SQL rồi mới kết luận.
- Không bỏ mục Config hay Database; nếu trống thì ghi "không có".
- Không đưa nhận xét về người viết commit.
