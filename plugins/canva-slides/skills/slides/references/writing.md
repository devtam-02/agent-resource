# Writing rules

Cách viết giống tài liệu kỹ thuật: ngắn, cụ thể, có số, không sáo rỗng.

## Mỗi slide

- **Một thông điệp.** Tiêu đề nói kết luận: "Cache hạn mức giảm p95 còn 600 ms", không phải
  "Kết quả thử nghiệm". Người chỉ đọc tiêu đề vẫn hiểu câu chuyện.
- **Tối đa 5 ý**, mỗi ý một dòng (khoảng 90 ký tự). Không viết đoạn văn trên slide; chi tiết đưa
  vào speaker notes (`> `).
- **Số có đơn vị và mốc so sánh**: "2,3 s → 0,6 s (p95)", "18/24 issue Done". Số tiền, số người dùng,
  số giao dịch chỉ dùng khi nguồn có và người dùng đồng ý đưa ra ngoài.
- **Hình hơn chữ** khi nói về luồng, kiến trúc, xu hướng: ghi `Visual:` mô tả cụ thể (loại biểu đồ,
  trục, các khối và mũi tên). Sơ đồ tối đa khoảng 7 khối; nhiều hơn thì tách slide.
- Không dùng biểu tượng cảm xúc, không dùng từ đệm ("rất", "vô cùng", "cực kỳ"), không viết hoa
  cả câu.

## Cả deck

- Thời lượng: khoảng 1 slide cho 1–2 phút nói. 10 phút ≈ 6–10 slide.
- Mạch: bối cảnh → vấn đề (có số) → giải pháp/kết quả → rủi ro → việc cần chốt.
- Slide cuối: quyết định cần chốt hoặc việc tiếp theo, có người phụ trách và mốc thời gian nếu nguồn có.
- Một deck một ngôn ngữ. Tiếng Việt có dấu; thuật ngữ kỹ thuật (API, cache, p95, Sprint, PR),
  tên class, mã issue giữ nguyên.
- Tên người: chỉ dùng khi cần (người phụ trách việc), không đưa email hay số điện thoại.

## Sơ đồ từ tài liệu kỹ thuật

Canva không vẽ Mermaid/PlantUML/C4. Khi nguồn có sơ đồ:

1. rút gọn còn các khối và quan hệ cần cho thông điệp của slide;
2. mô tả trong `Visual:` dạng "Mobile App → API Gateway → Payment Service → Core Banking";
3. nếu người dùng cần đúng sơ đồ gốc: để slide đó có chỗ trống và nhắc họ chèn ảnh trong Canva.
