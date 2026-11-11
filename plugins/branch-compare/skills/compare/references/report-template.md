# So sánh `<nguồn>` → `<đích>` — <tên repo>

| | |
|---|---|
| **Nhánh** | `<source_ref>` (`<sha 8>`, <ngày>) → `<target_ref>` (`<sha 8>`, <ngày>) · merge-base `<sha 8>` (<ngày>) |
| **Commit còn thiếu** | **N** commit (<tác giả>, <ngày đầu> – <ngày cuối>) · bỏ qua M merge commit |
| **Đã có trên đích** | A cùng nội dung · B cherry-pick -x · C cùng tiêu đề khác nội dung ⚠️ · D không còn thay đổi · E cặp revert |
| **Chỉ có trên đích** | K commit, trong đó H cần merge ngược về `<nguồn>` |
| **Thay đổi thực khi merge** | F file · **+A / −R** (`<đích>` → kết quả merge thử) · <có/không> đụng `pom.xml`/Gradle |
| **Merge thử** | ✅ sạch / ❌ xung đột N file / ⚠️ không thử được (<lý do>) |
| **DB · môi trường đích** | <MariaDB / PostgreSQL …> (<tự nhận / chỉ định>) · <uat, rồi production> |

> <Hai đến ba câu: đợt merge này mang lên những gì (theo nghiệp vụ), rủi ro lớn nhất, và có merge
> được không.>

---

## 1. Commit còn thiếu trên `<đích>`

Nhóm theo ticket hoặc chủ đề; trong nhóm giữ thứ tự thời gian. Mỗi commit một câu tác dụng (nghiệp
vụ hoặc kỹ thuật), không chép nguyên commit message.

### Nhóm A — <ticket / chủ đề>

| Commit | Ngày | Tác giả | Tác dụng | Đụng tới |
|---|---|---|---|---|
| `<sha 8>` | <ngày> | <tác giả> | **[<ticket>]** <tác dụng, 1 câu> | 🗄 migration · ⚙️ config · 📨 Kafka · 📦 build · 🌐 API · 🔐 quyền |

### Cần xác nhận trước khi tính là "đã có"

| Commit `<nguồn>` | Commit `<đích>` | Lý do | Kết luận |
|---|---|---|---|
| `<sha>` | `<sha>` | Cùng tiêu đề, khác nội dung | <merge vẫn đổi file / nội dung đã trùng> |

<!-- Ticket lên một phần (partial_tickets): thêm dòng "Ticket <X>: đã có <sha>, còn thiếu <sha>". -->

### Đã có trên `<đích>` (không cần đưa lên)

<Bảng ngắn: commit nguồn → commit đích, cách nhận ra (cùng nội dung / cherry-pick -x). Trên 10 dòng
thì ghi số lượng và vài ví dụ. Commit "không còn thay đổi" và cặp revert ghi chung ở đây.>

### Chỉ có trên `<đích>` (hotfix)

| Commit | Ngày | Tác dụng | Merge ngược về `<nguồn>` |
|---|---|---|---|
| `<sha 8>` | <ngày> | <tác dụng> | Cần (đụng `<file>`) / Không cần (nội dung đã có ở nguồn) |

---

## 2. Ảnh hưởng nếu merge `<nguồn>` → `<đích>`

Theo 8 nhóm của checklist; nhóm không có gì ghi "Không có". Mỗi mục có mức ở đầu dòng và bằng chứng
(file:dòng, tên key, tên bảng, tên topic).

### 2.1 Kafka

| Mức | Thay đổi | Topic / group / lớp message | Cần làm |
|---|---|---|---|
| 🟡 | Topic mới | `<key>` = `<topic>` | Tạo topic trên cụm uat/prod trước deploy |

### 2.2 Database migration (<DB>)

| File | Trạng thái | Nội dung | Mức — lý do |
|---|---|---|---|
| `<file>` | mới / sửa | <thay đổi, một dòng> | 🔴/🟡/🟢 — <lý do; có rollback không> |

<Chi tiết file rủi ro cao: SQL then chốt, khóa bảng/thời gian chạy, câu SQL kiểm dữ liệu trước khi
chạy. Thứ tự include/version; migration chỉ có ở đích.>

```sql
-- kiểm tra trước khi chạy trên <đích>
SELECT ... ;
```

### 2.3 Config và biến môi trường

```properties
+ <key mới>=<giá trị>        # mới
- <key bỏ>
```

**Đối chiếu repo cấu hình** (`env_overrides.py`, commit `<sha>`):

| Key | staging | uat | production | Nhận xét |
|---|---|---|---|---|
| `<key>` | ghi đè: `<giá trị>` / — | … | … | <ví dụ: key bắt buộc, uat chưa có → 🔴> |

<!-- "—" = môi trường không đặt key, giá trị trong code có hiệu lực. Secret hiện `<secret>`.
     Không đối chiếu được: ghi "chưa đối chiếu cấu hình môi trường" và lý do. -->

<File cấu hình riêng môi trường (uat/prod đổi; staging đổi), giá trị giống staging trong file dùng chung.>

### 2.4 Dependency, build, CI/CD và hạ tầng

<Version đổi (cũ → mới), SNAPSHOT, Dockerfile/Helm/pipeline. Không có thì "Không có".>

### 2.5 API contract

<Endpoint/DTO/Feign đổi; breaking hay không; client đã kiểm và chưa kiểm.>

### 2.6 Xung đột merge

| File | Commit `<nguồn>` | Commit `<đích>` | Nhận xét |
|---|---|---|---|
| `<file>` | `<sha>` | `<sha>` | <cùng nội dung, gỡ dễ / khác logic, cần người hiểu nghiệp vụ> |

### 2.7 Nghiệp vụ nhạy cảm — cần xác nhận

| Commit | Vùng | Cần xác nhận |
|---|---|---|
| `<sha 8>` | tiền/giá · phân quyền · job định kỳ · gửi ra ngoài · xóa dữ liệu | <đã nghiệm thu trên staging chưa> |

### 2.8 Rủi ro code khác

<🟡/⚪ từ cờ code đã kiểm (transaction, JPA, catch rộng…). Không build, không chạy test (nếu thật sự
không làm): nói rõ và nêu lệnh cần chạy.>

---

## 3. Việc cần làm

### 3.1 Trước khi merge

1. <gỡ xung đột `<file>` — người nhận>
2. <xác nhận commit cùng tiêu đề khác nội dung>

### 3.2 Trước và trong khi deploy (ngoài repo)

| # | Việc | Nơi |
|---|---|---|
| 1 | <tạo topic `<topic>` trên cụm uat, prod> | Infra |
| 2 | <thêm key `<key>` vào cấu hình uat> | Dev / DevOps |
| 3 | <backup bảng `<bảng>`, chạy câu kiểm trùng> | DBA |

### 3.3 Sau khi merge

1. <merge ngược `<đích>` → `<nguồn>` cho hotfix `<sha>`>

---

## Kết luận

<Một đoạn: đợt merge mang gì lên, rủi ro chính, mức sẵn sàng.>

**<Có thể merge / Merge sau khi xử lý / Chưa nên merge>.** <Các việc phải chốt, đánh số; nói rõ việc
chặn merge/deploy và việc chỉ nên làm.>
