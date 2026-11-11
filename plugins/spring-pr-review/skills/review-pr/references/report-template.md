# Review PR #<số PR> — <tên repo>

| | |
|---|---|
| **PR** | #<số> `<tiêu đề PR>` — <trạng thái>; hoặc "không có thông tin (chưa đọc Bitbucket)" |
| **Nhánh** | `<head>` (`<head sha 8>`) → `<base>` (`<base sha 8>`) |
| **Phạm vi** | N commit · F file · **+A / −R** |
| **Tác giả commit** | <tên tác giả> (<ngày đầu> – <ngày cuối>) |
| **Merge** | <✅ sạch / ❌ xung đột: `<file>`> (Bitbucket: <canMerge, vetoes> · thử merge local: <clean/conflict>); <có/không đụng `pom.xml`/Gradle> |
| **CI** | <SUCCESSFUL n, FAILED n> / ⚠️ **không có build status nào** — chưa có bằng chứng build/test xanh / không có thông tin |
| **DB** | <MariaDB / PostgreSQL / ...> (<tự nhận từ JDBC URL/driver hoặc chỉ định>) |

> <Một đến hai câu: PR này thực chất làm gì, có phải PR dọn dẹp hay không, có migration / đổi topic / đổi hợp đồng API không.>

---

## 1. Tác dụng của từng commit

Nhóm các commit theo chủ đề (ví dụ: hợp đồng API, sửa lỗi theo yêu cầu, thay đổi lớn). Mỗi nhóm một
bảng; mỗi commit một dòng, giữ thứ tự thời gian trong nhóm.

### Nhóm A — <chủ đề> (<ngày>)

| Commit | Tác dụng |
|---|---|
| `<sha 7>` | **[<mã yêu cầu nếu có>]** <tác dụng 1 câu, nói nghiệp vụ hoặc kỹ thuật, có số liệu nếu có>. |

### Nhóm B — <chủ đề> (<ngày>)

| Commit | Tác dụng |
|---|---|
| `<sha 7>` | … |

---

## 2. Đánh giá ảnh hưởng

Mỗi mục có mức ở tiêu đề. Ghi cụ thể: tên endpoint, tên key, tên bảng, tên topic, file:dòng.

### 🔴 <Rủi ro lớn nhất, một câu tiêu đề>

<Mô tả: cái gì xảy ra, khi nào, ai bị ảnh hưởng, vì sao. Dẫn cấu hình hoặc đoạn code làm bằng chứng
trong khối code nếu cần.>

**Cần làm trước khi deploy:** <việc cụ thể, có người hoặc bộ phận phụ trách nếu biết>.

### 🟡 <Ảnh hưởng trung bình, một câu tiêu đề>

<Mô tả ngắn, bảng nếu có nhiều dòng so sánh (ví dụ key theo môi trường).>

### 🟡 <Hợp đồng API / tích hợp thay đổi>

<Response/request thêm, bỏ, đổi ràng buộc. Phía gọi đã chuẩn bị chưa — nêu rõ đã kiểm tra những client nào
và còn client nào chưa kiểm tra.>

### 🟢 Những thay đổi lành

- <điểm tốt cụ thể, kèm lý do ngắn>
- <test được thêm: số file, lớp chính>

### ⚪ Ghi chú nhỏ

- <việc không chặn merge nhưng nên biết: encoding, đặt tên, repo anh em lệch nhau, …>
- **Không build, không chạy test** (nếu thật sự không làm): nói rõ và nêu lệnh cần chạy trước khi merge.

---

## 3. Config và database migration

<Mở đầu bằng: "Có" / "Không có", kèm mức nặng nhẹ. Nếu không có thay đổi, ghi "Không có thay đổi
config" và "Không có migration" rồi kết thúc mục.>

### 3.1 <N> migration <Liquibase / Flyway> mới (<DB>)

| File | Nội dung | Rủi ro |
|---|---|---|
| `<tên file>` | <thay đổi, một dòng> | Thấp / Trung bình / Cao — <lý do ngắn; có rollback không> |

**Chi tiết <tên file rủi ro cao>.** <SQL then chốt trong khối code>

Điểm cần biết trước khi chạy trên môi trường có dữ liệu:

1. **Khóa bảng / thời gian.** <DDL nào dựng lại bảng, cần cửa sổ bảo trì không>.
2. **Ràng buộc / điều kiện môi trường.** <changeset chỉ áp dụng cho DB nào, rủi ro mất ràng buộc trong im lặng nếu có>.
3. **Dữ liệu hiện có.** <câu SQL kiểm trùng hoặc kiểm dữ liệu trước khi chạy>:
   ```sql
   SELECT ... ;
   ```

<Nếu có: thứ tự khai báo trong changelog master, các bảng bị xóa và điều kiện an toàn để xóa.>

### 3.2 Thay đổi cấu hình (đóng gói)

```properties
+ <key mới>=<giá trị>        # mới
- <key cũ>=<giá trị>
+ <key cũ>=<giá trị mới>
```

**Đối chiếu với cấu hình môi trường** (`env_overrides.py`, repo cấu hình commit `<sha>`):

| Key | staging | uat | production | Nhận xét |
|---|---|---|---|---|
| `<key>` | ghi đè: `<giá trị>` / — | … | … | <ví dụ: đổi trong code không có tác dụng vì mọi env ghi đè; ✅ code có default> |

<!-- "—" = môi trường không đặt key, giá trị trong code có hiệu lực. Secret hiện là `<secret>`.
     Luôn thêm dòng auto-offset-reset khi PR có consumer/group Kafka mới.
     Không đối chiếu được: ghi "chưa đối chiếu cấu hình môi trường" và lý do. -->


<Mâu thuẫn giữa tài liệu nghiệp vụ và cấu hình thật, nếu có: nêu cả hai con số và nguồn của từng con số.>

### 3.3 Việc cần làm ngoài repo

| # | Việc | Nơi |
|---|---|---|
| 1 | <việc cụ thể, ví dụ tạo topic trên cụm Kafka của từng env> | Infra / DBA / Dev |
| 2 | … | … |

---

## Kết luận

<Một đoạn: PR đúng hướng hay không, chất lượng mã, điểm nổi bật, rủi ro chính.>

**<Kết luận: Approve / Request Changes / Needs Discussion>.** <Liệt kê các việc phải chốt trước khi
merge/deploy, đánh số; nêu rõ việc chặn deploy và việc chỉ nên làm.>
