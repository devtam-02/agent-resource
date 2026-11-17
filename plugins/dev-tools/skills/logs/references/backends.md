# Backend log và tên trường

Mỗi môi trường có thể nằm trên một Kibana/Elasticsearch khác nhau, với **schema log khác nhau**.
Script không biết trước gì cả: mọi thứ đọc từ `~/.dev-cli/.env` (`DEV_CLI_HOME` đổi thư mục).

## Chọn backend theo môi trường

`APP_LOGS_BACKENDS` ánh xạ môi trường → tiền tố khối cấu hình. Mặc định `staging:staging`:

| Môi trường | Khối cấu hình |
|---|---|
| `staging` | `APP_LOGS_STAGING_*` |
| mọi môi trường khác (`uat`, `production`, ...) | `APP_LOGS_*` |

Thiếu khóa nào trong khối riêng thì lấy từ khối `APP_LOGS_*`, rồi mới tới giá trị mặc định. Muốn
tách thêm một backend nữa: `APP_LOGS_BACKENDS=staging:staging,production:prod` → production đọc
`APP_LOGS_PROD_*`.

`APP_LOGS_ENV_ALIASES` (mặc định `prod:production,stg:staging,uat:uat`) đổi tên người dùng gõ
thành tên môi trường thật.

## Khóa của một backend

| Khóa | Mặc định | Ý nghĩa |
|---|---|---|
| `URL` | — | gốc của Kibana (chế độ `kibana`) hoặc của Elasticsearch (chế độ `elasticsearch`) |
| `INDEX` | — | index pattern, ví dụ `app-logs-*`, `staging-*` |
| `USER`, `PASSWORD` | — | HTTP Basic; bỏ trống nếu backend mở |
| `CA_BUNDLE` | — | file PEM của CA nội bộ, khi host dùng chứng chỉ tự ký |
| `MODE` | `kibana` | `kibana` = gọi qua `/api/console/proxy`; `elasticsearch` = gọi thẳng `_search` |
| `INSTANCE_TEMPLATE` | `{env}-{service}` | cách ghép giá trị instance |
| `INSTANCE_MATCH` | `phrase` | `phrase` khi trường instance là `text`, `term` khi là `keyword` |
| `FIELD_INSTANCE` | `kubernetes.labels.app_kubernetes_io/instance` | trường lọc service |
| `FIELD_LEVEL` | `level` | mức log (`log.level` ở schema ECS) |
| `FIELD_POD` | `kubernetes.pod_name` | tên pod |
| `FIELD_MESSAGE` | `message` | nội dung |
| `FIELD_LOGGER` | `class` | tên logger/class (`log.logger` ở schema ECS) |
| `FIELD_IMAGE` | `kubernetes.container_image` | image đang chạy |
| `FIELD_TIME` | `@timestamp` | trường thời gian |
| `NOISE` | `actuator` | các cụm từ bị loại khỏi message, cách nhau bằng dấu phẩy |

Tên đầy đủ là tiền tố + khóa: `APP_LOGS_INDEX`, `APP_LOGS_STAGING_FIELD_LEVEL`... Biến môi trường
cùng tên thắng giá trị trong file, dùng cho một lần chạy.

## Tìm đúng tên trường

```bash
python3 scripts/app_logs.py fields <service> --env <env>
```

In ra một document thật của index cùng toàn bộ tên trường. Đối chiếu với các khóa `FIELD_*`.

Hai cái bẫy:

- **Trường `text` không gom nhóm được.** Elasticsearch không báo lỗi, chỉ trả về **0 nhóm**. Khi
  `summary`/`pods` in cảnh báo `field '...' grouped nothing although there are lines`, phải đổi
  sang trường con `.keyword` (ví dụ `kubernetes.pod_name.keyword`).
- **Trường `keyword` không khớp bằng `match_phrase` nếu giá trị có dấu gạch nối.** Ngược lại,
  `term` trên trường `text` cũng trả về 0 kết quả mà không báo lỗi. Đó là ý nghĩa của
  `INSTANCE_MATCH`: `phrase` cho `text`, `term` cho `keyword`.

Hai trường hợp đã gặp:

| | Backend A | Backend B |
|---|---|---|
| instance | `...app_kubernetes_io/instance` là `text` → `INSTANCE_MATCH=phrase` | `...app_kubernetes_io/instance.keyword` → `INSTANCE_MATCH=term` |
| level | `level` | `log.level` |
| logger | `class` | `log.logger` |

## Chứng chỉ nội bộ

Host HTTPS dùng chứng chỉ do CA nội bộ cấp sẽ lỗi TLS (exit 5). Lấy chuỗi chứng chỉ của CA đó rồi
khai đường dẫn:

```bash
APP_LOGS_STAGING_CA_BUNDLE=~/.dev-cli/ca/internal-ca.pem
```

**Không** dùng `curl -k` hay tắt xác thực chứng chỉ: log đi qua mạng nội bộ vẫn là dữ liệu sản xuất.

## Tổng số dòng

`hits.total` của Elasticsearch dừng ở 10000 trừ khi `track_total_hits` bật — script đã bật, nhưng
khoảng thời gian rộng vẫn làm query nặng. Thu hẹp bằng `--since` trước khi tăng `--scan`.

## Gom nhóm lỗi

`errors` và `summary` thay các phần biến thiên trong message (UUID, số, thời gian, chuỗi trong
nháy kép, chuỗi hex dài) bằng ký hiệu rồi gom theo kết quả đó. Hai hệ quả:

- Cùng một lỗi với id khác nhau → **một nhóm**: cột `Count` là số lần lặp, không phải số lỗi khác nhau.
- Message dựng hoàn toàn từ biến thì gom nhầm. Nhìn cột `Message` (một mẫu thật) trước khi kết luận.

Chỉ `--scan` dòng mới nhất (mặc định 500) được gom; dòng cuối phần in nói rõ khi số dòng thật lớn hơn.
