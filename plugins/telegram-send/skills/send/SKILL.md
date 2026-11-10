---
name: send
description: >
  Send to the configured Telegram group through dev-cli: one local file (a report, an export, a
  file just created; checked first for existence, size and credential-looking names), or a short
  text message (checked for empty text, the 4096-character limit and credential-looking content).
  Never prints the bot token. Use when the user asks to send, upload or share a file on Telegram,
  or to send a message/notification to Telegram, or says "gửi file này lên telegram", "gửi file
  vừa tạo", "gửi tin nhắn lên telegram", "báo team trên telegram", "send this to telegram". Not
  for a request that only mentions Telegram without asking to send anything.
argument-hint: "[đường dẫn file | tin nhắn] [--topic <id>]"
effort: low
---

# Gửi file hoặc tin nhắn lên Telegram

Một lệnh, không cần suy luận nhiều. Làm đúng 3 bước dưới, đừng mở rộng.

Có hai việc, chọn theo yêu cầu:

- **Gửi file** → `send <file> --yes` (mục 1 và 2 bên dưới).
- **Gửi tin nhắn** (văn bản ngắn, không có file) → `message --yes --text "..."`, hoặc
  `message --yes --stdin` khi nội dung dài hoặc có nhiều dòng. Nội dung đi qua stdin, không qua
  dòng lệnh. Tối đa 4096 ký tự. Nội dung trông như token, mật khẩu, khóa riêng hay URL có
  `user:password` sẽ bị từ chối; chỉ thêm `--force` khi người dùng biết rõ và vẫn yêu cầu.

## 1. Xác định file hoặc nội dung

Theo thứ tự ưu tiên:

1. Đường dẫn người dùng đưa trong câu lệnh.
2. File vừa tạo ở bước ngay trước, khi người dùng nói "file vừa tạo", "báo cáo đó", "cái file này".
3. Đường dẫn đã nói tới gần đây trong hội thoại.

Nhiều file có thể khớp → **hỏi một câu ngắn**, đừng đoán. Chưa lưu ra đĩa (mới chỉ in ra màn hình)
→ ghi file trước rồi mới gửi.

Với tin nhắn: nội dung do người dùng đưa, hoặc tóm tắt đúng điều họ vừa yêu cầu. Không tự thêm
thông tin, không gửi kết quả của công việc khác.

## 2. Gửi

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/send/scripts/send_telegram.py"
python3 "$S" send "/đường/dẫn/tuyệt/đối" --yes                       # file
python3 "$S" message --yes --text "Deploy staging xong"              # tin nhắn ngắn
printf '%s' "$NOI_DUNG" | python3 "$S" message --yes --stdin        # tin nhắn nhiều dòng
```

- `--yes` bắt buộc: gửi là việc không rút lại được. Chỉ thêm khi **người dùng đã yêu cầu gửi đúng
  file đó**. Người dùng chưa yêu cầu thì dùng `python3 "$S" check <file>` — xem trước, không gửi.
- `--topic <id>` khi nhóm có nhiều chủ đề và người dùng chỉ định.
- `python3 "$S" doctor` khi nghi chưa cấu hình.
- `${CLAUDE_PLUGIN_ROOT}` không có thì tìm:
  `find ~/.claude/plugins -path '*telegram-send*/scripts/send_telegram.py' | head -1`

Script tự kiểm tra trước khi gửi: file có thật, không phải thư mục, không rỗng, dưới 50 MB, và tên
không giống file chứa bí mật.

## 3. Báo lại

Một dòng: tên file, dung lượng, đã gửi hay chưa. Chỉ nói "đã gửi" khi script in `sent.` và thoát 0.

| Mã | Nghĩa | Nói gì với người dùng |
|---|---|---|
| 0 | xong | đã gửi |
| 2 | thiếu `--yes` | hỏi người dùng có muốn gửi không |
| 3 | chưa cấu hình | họ chạy `! dev-cli --send-telegram --config` |
| 4 | bị từ chối | nói lý do script in ra (không tồn tại / quá lớn / giống bí mật / tin nhắn quá dài) |
| 5, 6 | gửi hỏng | nói **chưa gửi được**, kèm lý do |

## Không làm

- **Không tự gửi.** Chỉ gửi khi người dùng yêu cầu. Xong một việc rồi tự gửi kết quả lên Telegram
  là hành vi sai, kể cả khi thấy "có vẻ hữu ích".
- **Không gửi file bí mật.** Script chặn `.env`, `*.pem`, `*.key`, `id_rsa`, `*credentials*`...
  Chỉ dùng `--force` khi người dùng biết rõ nội dung sẽ nằm trong nhóm chat và vẫn yêu cầu.
- **Không in token.** `TELEGRAM_BOT_TOKEN` không bao giờ được xuất hiện trong output hay lệnh chạy.
- Không gửi nhiều file hay nhiều tin nhắn bằng vòng lặp khi người dùng chỉ yêu cầu một lần.
- Không đưa nội dung bí mật vào tin nhắn để "cho tiện", kể cả khi người dùng yêu cầu gửi lại
  đúng một đoạn log: xem trước, và nếu có token/mật khẩu thì hỏi lại trước khi dùng `--force`.
- Không gửi lại sau khi lỗi, trừ khi người dùng bảo thử lại.
- Không sửa, đổi tên hay xóa file gốc.
