# Checklist review Spring Boot

Dùng sau khi chạy `pr_scan.py`. Chỉ kiểm tra những mục mà diff có chạm tới. Mỗi kết luận phải
gắn với file và dòng thật; nếu không chắc, đọc file gốc trước khi kết luận.

## Migration (Flyway / Liquibase)

- Migration đã có trên nhánh đích có bị sửa hoặc xóa không? Đây là lỗi, không phải gợi ý: đổi
  checksum làm hỏng môi trường đã chạy. Sửa bằng migration mới.
- Thêm cột `NOT NULL` mà không `DEFAULT`: lỗi nếu bảng có dữ liệu.
- Đổi tên cột/bảng hoặc đổi kiểu: code đang chạy (bản cũ) sẽ lỗi trong lúc rolling deploy. Cần
  giai đoạn mở rộng (thêm mới, ghi cả hai, đọc mới, bỏ cũ) tách qua nhiều lần deploy.
- `DROP`, `TRUNCATE`: xác nhận không còn code đọc; có bản sao lưu.
- `UPDATE` / `DELETE` không có `WHERE`, hoặc `WHERE` quá rộng: đếm dòng trên bản sao trước.
- Migration chạy với code: migration phải tương thích với bản code cũ đang chạy song song.
- Khóa ngoại mới: thứ tự xóa dữ liệu, dữ liệu mồ côi.
- UNIQUE mới: chạy câu kiểm trùng trước (`SELECT col, COUNT(*) ... GROUP BY col HAVING COUNT(*) > 1`),
  chỉ trên bản ghi còn hiệu lực nếu có xóa mềm.

### Liquibase

- Changeset được nhận diện bằng `id` + `author` + **đường dẫn file**: đổi đường dẫn include, đổi tên
  file hay sửa changeset đã chạy đều làm changeset chạy lại hoặc lỗi checksum.
- Changelog master chỉ được **thêm** include. Thứ tự chạy là thứ tự khai báo, không phải thứ tự số:
  thêm `024` sau `9999` vẫn chạy đúng nhưng dễ nhầm cho người sau.
- File change mới phải được include (trừ khi dùng `includeAll`).
- `dbms:` phải nhất quán: changeset tạo ràng buộc mới có `dbms: mariadb` mà changeset bỏ ràng buộc cũ
  không có → trên DB khác, ràng buộc cũ bị bỏ mà không có cái mới.
- `dropTable` an toàn hơn khi có precondition (bảng tồn tại và rỗng, `onFail: MARK_RAN`).
- Thay đổi không tự đảo được (`sql`, `update`, `delete`, `drop*`, `modifyDataType`) nên có `rollback`.

### Flyway

- Phiên bản mới phải lớn hơn bản lớn nhất trên nhánh đích, và không trùng; nếu không, Flyway từ chối
  khi khởi động (trừ khi bật `outOfOrder`).

### Theo DB

- **MariaDB/MySQL**: `MODIFY`/`CHANGE` và cột sinh `PERSISTENT`/`STORED` dựng lại cả bảng (COPY); tạo
  index thường chạy online nhưng lâu và chờ metadata lock. Bảng lớn trên production cần cửa sổ bảo trì.
  `ADD COLUMN` ở cuối bảng thường nhanh (instant, MariaDB 10.3+).
- **PostgreSQL**: `CREATE INDEX CONCURRENTLY` (không chạy trong transaction; Liquibase cần
  `runInTransaction: false`); `ALTER COLUMN TYPE` khóa bảng và có thể ghi lại toàn bộ bảng.

## Kafka

- Consumer group **mới** (hoặc đổi `group-id`) chưa có offset: hành vi lần chạy đầu theo
  `auto-offset-reset` **của môi trường** (đối chiếu bước 4). `earliest` = đọc lại toàn bộ lịch sử còn
  trong retention, với `concurrency` luồng song song. Chốt offset (đặt `latest` riêng, hoặc seek) trước.
