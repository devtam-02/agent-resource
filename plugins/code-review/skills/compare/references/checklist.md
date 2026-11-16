# Checklist rủi ro khi merge nhánh nguồn → nhánh đích

Đọc ở Bước 4, khi đã có `compare.json`. Cột **Cờ** là `kind` script đã gắn: chỉ là chỗ cần nhìn, đọc
code rồi mới chấm. Mức là mặc định cho nhánh phát hành; hạ hoặc nâng theo ngữ cảnh và ghi lý do.

🔴 chặn merge/deploy đến khi xử lý · 🟡 làm hoặc xác nhận trước khi deploy · 🟢 lành · ⚪ ghi nhận

Mọi đánh giá dựa trên **thay đổi thực khi merge** (`diff_from` → `diff_to`): commit đã cherry-pick
không còn trong đó, hotfix chỉ có ở đích vẫn được giữ.

## 1. Kafka

Tìm: tên topic (config, hằng số, `@KafkaListener(topics=…)`, `NewTopic`/`TopicBuilder`), `group-id`/
`groupId`, `auto-offset-reset`, lớp message/schema (`.avsc`, `.proto`, DTO sự kiện), partition, key.

| Thay đổi | Cờ | Mức | Vì sao / cần làm |
|---|---|---|---|
| Topic mới | `kafka-topic-added`, `kafka-topic-declared` | 🟡 | Cụm uat/prod thường tắt auto-create: tạo topic (partition, retention, ACL) trước khi deploy. |
| Đổi tên topic | `kafka-topic-changed`, `topic-hardcoded` | 🔴 | Message cũ còn ở topic cũ; service khác có thể vẫn dùng tên cũ; giá trị ghi đè trên môi trường phải khớp. |
| Bỏ listener hoặc key topic | `kafka-listener-removed`, `config-key-removed` | 🔴 | Service khác còn gửi/đọc: kiểm tra ai phụ thuộc. |
| Group mới hoặc đổi group | `kafka-group-changed`, `kafka-replay-risk` | 🔴 nếu môi trường đặt `earliest`; 🟡 nếu `latest` | earliest đọc lại toàn bộ lịch sử trong retention; latest bỏ sót message lúc chuyển. Chốt offset trước khi deploy. |
| Đổi cấu trúc message | `message-field-removed`, `message-field-changed`, `message-schema-changed` | 🔴 nếu bỏ/đổi kiểu field; 🟢 nếu chỉ thêm field tùy chọn | Producer và consumer deploy lệch thời điểm sẽ lỗi deserialize. |
| Consumer mới | `kafka-listener` | 🟡 | ACL, topic tồn tại, backlog cũ, idempotent, nhiều instance. |
| Concurrency, partition, key | `kafka-concurrency` | 🟡 | Đổi key/partition đổi thứ tự xử lý theo key. |

## 2. Database migration

Đọc nội dung, không chỉ tên file. DB lấy từ `range.dialect`.

| Thay đổi | Cờ | Mức | Vì sao / cần làm |
|---|---|---|---|
| Migration đã có trên đích bị sửa/xóa, include đổi đường dẫn | `migration-existing-changed`, `lb-include-changed` | 🔴 | Checksum đổi: môi trường đã chạy bản cũ dừng khi khởi động, hoặc phần sửa không có hiệu lực. |
| Flyway trùng version hoặc thấp hơn bản lớn nhất trên đích | `flyway-duplicate-version`, `flyway-out-of-order` | 🔴 / 🟡 | Flyway từ chối nếu không bật `outOfOrder`. |
| Migration chỉ có ở đích | `target_only_migrations` | 🟡 | Phải merge ngược về nguồn; Flyway: nguồn đã có version cao hơn thì thành out-of-order trên staging. |
| DROP, TRUNCATE, RENAME, đổi kiểu, NOT NULL không default | `sql-drop`, `sql-truncate`, `sql-rename`, `sql-type-change`, `sql-add-not-null`, `lb-drop`, `lb-rename`, `lb-type-change`, `lb-add-not-null` | 🔴 (🟡 nếu có precondition hoặc bảng rỗng) | Mất dữ liệu hoặc gãy với dữ liệu uat; code bản cũ còn chạy trong lúc rolling deploy. Backup và kế hoạch rollback. |
| UNIQUE/NOT NULL trên cột có dữ liệu, cột sinh PERSISTENT, index bảng lớn | `sql-unique`, `lb-unique`, `sql-not-null`, `lb-not-null`, `sql-generated-column`, `sql-index-lock`, `lb-index` | 🟡 | Câu SQL kiểm trùng/NULL trước khi chạy; cửa sổ bảo trì. |
| Data fix, backfill | `lb-update`, `lb-delete`, `sql-update-no-where`, `sql-delete-no-where`, `lb-load-data` | 🟡 | Thời gian chạy, idempotent không, điều kiện WHERE. |
| File mới không được include | `lb-not-included` | 🟡 | Không chạy. |
| Code cần cột mới mà migration chưa lên, hoặc ngược lại | `partial_tickets`, `jpa-mapping` | 🔴 | Thường do cherry-pick thiếu: đối chiếu entity với migration của cùng ticket. |

