# Review cấu hình: <service> / <môi trường>

- **Config**: `<repo cấu hình>` commit `<sha 7>` (<các file cấu hình đã đọc>)
- **Code**: <nhánh `<tên>` @ `<sha 7>` | code hiện tại + thay đổi chưa commit | không đọc (`--no-code`)>; <N> file nguồn; config trong code: <file>
- **So sánh với**: <uat, production | không có>
- **Khởi chạy**: <N lỗi làm app không khởi chạy được | không có lỗi chặn khởi chạy | chưa kiểm vì không đọc code>

## 1. Config còn thiếu

| Key | Chặn khởi chạy | Vì sao thiếu | Hiện trạng | Code đọc ở đâu |
|---|---|---|---|---|
| `<key>` | Có / Có thể / Không | <một câu> | <không có trong env này> | `<file>:<dòng>` |

<!-- Không thiếu key nào: thay bảng bằng dòng "Không thiếu key nào." -->

## 2. Config đang hoặc có thể sai

| Key | Chặn khởi chạy | Nguyên nhân | Hiện trạng | Dùng ở đâu |
|---|---|---|---|---|
| `<key>`, `<key>` | Có / Có thể / Không | <một câu> | <`<secret>` / plain text / giá trị không bí mật> | `<file>:<dòng>` |

<!-- Nhiều key cùng một lỗi: gộp một dòng, cột Key liệt kê các key.
     Không có: thay bảng bằng dòng "Không có config sai hoặc đáng ngờ." -->

## 3. Hướng xử lý

| # | Khi nào | Việc cần làm | Key |
|---|---|---|---|
| 1 | Trước khi deploy / Sớm / Sau | <việc cụ thể, một câu> | `<key>` |

<!-- Không có: thay bảng bằng dòng "Không có việc cần làm." -->

## Ghi chú

- <tối đa 3 dòng: key dùng giá trị mặc định của code, key không ai đọc, vấn đề có hệ thống nói một lần>
- **Đã loại sau khi kiểm**: `<key>` — <vì sao không phải lỗi> <!-- chỉ khi có -->

Tổng: <N> lỗi, <N> cảnh báo, <N> thông tin.

<!-- Quy tắc chung:
     - Viết tiếng Việt, mỗi ô một câu ngắn.
     - Không bao giờ ghi giá trị bí mật: giữ nguyên `<secret>` / "plain text ..." mà script trả về.
     - Chỉ ghi phát hiện đã kiểm chứng. Phát hiện bị loại thì bỏ khỏi bảng và nêu ở "Đã loại sau khi kiểm".
     - Bỏ mục "Ghi chú" nếu không có gì. -->
