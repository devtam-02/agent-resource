---
name: review
description: >
  Review the staging configuration of one or more services kept in the Git config repositories
  (one folder per service, one subfolder per environment): plain-text secrets, risky settings,
  staging pointing at production resources, secrets shared with UAT/production, and drift against
  UAT and production. Read-only. Use when the user asks to review, check or audit the config of a
  service for staging, or says "review cấu hình", "kiểm tra config staging", "rà config service",
  "so config staging với uat/production", "config staging có vấn đề gì không".
argument-hint: "<service> [more services] [--compare uat,production] | --list [text]"
---

# Review cấu hình staging

Mục tiêu: trước khi triển khai staging, tìm lỗi cấu hình **rẻ để sửa bây giờ, đắt khi lên UAT/production**.
Chỉ đọc: không sửa, không commit, không đẩy lên repo cấu hình.

## Đầu vào

Tham số khi gọi bằng `/app-config-review:review`: `$ARGUMENTS`

- Tên service (một hoặc nhiều) → review từng service.
- `--list [text]` → liệt kê service có thư mục `staging/` (lọc theo `text`).
- `--compare uat,production` (mặc định) → môi trường để so sánh; `--compare ''` để bỏ so sánh.
- Được Claude tự gọi → lấy tên service từ yêu cầu của người dùng. Tên không khớp chính xác → chạy
  `--list <phần tên>` và hỏi người dùng chọn, không đoán.

## Cách hoạt động

Dữ liệu nằm ở hai repo Git (nhánh `master`). Địa chỉ đọc từ file cấu hình của dev-cli
**`~/.dev-cli/.env`** (biến môi trường cùng tên, nếu có, được ưu tiên), **không** nằm trong plugin:

| Biến | Chứa |
|---|---|
| `APP_CONFIG_STAGING_REPO` | `<service>/staging/` |
| `APP_CONFIG_REPO` | `<service>/uat/` và `<service>/production/` |
| `APP_CONFIG_BRANCH` | nhánh, mặc định `master` |

Script `scripts/config_review.py` (cạnh file SKILL.md) clone nông vào thư mục cache rồi đọc.

## Quy trình

1. **Kiểm tra môi trường** nếu chưa chắc đã cấu hình:

   ```bash
   python3 "<thư mục skill>/scripts/config_review.py" doctor
   ```

   Thiếu khóa hoặc không truy cập được → dừng, nói rõ thiếu khóa nào trong `~/.dev-cli/.env` và chỉ
   người dùng tới mục *Setup* trong README của plugin. Không tự đặt URL, không xin hay ghi mật khẩu/token
   vào đâu. Chỉ sửa `~/.dev-cli/.env` khi người dùng đưa giá trị và yêu cầu rõ.
2. **Chạy script** cho từng service:

   ```bash
   python3 "<thư mục skill>/scripts/config_review.py" review <service> --json
   ```

   Exit 1 nghĩa là có `error`, không phải script hỏng. Exit 3: thiếu cấu hình; 4: không có service/staging;
   5: lỗi git/mạng. Lần đầu clone repo lớn có thể mất vài chục giây.
3. **Đọc kết quả, rồi đọc file thật** cho từng phát hiện `error` và `warn` đáng ngờ (đường dẫn trong
   cache: `~/.cache/app-config-review/`, hoặc `$APP_CONFIG_CACHE`) để xác nhận và hiểu ngữ cảnh. Script là
   heuristic theo dòng; `references/rules.md` nói mỗi quy tắc đúng/sai khi nào. Đừng chuyển nguyên
   phát hiện chưa kiểm chứng thành kết luận.
4. **Báo cáo** (tiếng Việt, ngắn): theo từng service, nặng nhất trước:
   - `error`: file, key, vì sao nguy hiểm, cách sửa gợi ý;
   - `warn`: gộp theo quy tắc nếu lặp nhiều;
   - lệch cấu hình với UAT/production: chỉ nêu những key thật sự đáng ngờ (cờ bật/tắt, kích thước pool, timeout,
     key có ở UAT/production mà thiếu ở staging), bỏ qua khác biệt hiển nhiên giữa các môi trường (host, port, user);
   - môi trường nào không so sánh được (không có thư mục) và vì sao.
   Nhiều service: bảng tóm tắt (service, số error/warn) rồi chi tiết từng cái; cấu hình sai có hệ thống
   (ví dụ mọi service đều để mật khẩu plain text) nói **một lần** ở đầu, không lặp ở từng service.
5. **Đề xuất sửa** bằng lời hoặc diff mẫu trong báo cáo. Người dùng muốn sửa thật → họ sửa trong repo cấu
   hình, hoặc yêu cầu rõ; khi đó làm trên bản clone riêng của họ, không sửa trong thư mục cache.

## Không làm

- **Không in giá trị bí mật.** Script chỉ báo tên key và file; khi đọc file thật cũng không chép giá trị của key
  mật khẩu/token/secret/private-key vào câu trả lời, báo cáo hay commit. Khi cần nói về nó, nêu `file:line` và tên key.
- Không đưa URL có user/mật khẩu, không đưa nội dung config lên dịch vụ ngoài (Canva, Telegram, ...) trừ khi
  người dùng yêu cầu và đã loại giá trị nhạy cảm.
- Không sửa/commit/push repo cấu hình; không `git reset` ngoài thư mục cache của script.
- Không kết luận "staging sạch" khi script báo thiếu so sánh hoặc lỗi: nói đúng phạm vi đã kiểm.
