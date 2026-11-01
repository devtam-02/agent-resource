# Test

## Mỗi test phải đỏ được
Nêu được một thay đổi cụ thể trên code làm test đỏ. Không nêu được → test thừa hoặc oracle hỏng.

Cấm:
1. Oracle yếu: chỉ "not null", "exists", `contains` một mẩu ngắn, hoặc so hai giá trị cùng sinh từ code đang test. Kiểm giá trị cụ thể.
2. Mock lỏng: `any()` nơi cần đúng tham số; stub ném lỗi rồi chỉ kiểm "không vỡ". `verify` đúng số lần, đúng tham số (`ArgumentCaptor` + `isEqualTo`).
3. Tên test hứa nhiều hơn thân test.
4. Đồng thời: test xanh khi chạy tuần tự là hỏng. Ép đan xen bằng `CountDownLatch`/`CyclicBarrier`.
5. Thời gian: `sleep`, đồng hồ thật. Dùng `Clock` giả, chờ theo điều kiện (Awaitility).
6. "Không lộ / không ghi": liệt kê đủ trường nhạy cảm, bắt log ở mức thấp nhất, kèm đối chứng dương.
7. Harness che lỗi: helper nuốt lỗi, fallback lặng lẽ.
8. Dữ liệu dùng chung: id cứng, dữ liệu trùng giữa class test. Mỗi test tự tạo và tự dọn.

## Số lượng và tầng
- Một hành vi một test, ở tầng thấp nhất quan sát được. Integration test chỉ cho điều tầng thấp không thấy: nối dây, transaction, lock, constraint (Testcontainers, không H2).
- Ca giống nhau → `@ParameterizedTest`.

## Cách viết ưu tiên
- Unit test thuần, không Spring context: mock bằng `mock(...)` làm field, class cần test tạo bằng `new`.
- Tên test `snake_case` mô tả hành vi (`rejects_amount_with_wrong_scale`), có thể kèm `@DisplayName` tiếng Việt dạng "điều kiện → kết quả".
- AssertJ; `isEqualByComparingTo` cho `BigDecimal`.
- Dữ liệu mẫu dùng chung đặt trong class `*Fixtures`.

## Sửa test có sẵn
- Không nới assert, không `@Disabled` để test qua. Đổi assert thì ghi lý do vào báo cáo.
