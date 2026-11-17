---
name: write
description: >
  Write into the user's Obsidian vault (OBSIDIAN_VAULT in ~/.dev-cli/.env): create a note, append to
  a note or under one of its headings, append to today's daily note, set or remove properties (tags,
  status, aliases). Never overwrites an existing note unless the user asks (the old version is backed
  up), never writes in .obsidian/ or .git/. Then syncs through Git Vault Sync when the user wants it
  pushed. Use when the user says "ghi vào obsidian", "lưu vào vault", "tạo note", "thêm vào ghi chú X",
  "ghi nhật ký hôm nay", "gắn tag", "đặt status", "save this to obsidian", "add to my note".
argument-hint: "<ghi chú> [nội dung | --section H] [--prop K=V]"
---

# Ghi vào vault Obsidian

Script ghi file trực tiếp (ghi nguyên tử, giữ xuống dòng `\n`), Obsidian đang mở sẽ tự nạp lại.

```bash
V="${CLAUDE_PLUGIN_ROOT}/scripts/vault.py"   # không có biến: find ~/.claude/plugins -path '*obsidian-vault*/scripts/vault.py' | head -1
python3 "$V" write  "<thư mục/tên>" --file "$TMP/note.md" [--prop status=draft --prop tags=x]   # tạo mới
python3 "$V" append "<ghi chú>" --file "$TMP/part.md" [--section "<tiêu đề>"] [--create]
python3 "$V" append "<ghi chú>" --text "- [ ] gọi lại team A" --section "Việc cần làm" --create
python3 "$V" prop   "<ghi chú>" [--set status=done] [--add tags=review] [--list aliases=A,B] [--unset K]
python3 "$V" daily                          # đường dẫn daily note hôm nay, rồi append --create vào đó
```

Nội dung nhiều dòng: ghi ra file tạm trong scratchpad rồi `--file`, hoặc `--stdin` qua heredoc có
nháy (`<<'EOF'`). `--text` chỉ cho một dòng.

## Cách làm

1. **Chọn chỗ ghi.** Người dùng nêu ghi chú thì dùng đúng ghi chú đó (`read` skill để xem trước khi
   chèn vào giữa). Tên trần không có thư mục thì vào `OBSIDIAN_INBOX` (mặc định gốc vault). Chưa rõ
   ghi mới hay thêm vào ghi chú có sẵn thì `search` trước; còn mơ hồ thì hỏi.
2. **Tên file** dùng được trên mọi máy: không `\ : * ? " < > | # ^ [ ]`, không bắt đầu bằng dấu chấm.
   Ngày giờ viết `2026-11-17` hoặc `20261117-1430`, không có `:`. Script từ chối tên sai (exit 2).
3. **Nội dung** là Markdown của Obsidian: tiêu đề `#`, wikilink `[[ghi chú]]`, task `- [ ]`, callout
   `> [!note]`, properties ở đầu file (đặt bằng `--prop`, không tự viết khối `---` lệch định dạng).
4. **Không ghi đè.** Ghi chú đã có → `write` từ chối (exit 4). Thêm vào thì `append`, đổi thuộc tính
   thì `prop`. Chỉ dùng `write --overwrite` khi người dùng nói rõ thay toàn bộ ghi chú; bản cũ được
   sao lưu ở `OBSIDIAN_BACKUP_DIR` (mặc định `~/.cache/obsidian-vault/backups`), báo đường dẫn đó.
5. **Báo lại** đường dẫn ghi chú (dạng `[[...]]`) và việc đã làm, một hai dòng.
6. **Đồng bộ** khi người dùng muốn đẩy lên git ("ghi rồi đồng bộ", "sync luôn"): skill
   `obsidian-vault:sync`, hoặc subagent `obsidian-vault:vault-syncer` chạy nền. Không tự đồng bộ khi
   không được yêu cầu, trừ khi script in dòng `auto-sync: on` (người dùng đặt `OBSIDIAN_AUTO_SYNC=on`
   trong `~/.dev-cli/.env`): khi đó chạy `sync` một lần sau lần ghi cuối của lượt này.

Đổi tên hay chuyển ghi chú: dùng Obsidian CLI (Obsidian phải đang mở; tên vault lấy từ `doctor`)
để các liên kết được cập nhật, không `mv` file trong vault:
`obsidian move path=<cũ.md> to=<thư mục hoặc đường dẫn mới> vault=<tên>`,
`obsidian rename path=<cũ.md> name=<tên mới> vault=<tên>`. Xóa ghi chú chỉ khi người dùng yêu cầu
đích danh: `obsidian delete path=<ghi chú.md> vault=<tên>` (vào thùng rác), không `rm`, không `permanent`.

## Không làm

- Không ghi, sửa, xóa gì trong `.obsidian/`, `.git/`; không chạy `git add/commit/push` trong vault
  (đồng bộ là việc của plugin Git Vault Sync).
- Không chép secret (token, mật khẩu, khóa, URL có `user:pass@`) vào ghi chú, kể cả khi đang lưu log
  hay cấu hình: che giá trị trước.
- Không ghi vào vault kết quả của việc khác khi người dùng không yêu cầu.
