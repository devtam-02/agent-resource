---
name: send
description: >
  Send one local file to the configured Telegram group through dev-cli: a report, an export, a
  file just created. Checks the file first (exists, size, not a credential file) and never prints
  the bot token. Use when the user asks to send, upload or share a file on Telegram, or says
  "gửi file này lên telegram", "gửi file vừa tạo", "gửi báo cáo qua telegram", "send this to
  telegram". Not for a request that only mentions Telegram without asking to send anything.
argument-hint: "[đường dẫn file] [--topic <id>]"
effort: low
---

# Gửi file lên Telegram

Một lệnh, không cần suy luận nhiều. Làm đúng 3 bước dưới, đừng mở rộng.

## 1. Xác định file

Theo thứ tự ưu tiên:

1. Đường dẫn người dùng đưa trong câu lệnh.
2. File vừa tạo ở bước ngay trước, khi người dùng nói "file vừa tạo", "báo cáo đó", "cái file này".
3. Đường dẫn đã nói tới gần đây trong hội thoại.

Nhiều file có thể khớp → **hỏi một câu ngắn**, đừng đoán. Chưa lưu ra đĩa (mới chỉ in ra màn hình)
→ ghi file trước rồi mới gửi.

## 2. Gửi

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/send/scripts/send_telegram.py"
python3 "$S" send "/đường/dẫn/tuyệt/đối" --yes
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
| 4 | file bị từ chối | nói lý do script in ra (không tồn tại / quá lớn / giống file bí mật) |
| 5, 6 | gửi hỏng | nói **chưa gửi được**, kèm lý do |

## Không làm

- **Không tự gửi.** Chỉ gửi khi người dùng yêu cầu. Xong một việc rồi tự gửi kết quả lên Telegram
  là hành vi sai, kể cả khi thấy "có vẻ hữu ích".
- **Không gửi file bí mật.** Script chặn `.env`, `*.pem`, `*.key`, `id_rsa`, `*credentials*`...
  Chỉ dùng `--force` khi người dùng biết rõ nội dung sẽ nằm trong nhóm chat và vẫn yêu cầu.
- **Không in token.** `TELEGRAM_BOT_TOKEN` không bao giờ được xuất hiện trong output hay lệnh chạy.
- Không gửi nhiều file bằng vòng lặp khi người dùng chỉ nói một file; không gửi thư mục.
- Không gửi lại sau khi lỗi, trừ khi người dùng bảo thử lại.
- Không sửa, đổi tên hay xóa file gốc.
