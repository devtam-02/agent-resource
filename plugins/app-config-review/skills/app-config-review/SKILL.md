---
name: app-config-review
description: >
  Inside a code repository, review the configuration of one environment against the code that will
  run with it: config read from the Git config repositories (via app-config-access), code read from
  the matching branch (staging -> branch staging, uat -> branch release, otherwise the current code or
  a branch the user names). Finds keys the code needs but the config lacks, wrong value types,
  unresolved placeholders, keys nothing reads, plain-text and shared secrets, risky settings, staging
  pointing at production, drift between environments. Read-only. Use when the user, in a service
  repository, asks to review or check the config of an environment, or says "review cấu hình staging",
  "kiểm tra config uat với code", "config production có thiếu gì không", "rà cấu hình trước khi deploy".
argument-hint: "--env staging|uat|production|... [--code current|<branch>] [--service name] [--compare uat,production]"
---

# Review cấu hình theo code

Mục tiêu: trước khi deploy một môi trường, biết chắc **cấu hình của môi trường đó khớp với code sẽ chạy**
(không thiếu key bắt buộc, không sai kiểu, không trỏ nhầm môi trường, không lộ secret).
Chỉ đọc: không sửa code, không checkout, không sửa/commit repo cấu hình.

## Đầu vào

Tham số khi gọi bằng `/app-config-review:app-config-review`: `$ARGUMENTS`

- `--env <môi trường>` bắt buộc. Người dùng nói "staging", "uat", "prod/production"... → dùng đúng tên thư
  mục trong repo cấu hình (kiểm tra bằng `app_config.py envs <service>` khi không chắc).
- Code được đọc theo môi trường:

  | Môi trường | Code |
  |---|---|
  | `staging` | nhánh `staging` |
  | `uat` | nhánh `release` |
  | khác (`production`, ...) | **phải chỉ định**: `--code current` (code đang mở, gồm cả thay đổi chưa commit) hoặc `--code <nhánh>` |

  Người dùng nói "code hiện tại" → `--code current`; nêu tên nhánh → `--code <nhánh>` (ghi đè bảng trên).
  Môi trường không có trong bảng mà người dùng chưa nói → **hỏi** dùng code hiện tại hay nhánh nào, không đoán.
  Bảng đổi được bằng khóa `APP_CONFIG_CODE_BRANCHES` (mặc định `staging:staging,uat:release`) trong `~/.dev-cli/.env`.
- `--service` chỉ khi script không tự nhận ra (nó thử `spring.application.name`, `artifactId`, tên thư mục repo).
- `--compare uat,production` khi người dùng muốn so với môi trường khác.
- Được Claude tự gọi → lấy các thông tin trên từ yêu cầu; thư mục hiện tại phải là repo code của service.

## Quy trình

1. **Chạy script** từ thư mục repo code (script nằm ở `scripts/` cạnh file SKILL.md này; thư viện đọc
   cấu hình dùng chung với skill `app-config-access` ở thư mục bên cạnh):

   ```bash
   python3 "<thư mục skill>/scripts/config_review.py" --env <env> [--code current|<nhánh>] --json
   ```

   Nhánh được `git fetch` riêng rồi đọc qua git object: working tree và nhánh đang checkout không bị đổi.
   Exit 1 = có `error` (không phải script hỏng); 2 tham số sai/thiếu `--code`/nhiều service; 3 chưa cấu
   hình (chạy `app_config.py doctor`, xem skill `app-config-access`); 4 không tìm thấy nhánh/service;
   5 lỗi git/mạng.
2. **Xác nhận** từng `error` và `warn` đáng ngờ bằng cách đọc chỗ dùng trong code (`file:line` có trong kết
   quả, đọc ở đúng nhánh: `git show origin/<nhánh>:<file>`) và giá trị cấu hình (`app_config.py show ...`).
   `references/rules.md` nói mỗi quy tắc đúng/sai khi nào. Không chuyển phát hiện chưa kiểm chứng thành kết luận.
3. **Báo cáo** (tiếng Việt, ngắn), nặng nhất trước:
   - dòng đầu: service, môi trường, commit repo cấu hình, nhánh/commit code đã đọc (hoặc "code hiện tại, có
     thay đổi chưa commit");
   - `error`: key, chỗ code dùng (`file:line`), vì sao lỗi, cách sửa (thêm key nào vào file nào của repo
     cấu hình, sửa kiểu giá trị, chuyển secret ra khỏi repo);
   - `warn`: gộp theo quy tắc;
   - `possibly-unused`: chỉ nêu nếu nhiều hoặc có gợi ý đổi tên; nhắc khai `APP_CONFIG_LIBRARY_PREFIXES` cho
     các prefix do thư viện dùng chung đọc;
   - vấn đề có hệ thống (mọi service đều để mật khẩu plain text) nói **một lần**.
4. **Đề xuất sửa** bằng lời hoặc diff mẫu. Người dùng muốn sửa thật → họ sửa trong repo cấu hình hoặc yêu cầu
   rõ; khi đó làm trên bản clone riêng của họ, không trong thư mục cache.

## Không làm

- **Không in giá trị bí mật** (script chỉ in tên key và `file:line`; khi đọc file thật cũng vậy).
- Không checkout, stash, reset hay đổi nhánh trong repo code; không sửa/commit/push repo cấu hình.
- Không kết luận "cấu hình ổn" khi script không đọc được code (`--no-code`) hoặc bỏ qua môi trường so sánh:
  nói đúng phạm vi đã kiểm.
- Không đưa kết quả lên dịch vụ ngoài khi người dùng không yêu cầu.