- Consumer phải idempotent; message cũ có dữ liệu không còn hợp lệ sẽ rơi vào DLQ.
- Đổi tên topic: kiểm tra môi trường có ghi đè key không (đổi trong code có tác dụng không), topic mới
  đã tồn tại trên từng cụm chưa, producer/consumer ở service khác đã đổi chưa. Topic chưa tồn tại có thể
  làm producer chặn tới `max.block.ms` mỗi message.
- Tên topic hardcode trong Java không đổi được theo môi trường.

## Entity và mapping JPA

- `@Column` / `@Table` / quan hệ đổi: đối chiếu với migration; cột có trong entity mà không có
  trong DB sẽ lỗi lúc chạy (nếu `ddl-auto=validate`).
- `FetchType.EAGER`, quan hệ `@OneToMany` không `fetch = LAZY`: nguy cơ N+1 hoặc tải thừa.
- `spring.jpa.open-in-view=true`: lazy load ngoài transaction, che giấu N+1.
- `ddl-auto=update|create|create-drop` ngoài môi trường dev: schema bị sửa ngầm, phải dừng.
- Xóa mềm bằng `@SQLRestriction`/`@Where`: chỉ áp dụng cho JPQL/Criteria; mọi native query phải tự
  lọc `deleted_at IS NULL`.

## Config (application*.yml / .properties / helm values / .env)

- Key mới mà code đọc (`@Value`, `@ConfigurationProperties`) không có giá trị ở mọi môi trường
  liên quan: app không khởi động. Kiểm tra từng môi trường (staging, uat, production).
- Key bị bỏ mà code còn đọc: lỗi khởi động hoặc giá trị mặc định ngầm.
- Placeholder `${...}` không có default và không có biến môi trường tương ứng.
- Secret viết thẳng (password, token, key): chuyển sang biến môi trường hoặc secret store. Báo tên
  key, không báo giá trị.
- Actuator: `exposure.include=*` ngoài dev là lộ endpoint nhạy cảm.
- Staging trỏ vào URL/DB của production: dừng, đây là lỗi nghiêm trọng.
- Log level `DEBUG`/`TRACE` cho package nghiệp vụ: lộ dữ liệu và chậm.

## API và tương thích

- Endpoint bị bỏ hoặc đổi path/method: client cũ lỗi. Có cần giữ bản cũ tạm thời không.
- Thêm trường bắt buộc vào request: client cũ lỗi.
- Đổi kiểu hoặc tên trường response: client có thể vỡ.
- DTO: có trả entity trực tiếp ra API không (lộ trường nội bộ, lazy load lỗi).
- Validation: `@Valid` / `@Validated` có còn đó không.

## Transaction và concurrency

- `@Transactional` thêm/bỏ: kiểm tra phạm vi, rollback, `readOnly`, gọi nội bộ (self-invocation)
  không qua proxy.
- `@Async`, `@Scheduled`, listener (Kafka, Rabbit): kiểm tra chạy nhiều instance cùng lúc (cần
  khóa phân tán hoặc idempotent).
- Gọi HTTP ra ngoài không có timeout (`RestTemplate()`, `WebClient.create()`), không retry có kiểm soát.

## Bảo mật

- `permitAll()` hoặc `csrf().disable()` mới: có chủ đích không.
- Log có in token, password, thông tin cá nhân không.
- Kiểm tra quyền ở tầng service, không chỉ controller.
- SQL nối chuỗi (native query ghép tham số): nguy cơ injection.

## Hiệu năng

- Vòng lặp gọi repository (N+1); `findAll()` không phân trang.
- Query không có điều kiện index trên bảng lớn.
- Load toàn bộ kết quả vào bộ nhớ khi chỉ cần đếm hoặc stream.

## Phân loại mức độ

- **Cần sửa (🔴)**: migration cũ bị đổi; include đã có bị đổi đường dẫn; mất dữ liệu có thể xảy ra;
  app không khởi động; secret lộ; staging trỏ production; API vỡ client đang chạy mà không có kế hoạch;
  consumer mới đọc lại toàn bộ lịch sử topic; xung đột merge.
- **Nên cải thiện (🟡)**: hiệu năng, transaction chưa rõ, thiếu timeout, thiếu validation.
- **Gợi ý nhỏ (🟢)**: đặt tên, comment, magic number, method quá dài.
