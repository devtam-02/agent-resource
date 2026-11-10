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
- Index trên bảng lớn: PostgreSQL dùng `CREATE INDEX CONCURRENTLY` (không chạy trong transaction).
- Migration chạy với code: migration phải tương thích với bản code cũ đang chạy song song.
- Khóa ngoại mới: thứ tự xóa dữ liệu, dữ liệu mồ côi.

## Entity và mapping JPA

- `@Column` / `@Table` / quan hệ đổi: đối chiếu với migration; cột có trong entity mà không có
  trong DB sẽ lỗi lúc chạy (nếu `ddl-auto=validate`).
- `FetchType.EAGER`, quan hệ `@OneToMany` không `fetch = LAZY`: nguy cơ N+1 hoặc tải thừa.
- `spring.jpa.open-in-view=true`: lazy load ngoài transaction, che giấu N+1.
- `ddl-auto=update|create|create-drop` ngoài môi trường dev: schema bị sửa ngầm, phải dừng.

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

- **Cần sửa (🔴)**: migration cũ bị đổi; mất dữ liệu có thể xảy ra; app không khởi động; secret lộ;
  staging trỏ production; API vỡ client đang chạy mà không có kế hoạch.
- **Nên cải thiện (🟡)**: hiệu năng, transaction chưa rõ, thiếu timeout, thiếu validation.
- **Gợi ý nhỏ (🟢)**: đặt tên, comment, magic number, method quá dài.
