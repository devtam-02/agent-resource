# Quy tắc của config_review.py

Cấu hình của môi trường được review đọc từ repo cấu hình; code đọc từ nhánh tương ứng (hoặc working tree).

Mức: `error` = sửa trước khi triển khai; `warn` = xem xét; `info` = để tham khảo.
Mọi quy tắc là heuristic theo tên key và giá trị. Đọc file thật trước khi kết luận.

## Trên cấu hình của môi trường

| Quy tắc | Mức | Khi nào báo | Sai báo thường gặp |
|---|---|---|---|
| `plain-secret` | error | Key có `password`, `secret`, `token`, `private-key`, `api-key`, `credential`, `salt`... mà giá trị là chuỗi thật, không phải `${...}`, `ENC(...)`, `{cipher}`, `vault:`, `file:`/`classpath:` | Key chứa chữ `token`/`key` nhưng là cấu hình thường (loại một phần: `*-ttl`, `public-key`, `*-url`...). Số, `true/false` được bỏ qua |
| `credential-in-url` | error | `scheme://user:pass@host` trong giá trị | Hiếm; DSN có user:pass vẫn nên tách khóa |
| `secret-shared` | error | Cùng một giá trị secret ở staging và ở UAT/production | Giá trị mẫu dùng chung kiểu `changeme` (đã bỏ qua) |
| `points-at-production` | error | (với `--compare production`) Địa chỉ nội bộ (IP riêng, host không có domain công khai) giống hệt giá trị production: môi trường thử gọi nhầm vào production | Dịch vụ nội bộ dùng chung có chủ đích (một registry chung) |
| `same-endpoint` | warn | Endpoint giống UAT, hoặc endpoint công khai giống production | Dịch vụ bên thứ ba có một URL duy nhất: thường chấp nhận được |
| `other-env-marker` | error/warn | Giá trị endpoint nhắc tên môi trường khác: `prod` trong staging/uat, hoặc `staging`/`uat`/`dev` trong production là error; còn lại warn (staging cạnh hệ thống `dev` thì bỏ qua) | Tên host chứa chữ đó vì lý do khác |
| `ddl-auto` | error | `hibernate.ddl-auto` là `update`/`create`/`create-drop` | — |
| `actuator-exposure`, `actuator-env` | error | Mở mọi endpoint actuator, hoặc `env`/`heapdump`/`threaddump`/`configprops` | Staging cố ý mở để debug: ghi nhận nhưng nói rõ |
| `auth-disabled` | error | key `*.auth.enabled`, `*.security.enabled`... bằng `false` | Công tắc của tính năng khác (ví dụ auth của Kafka nội bộ): đọc ngữ cảnh |
| `ssl-verify-off` | error | Key có `ssl/tls/cert/hostname` + `verify/validate/check` = `false`, hoặc `trust-all`/`insecure`/`allow-insecure` = `true` | — |
| `h2-console` | error | `spring.h2.console.enabled=true` | Dịch vụ dùng H2 thật sự cho test |
| `show-sql`, `debug-flag`, `root-log-level` | warn | In SQL, `debug=true`, root log DEBUG/TRACE | Có thể có PII/dữ liệu tiền trong log |
| `swagger` | warn | Tài liệu API bật | Bình thường ở staging; so với UAT/production |
| `cors-any` | warn | `allowed-origins=*` | — |
| `localhost` | warn | Endpoint trỏ `localhost`/`127.0.0.1` | Sidecar thật sự chạy cùng pod |
| `timeout-zero` | warn | Key timeout bằng 0 hoặc âm | Một số thư viện dùng 0 = mặc định, không phải vô hạn |
| `pool-huge` | warn | Giới hạn pool/kết nối trên 500 | — |
| `no-timeout` | warn | File `application*`/`bootstrap*` có cấu hình HTTP client mà không có key timeout nào | — |
| `duplicate-key` | warn | Một key khai báo hai lần trong một file (cái sau thắng) | — |
| `parse` | warn | Dòng không đọc được | Cú pháp YAML phức tạp (anchor, flow style): đọc tay |

