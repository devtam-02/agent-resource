# Outline template

`scripts/outline_check.py` đọc đúng định dạng này. Giữ nguyên các tên khoá và ký hiệu.

```markdown
---
title: Tối ưu luồng thanh toán QR
audience: Dev team + Tech lead
goal: Chốt phương án cache và lịch triển khai
duration_min: 15
language: vi
deck_type: tech-design
---

## 1. Thanh toán QR chậm 2,3 giây ở p95 giờ cao điểm
- p95 2,3 s, mục tiêu 800 ms
- 70% thời gian nằm ở gọi tra cứu hạn mức
Visual: biểu đồ cột p50/p95 theo giờ
> Mở bằng con số; nhắc lại mục tiêu SLA đã cam kết.

## 2. ...
```

Quy ước:

| Phần | Cú pháp | Ghi chú |
|---|---|---|
| Metadata | front matter `---` ở đầu file | `title`, `audience`, `goal`, `duration_min` bắt buộc; `language`, `deck_type` nên có |
| Slide | `## <số>. <tiêu đề>` | Số liên tục từ 1. Tiêu đề là **kết luận**, không phải chủ đề |
| Ý chính | `- ` ở đầu dòng | Tối đa 5 ý; ý con `  - ` tính chung vào giới hạn |
| Gợi ý hình | `Visual: ...` | Một dòng, mô tả hình/sơ đồ/biểu đồ Canva cần dựng |
| Lời nói | `> ...` | Speaker notes; chi tiết để ở đây, không nhồi lên slide |
| Thiếu dữ liệu | `[cần số liệu: ...]` | Script đếm và nhắc; không gửi lên Canva khi còn |

Slide đầu là tiêu đề (tên buổi, người trình bày, ngày). Slide cuối là **việc cần làm/quyết định
cần chốt**, không phải "Cảm ơn" trống.
