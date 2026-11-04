---
name: app-config-review
description: >
  Inside a code repository, review the configuration of one environment against the code that will
  run with it: config read from the Git config repositories (via app-config-access), code read from
  the matching branch (staging -> branch staging, uat -> branch release, otherwise the current code or
  a branch the user names). First looks for missing keys and for anything that stops the application
  from starting, then wrong values: wrong types, unresolved placeholders, plain-text and shared
  secrets, risky settings, staging pointing at production, drift between environments. Read-only.
  Use when the user, in a service repository, asks to review or check the config of an environment,
  or says "review cấu hình staging", "kiểm tra config uat với code", "config production có thiếu gì
  không", "thiếu config nào không", "app có chạy được không", "rà cấu hình trước khi deploy".
argument-hint: "--env staging|uat|production|... [--code current|<branch>] [--service name] [--compare uat,production]"
---

# Review cấu hình theo code

Mục tiêu, theo đúng thứ tự ưu tiên:

1. **Thiếu config nào không** — code đọc key mà cấu hình môi trường không có.
2. **Cái nào làm app không khởi chạy được** — thiếu key bắt buộc, sai kiểu giá trị, placeholder không
   ai định nghĩa.
3. Phần còn lại: giá trị sai/đáng ngờ, lộ secret, trỏ nhầm môi trường.

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
2. **Xác nhận**, bắt đầu từ những cái `startup: yes` (nói app không khởi chạy được là kết luận nặng
   nhất, phải chắc): đọc chỗ code dùng key đó (`used` trong kết quả, đọc ở đúng nhánh:
   `git show origin/<nhánh>:<file>`) và giá trị cấu hình (`app_config.py show ...`). Kiểm xem key có
   thật sự bắt buộc không — có `@ConfigurationProperties`, giá trị mặc định ở chỗ khác, hay chỉ là
   `${...}` trong chuỗi log/SQL mà script hiểu nhầm. Sai thì bỏ khỏi bảng và nói rõ đã loại.
   Sau đó tới các `error` và `warn` đáng ngờ. `references/rules.md` nói mỗi quy tắc đúng/sai khi nào.
   Không chuyển phát hiện chưa kiểm chứng thành kết luận.
3. **Báo cáo** bằng tiếng Việt, **ngắn gọn**, đúng 3 phần dưới đây, **mỗi phần một bảng**. Lấy sẵn dữ
   liệu từ `sections.missing`, `sections.wrong`, `sections.actions` của `--json` (script đã sắp xếp:
   thứ làm app không khởi chạy được đứng trước).

   Mở đầu **một dòng** gồm: service, môi trường, commit repo cấu hình, nhánh + commit code đã đọc
   (hoặc "code hiện tại, có thay đổi chưa commit"), và kết luận khởi chạy: *"N lỗi làm app không khởi
   chạy được"* / *"không có lỗi chặn khởi chạy"* / *"chưa kiểm vì không đọc code"*.

   **1. Config còn thiếu**

   | Key | Chặn khởi chạy | Vì sao thiếu | Hiện trạng | Code đọc ở đâu |
   |---|---|---|---|---|

   - `Chặn khởi chạy`: `Có` (`startup: yes`), `Có thể` (`maybe`), `Không` (rỗng).
   - Không thiếu key nào thì ghi một dòng "Không thiếu key nào", bỏ bảng.

   **2. Config đang/có thể sai**

   | Key | Chặn khởi chạy | Nguyên nhân | Hiện trạng | Dùng ở đâu |
   |---|---|---|---|---|

   - `Nguyên nhân` dịch ngắn từ `cause`, một câu.
   - `Hiện trạng` lấy từ `state`. **Không bao giờ in giá trị bí mật**: giữ nguyên `<secret>` /
     "plain text..." mà script trả về.
   - Nhiều key cùng một lỗi (ví dụ 5 secret plain text) thì gộp thành một dòng, cột Key liệt kê các key.

   **3. Hướng xử lý**

   | # | Khi nào | Việc cần làm | Key |
   |---|---|---|---|

   - `Khi nào`: `before deploy` → "Trước khi deploy", `high` → "Sớm", `later` → "Sau".

   Sau 3 bảng, nếu cần, thêm tối đa 3 gạch đầu dòng cho phần `Notes` (key dùng giá trị mặc định của
   code, key không ai đọc...). `possibly-unused` nhiều key thì nhắc khai `APP_CONFIG_LIBRARY_PREFIXES`.
   Vấn đề có hệ thống (mọi service đều để mật khẩu plain text) nói **một lần**. Không chép lại toàn bộ
   output của script.
4. **Sửa thật** chỉ khi người dùng yêu cầu rõ: họ sửa trong repo cấu hình, hoặc làm trên bản clone riêng
   của họ, không trong thư mục cache.

## Không làm

- **Không in giá trị bí mật** (script chỉ in tên key và `file:line`; khi đọc file thật cũng vậy).
- Không checkout, stash, reset hay đổi nhánh trong repo code; không sửa/commit/push repo cấu hình.
- Không kết luận "cấu hình ổn" khi script không đọc được code (`--no-code`) hoặc bỏ qua môi trường so sánh:
  nói đúng phạm vi đã kiểm.
- Không đưa kết quả lên dịch vụ ngoài khi người dùng không yêu cầu.
