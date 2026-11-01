---
name: optimize-spring-boot
description: >
  Review and optimize Java / Spring Boot code: Sonar self-check (S3776, S1192, S125, S1128),
  money and transaction safety (BigDecimal, idempotency, @Transactional boundaries),
  data access and performance (N+1, paging, batching, thread pools, timeouts), error
  handling, logging/PII, hexagonal structure, and test quality. Use when the user asks to
  optimize, refactor, review, clean up or harden Spring Boot code, or says "tối ưu code",
  "review code java", "rà soát service", "sửa lỗi sonar", "tối ưu hiệu năng".
---

# Optimize Spring Boot code

Mục tiêu: làm code **đúng trước, nhanh sau, dễ đọc sau cùng**, theo cách viết đã chốt trong
`references/style.md`. Thay đổi nhỏ, có test, không đổi hành vi nghiệp vụ nếu người dùng không yêu cầu.

## Phạm vi

1. Xác định phạm vi trước khi đọc code:
   - người dùng chỉ file/thư mục → chỉ phạm vi đó;
   - không chỉ gì → các file đã đổi so với nhánh gốc: `git diff --name-only $(git merge-base HEAD origin/HEAD 2>/dev/null || echo HEAD~1)` cộng `git status --porcelain`;
   - "cả service" → làm theo từng package, ưu tiên `application/` và `adapter/out/`.
2. Đọc `pom.xml` / `build.gradle`: phiên bản Java, Spring Boot, thư viện (Lombok, MapStruct,
   Resilience4j, ShedLock, Liquibase/Flyway, Kafka, Redis, WebClient/Feign). Không đề xuất thứ
   project không có nếu chưa hỏi.

## Quy trình

1. **Quét máy móc** bằng script đi kèm skill này (thư mục `scripts/` cạnh file SKILL.md):

   ```bash
   python3 <thư mục skill>/scripts/java_scan.py <file hoặc thư mục...>
   ```

   Kết quả dạng `path:line: [rule] severity: message`. `error` = vi phạm rule cứng, phải sửa.
   `warn` = cần xem trong ngữ cảnh. Script là heuristic: không thay cho việc đọc code.
2. **Đọc code theo checklist**, theo thứ tự ưu tiên (chỉ mở file tham chiếu khi cần):
   1. Tiền, giao dịch, idempotency, bảo mật → `references/finance-rules.md`
   2. Truy cập dữ liệu, hiệu năng, gọi ra ngoài, Kafka, job → `references/performance.md`
   3. Sonar → `references/sonar.md`
   4. Cấu trúc và cách viết → `references/style.md`
   5. Test → `references/testing.md`
3. **Lập danh sách phát hiện** trước khi sửa: mỗi mục có `file:line`, mức độ
   (`critical` sai tiền/mất dữ liệu/bảo mật · `major` lỗi chắc chắn hoặc hiệu năng rõ rệt ·
   `minor` style/Sonar), vấn đề, cách sửa. Với việc lớn (đổi schema, thêm unique constraint,
   đổi contract API, thêm thư viện) thì **hỏi người dùng trước**, không tự làm.
4. **Sửa** từng nhóm nhỏ. Mỗi thay đổi hành vi phải có test chứng minh (đỏ trước khi sửa nếu
   là bug). Không trộn refactor lớn với sửa bug trong cùng một bước.
5. **Kiểm chứng**:
   - build và test của module đã đụng: `./mvnw -q -pl <module> -am test` hoặc `mvn -q test`
     (Gradle: `./gradlew test`);
   - chạy lại `java_scan.py` trên các file đã sửa: không còn `error`;
   - Sonar self-check bắt buộc trong `references/sonar.md` (đặc biệt lệnh grep S125).
6. **Báo cáo** ngắn: file đã đụng, phát hiện theo mức độ, đã sửa gì, còn lại gì và vì sao
   (cần quyết định của người dùng, ngoài phạm vi, rủi ro cao), kết quả build/test.

## Nguyên tắc

- Không đổi hành vi nghiệp vụ, contract API, schema DB khi chưa hỏi.
- Không nới assert, không `@Disabled` test để cho qua.
- Không thêm dependency mới khi chưa hỏi.
- Không "tối ưu" mù: thay đổi hiệu năng phải chỉ ra được nguyên nhân (N+1, vòng lặp gọi DB,
  gọi tuần tự có thể song song, thiếu index, thiếu timeout...), không đoán.
- Comment giải thích **vì sao**, văn xuôi thuần, không có token code và không có `;` ở cuối
  dòng `//` (S125). Ghi chú kỹ thuật dài đặt trong Javadoc.
