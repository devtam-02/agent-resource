---
name: app-config-access
description: >
  Read a service's configuration from the Git config repositories (one folder per service, one
  subfolder per environment: staging, uat, production, ...): list services, list the environments of
  a service, show a service's config for one environment with secrets masked, locate its files.
  Read-only. Use when the user asks what a service's config is, which value a key has on an
  environment, which services/environments exist, or says "xem cấu hình", "đọc config", "config
  staging của service X", "key này trên uat là gì", "service nào có cấu hình production".
argument-hint: "<service> [--env staging|uat|production|...] | list [--env ENV] [text] | envs <service>"
---

# Đọc cấu hình từ repo cấu hình

Chỉ đọc. Không sửa, không commit, không push repo cấu hình.

## Nguồn

Hai repo Git, cùng nhánh (mặc định `master`), cấu trúc `<service>/<môi trường>/<file>`:

| Môi trường | Repo (khóa trong `~/.dev-cli/.env`) |
|---|---|
| `staging` | `APP_CONFIG_STAGING_REPO` |
| mọi môi trường khác (`uat`, `production`, ...) | `APP_CONFIG_REPO` |

Địa chỉ repo nằm trong file cấu hình của dev-cli `~/.dev-cli/.env`, **không** nằm trong plugin; biến
môi trường cùng tên (nếu có) được ưu tiên. `APP_CONFIG_BRANCH` đổi nhánh, `APP_CONFIG_CACHE` đổi chỗ
lưu bản clone.

## Lệnh

Script `scripts/app_config.py` (cạnh file SKILL.md này):

```bash
A="<thư mục skill>/scripts/app_config.py"
python3 "$A" doctor                                   # cấu hình + quyền truy cập 2 repo
python3 "$A" list [--env uat] [--filter text]          # service (có thư mục môi trường đó)
python3 "$A" envs <service>                            # môi trường của service, nằm ở repo nào
python3 "$A" show <service> --env <env> [--file application.properties] [--keys-only] [--json]
python3 "$A" path <service> --env <env>                # thư mục trong bản clone cache
```

Mọi lệnh tự `fetch` bản clone; thêm `--no-fetch` để dùng bản đã có. Exit: 0 ổn, 2 tham số sai,
3 chưa cấu hình, 4 không có service/môi trường, 5 lỗi git/mạng.

## Quy trình

1. Chưa chắc đã cấu hình → `doctor`. Thiếu khóa hoặc không truy cập được → dừng, nói thiếu khóa nào trong
   `~/.dev-cli/.env` và chỉ tới mục *Setup* trong README của plugin. Không tự đặt URL, không xin hay ghi
   mật khẩu/token. Chỉ sửa `~/.dev-cli/.env` khi người dùng đưa giá trị và yêu cầu rõ.
2. Tên service không chắc chắn → `list --filter <phần tên>` rồi hỏi người dùng chọn; không đoán.
3. Dùng `show` để đọc (giá trị bí mật hiện `<secret>`, user/mật khẩu trong URL bị che). Chỉ mở file thật
   trong thư mục `path` khi cần thứ `show` không có (comment, thứ tự dòng, file không phải properties/yaml).
4. Trả lời đúng câu hỏi: giá trị của key, khác biệt giữa hai môi trường (chạy `show` hai lần và so),
   danh sách service/môi trường. Ghi rõ commit của repo cấu hình đã đọc.

## Không làm

- **Không in giá trị bí mật**, kể cả khi đọc file thật: nêu `file:line` và tên key.
- Không đưa nội dung cấu hình lên dịch vụ ngoài (Canva, Telegram, ...) khi người dùng không yêu cầu, và
  không bao giờ kèm giá trị bí mật.
- Không sửa, commit hay push repo cấu hình; không `git reset` ngoài thư mục cache của script.
