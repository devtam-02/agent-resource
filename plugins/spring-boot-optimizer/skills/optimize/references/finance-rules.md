# Tiền, giao dịch, bảo mật (hệ thống tài chính)

Nguyên tắc: **tiền không được sai — đúng trước, nhanh sau.** Vi phạm ở đây là `critical`.

## 1. Tiền tệ và số học
- Cấm `double`/`float` cho tiền, tỷ giá, lãi suất, phí, phần trăm: dùng `BigDecimal` (DB `DECIMAL`/`NUMERIC`).
- Tạo từ chuỗi: `new BigDecimal("0.10")`, không `new BigDecimal(0.1)`.
- So sánh bằng `compareTo`, cấm `equals` (`0.10` và `0.1` không equals). Test dùng `isEqualByComparingTo`.
- Mọi phép chia/nhân tỷ lệ khai báo scale và `RoundingMode` (`HALF_UP`/`HALF_EVEN` theo nghiệp vụ). Không để mặc định.
- Tiền luôn đi kèm currency; không cộng trừ khác currency khi chưa quy đổi tường minh.
- Không tin số tiền/số dư client gửi lên; tính lại từ dữ liệu server.

## 2. Idempotency
- API ghi giao dịch nhận idempotency key + **unique constraint ở DB**; trùng key trả kết quả lần đầu.
- **Cấm chỉ dựa vào check-then-act** (`existsBy...` rồi `save`): race condition. Dùng unique constraint + bắt `DataIntegrityViolationException`, hoặc insert-first.
- Consumer message phải idempotent (at-least-once): dedupe bằng message id + bảng processed có unique.
- Chỉ retry thao tác idempotent. Timeout với đối tác thanh toán → `PENDING/UNKNOWN` + job đối soát, không đoán kết quả.

## 3. Transaction
- `@Transactional` gọn nhất: **không gọi API ngoài, gửi message, I/O chậm trong transaction DB**.
- Self-invocation và method `private` không đi qua proxy → annotation vô tác dụng.
- Query chỉ đọc: `@Transactional(readOnly = true)`.
- Cập nhật số dư chống race: `@Version`, `SELECT ... FOR UPDATE`, hoặc `UPDATE ... SET balance = balance - :x WHERE balance >= :x`. Cấm read-modify-write không khóa.
- Ledger append-only; sửa sai bằng bút toán đảo.
- Nhiều service: outbox / saga. Cấm "rollback tay" bằng try-catch gọi API.
- Trạng thái giao dịch là state machine tường minh, không set status tự do bằng setter.

## 4. Validation
- Bean Validation ở DTO request (`@Valid`, `@NotNull`, `@Positive`, `@Digits`); rule nghiệp vụ ở service.
- Amount: > 0, đúng scale theo currency, trong hạn mức.
- Cấm bind entity JPA trực tiếp từ request (mass assignment).
- Whitelist: status/type từ client parse về **enum**, không so chuỗi.

## 5. Bảo mật
- Kiểm quyền trên **chính resource** (ownership, chống IDOR), không chỉ role.
- `@PreAuthorize` cho thao tác nhạy cảm; mapping role tập trung một chỗ.
- Cấm secret trong code và file config commit: dùng env/vault placeholder `${...}`.
- Chỉ parameterized query; `ORDER BY` động phải whitelist.
- Dữ liệu thẻ/giấy tờ: mã hóa hoặc tokenize; không lưu CVV.

## 6. Log và audit
- Audit append-only cho thao tác tiền và admin: ai, làm gì, trên gì, khi nào (UTC), từ đâu, trước/sau.
- Cấm log PII/secret dạng thô (số điện thoại, số thẻ, token, OTP, mật khẩu, nội dung tin nhắn): mask `****1234` hoặc bỏ. Soát mọi log dump object.
- Correlation/trace id qua MDC, truyền qua service call và message (kể cả thread pool: `TaskDecorator` copy MDC).
- Log lỗi kèm exception làm tham số cuối (`log.error("...", id, ex)`), không chỉ `ex.getMessage()`.
- Cấm `System.out`.

## 7. Thời gian và dữ liệu
- Lưu UTC, code dùng `Instant`/`OffsetDateTime`; cấm `Date`/`Calendar` cho code mới. Lấy giờ qua `Clock` inject được để test.
- Múi giờ nghiệp vụ là hằng số có tên (`ZoneId`), cut-off tách service riêng có test.
- Soft-delete dữ liệu tài chính. Schema chỉ qua Liquibase/Flyway; script đã merge là bất biến.

## 8. API và lỗi
- Một format lỗi: mã lỗi nghiệp vụ (catalog tập trung) + message + traceId. Không trả stack trace.
- HTTP status đúng nghĩa (400, 401/403, 404, 409, 422, 500). **Cấm 200 kèm `success: false`.**
- Xử lý lỗi tập trung ở `@RestControllerAdvice`. **Cấm nuốt exception** (catch chỉ log rồi đi tiếp, catch rỗng). `catch (Exception)` chỉ ở biên (handler, consumer, job) và phải có hành động: chuyển trạng thái, rethrow, đẩy DLQ.
- Thêm field mới là optional; breaking change → version mới.

## 9. Đối tác
- Mọi outbound call có timeout + circuit breaker (Resilience4j); `RestTemplate`/`WebClient` phải cấu hình timeout.
- Lưu raw request/response đã mask để đối soát.
- Callback: verify chữ ký + chống replay (timestamp + nonce).
- Có job đối soát cho mọi luồng tiền qua đối tác.

## 10. Concurrency và job
- `@Scheduled` chạy nhiều instance phải có distributed lock (ShedLock).
- Batch resumable + idempotent (checkpoint theo record).
- Bean singleton stateless, không shared mutable state.

## 11. Test bắt buộc
- Logic tiền: biên 0, âm, max, scale lẻ, currency không có phần lẻ (VND, JPY).
- Ít nhất một test song song cho luồng số dư (không âm quỹ, không double-spend).
- State machine: transition không hợp lệ bị chặn.
- Integration test với DB thật (Testcontainers) cho lock/constraint, không chỉ H2.
