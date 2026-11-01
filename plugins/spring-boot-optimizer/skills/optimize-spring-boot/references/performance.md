# Truy cập dữ liệu và hiệu năng

Chỉ sửa khi chỉ ra được nguyên nhân. Ghi lại "trước/sau" (số query, số lời gọi, độ phức tạp) trong báo cáo.

## JPA / SQL
- **N+1**: quan hệ `@OneToMany`/`@ManyToOne` duyệt trong vòng lặp → `JOIN FETCH`, `@EntityGraph`, hoặc `@BatchSize`/`hibernate.default_batch_fetch_size`. Không để `FetchType.EAGER` trên collection.
- **Không tải cả bảng**: `findAll()` không `Pageable`, `findAll().stream().filter(...)` → query có điều kiện, `Page`/`Slice`, hoặc stream có giới hạn.
- **Projection** khi chỉ cần vài cột: interface/record projection hoặc JPQL `select new ...`; chỉ lấy id khi chỉ cần id.
- **Ghi hàng loạt**: `save()` trong vòng lặp → `saveAll` + `hibernate.jdbc.batch_size` (+ `order_inserts/order_updates`), hoặc update/insert dạng bulk (`@Modifying` JPQL/native). Nhớ bulk update bỏ qua persistence context.
- **Upsert** native (`ON CONFLICT` / `MERGE`) thay vì đọc rồi ghi khi có race.
- **Index**: mỗi điều kiện lọc/sắp xếp thường xuyên có index; thêm bằng Liquibase/Flyway changeset mới.
- `@Transactional(readOnly = true)` cho truy vấn (bỏ dirty checking, có thể route sang replica).
- `count` chỉ khi cần tổng; dùng `Slice` khi chỉ cần "còn trang sau".
- Không mở transaction dài bao quanh lời gọi mạng.

## Gọi service khác
- Gọi tuần tự các nguồn độc lập → song song bằng `CompletableFuture` trên **executor có tên**, cấu hình core/max/queue, `RejectedExecutionHandler` rõ ràng (`CallerRunsPolicy`), `TaskDecorator` copy MDC/security context. Không dùng `ForkJoinPool.commonPool()` cho I/O.
- Chỉ submit task từ thread request, không lồng future chờ nhau trên cùng pool (deadlock).
- Mọi lời gọi: timeout (connect + read), circuit breaker, fallback có chủ đích (port "best-effort" trả rỗng phải được ghi rõ là best-effort).
- `WebClient.block()` phải có `Duration`; trong code servlet thì cân nhắc gom nhiều lời gọi rồi block một lần.
- Phân trang API ngoài: page size + số trang tối đa (chặn vòng lặp vô hạn), log khi chạm trần.

## Kafka
- Batch listener: xử lý từng record, record lỗi → retry có giới hạn rồi **DLQ** (`DefaultErrorHandler` + `DeadLetterPublishingRecoverer`), không nuốt rồi ack cả batch.
- Consumer idempotent (xem finance-rules §2).
- Không làm I/O chậm trong transaction DB của consumer.

## Cache
- Cache có TTL và giới hạn kích thước; key có version/namespace; invalidation khi ghi.
- Không cache dữ liệu theo người dùng trong cache dùng chung nếu không có user trong key.

## Job / batch
- `@Scheduled` + ShedLock; job chia trang theo khóa (keyset), không `OFFSET` lớn.
- Checkpoint theo record để chạy lại an toàn.

## Bộ nhớ và CPU
- Không nạp danh sách lớn vào bộ nhớ để lọc/sắp xếp nếu DB làm được.
- `String` nối trong vòng lặp → `StringBuilder`; regex dùng nhiều lần → `static final Pattern`.
- Đối tượng nặng (ObjectMapper, client, formatter) là bean/hằng số, không tạo mỗi request. `DateTimeFormatter` thread-safe, `SimpleDateFormat` thì không.