## 3. Config và biến môi trường

Nhánh đích chạy trên uat (rồi production): key phải có trong repo cấu hình của môi trường đó (bảng
`env_overrides.py`, Bước 3). Không in giá trị secret.

| Thay đổi | Cờ | Mức | Vì sao / cần làm |
|---|---|---|---|
| Key bắt buộc mới (không default) | `config-required-key` | 🔴 nếu môi trường đích không có | App không khởi động. Thêm vào repo cấu hình (kể cả secret) trước khi deploy. |
| Key mới có default | `config_changes` "thêm" | 🟢 | Default có hợp với uat/prod không (URL, flag, timeout). |
| Đổi tên key, đổi default | `config_changes` "sửa"/"bỏ" | 🟡 | Môi trường đang ghi đè bằng tên cũ bị bỏ qua âm thầm; key đổi mà mọi môi trường đều ghi đè thì thay đổi không có tác dụng. |
| Bỏ key | `config-key-removed` | 🟡 | Code còn đọc không. |
| URL/host/port, timeout, pool | `config-timeout`, `config-staging-value` | 🟡 | Trỏ nhầm môi trường; giá trị staging/dev/localhost lọt vào file dùng chung. |
| File riêng môi trường đích | `config-target-env-file` | 🟡 | Có hiệu lực ngay trên uat/prod. |
| File riêng staging | `config-source-env-file` | ⚪ | Không chạy trên đích; kiểm tra thay đổi tương ứng cho đích không bị quên. |
| ddl-auto, actuator `*`, secret viết thẳng, profile | `ddl-auto`, `actuator-exposed`, `config-secret-value`, `config-profile` | 🔴 / 🟡 | Xem chi tiết cờ. |
| Feature flag mới | đọc diff | 🟢 | Trạng thái mặc định và giá trị trên uat. |

## 4. Dependency và build

| Thay đổi | Cờ | Mức | Vì sao / cần làm |
|---|---|---|---|
| Đổi Spring Boot, Java, Kotlin | `version-changed` (medium) | 🟡–🔴 | Build và chạy lại toàn bộ test; đọc ghi chú nâng cấp. |
| `-SNAPSHOT` trên nhánh phát hành | `dependency-snapshot` | 🟡 | Build không tái lập; chốt bản release trước production. |
| Đổi version thư viện khác, thêm/bỏ thư viện | `version-changed` (info), `dependency-changed` | 🟢–🟡 | Nâng major, xung đột version. |

## 5. CI/CD và hạ tầng

Dockerfile, Helm/k8s, pipeline (`categories.deploy`, `config-target-env-file`): image base, resource,
replica, ingress, cron job, quyền. 🟡–🔴 vì ảnh hưởng toàn bộ lần deploy, không riêng một tính năng.

## 6. API contract

`api-mapping`, `api-mapping-removed`, DTO request/response, Feign client: xóa/đổi tên endpoint hoặc
field, đổi kiểu, đổi status code, thêm field bắt buộc ở request là breaking (🔴 nếu có client khác).
Thêm endpoint hoặc field tùy chọn: 🟢. Nêu client đã kiểm và chưa kiểm.

## 7. Xung đột và thứ tự commit

- `range.merge.status = conflict`: bảng `conflicts` (file + commit hai phía). Hai phía cùng nội dung
  (cherry-pick cũ) thì dễ gỡ; khác nội dung thì cần người hiểu nghiệp vụ. 🔴 đến khi có người nhận gỡ.
- `target_only` có `back_merge_effect = true`: hotfix chỉ ở đích, phải merge ngược về nguồn (🟡), nếu
  không lần merge sau dễ ghi đè hoặc mất. `false`: nội dung đã có ở nguồn, chỉ ghi nhận.
- `maybe_on_target`: cùng tiêu đề, khác nội dung → xác nhận bản nào đúng.
- `partial_tickets`: ticket lên một phần → 🔴 nếu phần thiếu là migration/config mà phần đã lên cần.
- `reverted`: commit và revert của nó trong cùng đợt, tác động ròng bằng 0, ghi một dòng. Cờ
  `revert-older-change`: revert một thay đổi cũ hơn, sẽ gỡ thứ đang chạy trên đích.

## 8. Nghiệp vụ nhạy cảm (`areas`)

`tiền/giá`, `phân quyền`, `job định kỳ`, `gửi ra ngoài` (email, SMS, OTP, thông báo), `xóa dữ liệu`:
đưa commit vào mục "Cần xác nhận" khi chưa rõ đã nghiệm thu trên staging.