## So với code

Code: file `.java`/`.kt`/`.groovy` ngoài `src/test`; file `application*`/`bootstrap*` trong `resources`
(ngoài test) là cấu hình đi kèm code. File profile `application-<p>` chỉ tính khi `<p>` là môi trường đang
review hoặc nằm trong `spring.profiles.active/include` của cấu hình. Khóa so khớp theo relaxed binding
(không phân biệt hoa thường, `-`, `_`).

Code "cần" một key khi: `${key}` trong chuỗi bất kỳ (`@Value`, `@Scheduled`, `@KafkaListener`...),
`getProperty`/`getRequiredProperty("key")`, `@ConditionalOnProperty`. Key thuộc prefix của một
`@ConfigurationProperties` được coi là đã dùng.

| Quy tắc | Mức | Khi nào báo | Sai báo thường gặp |
|---|---|---|---|
| `missing-required` | error | Code cần key không có default (`${key}`, `getRequiredProperty`) mà cấu hình môi trường và file cấu hình trong code đều không có: ứng dụng lỗi lúc khởi động | Key được cấp bằng biến môi trường/ConfigMap ngoài repo cấu hình; key đặt trong thư viện dùng chung |
| `env-var-required` | warn | Code hoặc giá trị cấu hình cần `${UPPER_CASE}`: biến môi trường, kiểm tra bên deploy | — |
| `type-mismatch` | error | `@Value` gắn vào `int/long/boolean/double/BigDecimal/Duration` nhưng giá trị không đúng kiểu | Converter tự viết |
| `unresolved-placeholder` | error | Giá trị cấu hình chứa `${key}` không default mà không nơi nào định nghĩa | Key đến từ nguồn khác (Config Server, biến môi trường dạng thường) |
| `secret-in-code-repo` | error | Secret plain text trong `application*`/`bootstrap*` của **repo code** | Giá trị mẫu cho môi trường local |
| `possibly-renamed` | warn | Cấu hình có key gần giống (≥ 85%) một key code đọc mà cấu hình chưa có | — |
| `possibly-unused` | info/warn | Key cấu hình không được code, Spring, giá trị khác hay `APP_CONFIG_LIBRARY_PREFIXES` đọc. Không kiểm khi code có dưới 5 file nguồn | Thư viện dùng chung đọc key (khai prefix của nó), key dùng qua reflection |
| `code-default-used` | info | Code có default và môi trường không đặt key: chạy bằng default | — |
| `code-repo-value-used` | info | Key chỉ có trong file cấu hình đi kèm code | — |
| `profile-file-also-sets` | info | File profile trong code cũng đặt key với giá trị khác: bên nào thắng tùy cách nạp cấu hình | — |

## So với môi trường khác (`--compare`)

| Quy tắc | Mức | Ý nghĩa |
|---|---|---|
| `key-missing` | warn | Key có ở môi trường kia mà thiếu ở môi trường đang review |
| `key-extra` | info | Key chỉ có ở môi trường đang review (gộp một dòng mỗi file) |
| `file-missing` / `file-only-here` | warn / info | Thiếu hoặc thừa cả file (ví dụ `bootstrap.properties`) |
| `value-differs` | info | Khác giá trị ở key không phải host/port/user/secret. Xem các cờ bật/tắt, timeout, kích thước pool |

Không so giá trị của key bí mật và key riêng từng môi trường (url, host, port, user, topic, schema...)
ở `value-differs`; bí mật chỉ được so để phát hiện `secret-shared`.

## Phạm vi file

`.properties`, `.yml`, `.yaml`, `.env` trong thư mục môi trường (đệ quy). Bỏ qua file lớn hơn 2 MB,
file không phải UTF-8, và mọi thứ khác (Dockerfile, `.conf`, `.ts`, `.xml`).

## Thêm quy tắc

Sửa `RISKY` (quy tắc theo key/giá trị), `review_config` (cấu hình) hoặc `review_against_code` (so với code) trong `scripts/config_review.py`, thêm một dòng ở bảng trên.
