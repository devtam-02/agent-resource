# Thay đổi `<from>` → `<to>` — <repo>

<!-- Không frontmatter (obsidian_note.py thêm), không bảng số liệu, không đoạn tóm tắt ở đầu.
     Hai nhóm người đọc:
       - BA/Test: mục 1, ngôn ngữ nghiệp vụ, KHÔNG tên class/file/method;
       - Dev: mục 2-9, ngắn, dạng bảng hoặc danh sách; bằng chứng là tên key, endpoint, topic, bảng, file, sha.
     Mục trống ghi "Không có". Không chép commit message, không viết đoạn văn dài. -->

## 1. Nghiệp vụ cần nghiệm thu

| # | Nghiệp vụ / luồng | Thay đổi (trước → sau) | Cần kiểm thử | Mức |
|---|---|---|---|---|
| 1 | <tên luồng người dùng/BA hiểu> | <trước … → nay …, 1–2 câu> | <ca kiểm thử chính, ca biên> | 🔴 / 🟡 / 🟢 |

<!-- 🔴 đổi luật nghiệp vụ, tiền, quyền, dữ liệu bị xóa/sửa; 🟡 đổi hiển thị, thứ tự, mặc định; 🟢 bổ sung không đổi luồng cũ. -->

**Có thể bị ảnh hưởng (kiểm thử hồi quy):**

- <luồng không đổi trực tiếp nhưng dùng chung phần bị sửa>

**Không đổi hành vi:** <refactor, Sonar, test… gộp một dòng>

## 2. Cấu hình

- <2–5 dòng: nhóm key nào thêm/đổi/bỏ và để làm gì; key bắt buộc khai báo trên môi trường>

```properties
# Lấy từ config.snippet của JSON; gom nhóm có ý nghĩa, giữ <secret> như đã che.
# ===== <file> =====
# --- thêm ---
<key>=<giá trị>
# --- đổi (giá trị cũ ở comment) ---
<key>=<giá trị mới>    # cũ: <giá trị cũ>
# --- bỏ ---
# <key>
# ===== bắt buộc khai báo trên môi trường (code đọc, không có default) =====
<KEY>=
```

<File cấu hình khác (Helm, k8s, docker-compose, file riêng môi trường): một dòng mỗi file, hoặc "Không có".>

## 3. Commit

### 3.1 Theo ticket / chủ đề

#### <Ticket / chủ đề>

| Commit | Ngày | Tác giả | Thay đổi | Vùng |
|---|---|---|---|---|
| `<sha 8>` | <ngày> | <tác giả> | <tác dụng, 1 câu> | 🌐 ⚙️ 🗄 📨 📦 🔐 ⏰ 💰 |

<!-- 🗄 migration · ⚙️ config · 📨 Kafka · 📦 build · 🌐 API · 🔐 quyền · ⏰ job · 💰 tiền.
     Nhiều commit cùng một việc (Sonar, sửa test) gộp một dòng. -->

### 3.2 Không tính vào thay đổi

<Một bảng ngắn: đã có ở `<from>` (cherry-pick), cặp revert, bản lặp, commit không còn thay đổi. Hoặc "Không có".>

### 3.3 Chỉ có ở `<from>`

<Commit `<to>` chưa có (hotfix chưa merge ngược), hoặc "Không có".>

## 4. API

### 4.1 Endpoint phục vụ

| Thay đổi | Method · path | Chi tiết | Breaking |
|---|---|---|---|
| thêm / bỏ / đổi | `POST /api/v1/x` | <header/param/body/return/quyền: trước → sau> | ⚠️ <lý do> / — |

### 4.2 Gọi service khác (Feign)

| Thay đổi | Service · method · path | Chi tiết | Ghi chú |
|---|---|---|---|
| đổi | `pp-x` · `POST /v1/y` | <header mới…> | <service đích đã hỗ trợ chưa: chưa kiểm / đã kiểm> |

### 4.3 Request / response / message

| Lớp | Dùng ở | Field thêm | Field bỏ / đổi | Breaking |
|---|---|---|---|---|
| `CreateRequest` | `POST /…` | `serviceCode` (bắt buộc) | −`channel` | ⚠️ |

<!-- Chỉ lớp lộ ra ngoài (exposed) có breaking; còn lại một dòng "N lớp khác đổi field, không breaking". -->

> [!warning] Breaking cho client
> - <client nào phải đổi gì, mỗi dòng một việc>

## 5. Kafka

| Loại | Tên | Thay đổi | Cần làm |
|---|---|---|---|
| topic | `topic_x` (`app.kafka.topics.x`) | mới | tạo trên cụm trước deploy |
| group | `group_y` | mới | đặt theo môi trường; đọc theo auto-offset-reset |
| message | `XEvent` | +field `y` | consumer bản cũ phải bỏ qua field lạ |

## 6. Database

### 6.1 Schema theo bảng (MongoDB: theo collection, gồm index)

| Bảng / collection | Thay đổi | Migration |
|---|---|---|
| `orders` | +cột `c1`; −cột `c2`; index `ix_a` | `024` |

### 6.2 Migration

| File | Context (Liquibase) / id (Mongock) | Tóm tắt | Rủi ro |
|---|---|---|---|
| `024-x.xml` | `dev` / — | <1 dòng> | 🔴/🟡/🟢 <lý do; có rollback không> |

### 6.3 Dữ liệu và seed

| Migration | Bảng | Thao tác | Câu kiểm trước khi chạy |
|---|---|---|---|
| `024` | `orders` | DELETE có WHERE | `SELECT COUNT(*) FROM orders WHERE …;` |

<!-- INSERT/loadData (seed), UPDATE, DELETE, customChange Java. Không có thì "Không có". -->

### 6.4 Entity ↔ migration

<Cột entity mới không có migration, migration bỏ cột còn được map, enum lưu DB bị bỏ/đổi thứ tự… hoặc "Không có".>

## 7. Dependency và build

| Thư viện | Trước | Sau | Ghi chú |
|---|---|---|---|
| `g:a` | 1.0 | 1.1 | SNAPSHOT / đổi nền tảng |

## 8. Ghi chú kỹ thuật cho dev

- <tối đa ~8 dòng: thay đổi logic chính, `Class#method`, commit; lỗi nghi ngờ đọc được trong code>

## 9. Khi deploy và chưa kiểm

- [ ] <tạo topic, thêm key, chạy câu kiểm dữ liệu, deploy service phụ thuộc trước…>

**Chưa kiểm:** <chưa build, chưa chạy test, chưa đọc service đích…>
