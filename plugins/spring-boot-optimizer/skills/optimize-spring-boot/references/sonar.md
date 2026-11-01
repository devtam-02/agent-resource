# Sonar self-check

Chạy **trước khi báo hoàn thành**, chỉ trên file đã sửa hoặc tạo trong task (không quét cả repo).
Có vi phạm thì sửa rồi mới kết thúc. Báo cáo: file đã review, rule vi phạm, đã sửa thế nào, hoặc "không vi phạm".

## java:S3776 — Cognitive Complexity > 15

- Tăng điểm: `if`, `else if`, `else`, `for`, `while`, `do`, `catch`, `?:`, `switch`; mỗi mức lồng thêm penalty; chuỗi `&&`/`||` xen kẽ; `break`/`continue` có label; đệ quy.
- Sửa: extract method, guard clause / early return, gộp điều kiện trùng, map hoặc polymorphism thay `switch` dài, tách vòng lặp lồng.

## java:S1192 — String literal lặp

- Literal ≥ 5 ký tự lặp ≥ 3 lần trong một file (bỏ qua giá trị annotation).
- Sửa: `private static final String` có tên nghĩa, thay mọi chỗ dùng.

## java:S125 — Code bị comment-out

- Không có ngưỡng: 1 dòng cũng vi phạm. Code chết thì **xóa** (đã có git).
- **Bắt buộc chạy**, không thay bằng "đọc lại thấy ổn":

  ```bash
  grep -nE '^\s*//.*;\s*$' <các file .java đã sửa>
  ```

  Có dòng nào → sửa. Đo thực tế trên một repo: `//` kết thúc bằng `;` bị flag 100%, `;` giữa
  dòng và trong Javadoc thì không. Rủi ro thật nằm ở `//` kết thúc bằng `;`.
- `;` là dấu câu hợp lệ trong tiếng Việt và vị trí nó rơi vào cuối dòng là ngẫu nhiên khi
  wrap. **Không dùng `;` ngăn câu trong comment `//`**, dùng dấu chấm hoặc phẩy.
- Sonar chấm theo **cú pháp**, không theo ý nghĩa. Comment giải thích vẫn bị flag nếu có:
  lời gọi `ten(...)`, `{...}`/`(...)` bọc token code, path `{id}`, `PUT /x/{id}`, identifier
  `snake_case`/`camelCase` viết raw (tên cột, field), từ ghép gạch ngang (`sub-link` đọc thành
  phép trừ), toán tử `=`, `+`, `->`, annotation `@...`.
- Khối `//` nhiều dòng: văn xuôi thuần, zero token code, không `;`. Cần nhắc tên kỹ thuật → Javadoc `/** ... */`.

## java:S1128 — Import thừa

- Mọi import không dùng (kể cả `import static` của Mockito/AssertJ/JUnit trong test), import
  trùng, import cùng package, import `java.lang.*`.
- Sửa: **xóa** dòng import. Không bao giờ thay bằng wildcard `*`.
- `java_scan.py` báo cả import thừa, trùng, `java.lang` và wildcard.

## Mở rộng

Thấy thì sửa kèm và nêu trong báo cáo: `S1186` method rỗng, `S106` `System.out`, biến không
dùng, `S2589` điều kiện luôn đúng/sai, `S1452` wildcard generic trả về, `S2142` nuốt
`InterruptedException` (phải `Thread.currentThread().interrupt()`), `S2095` resource không đóng.
Không chắc vượt ngưỡng thì **đếm** trước khi kết luận.
