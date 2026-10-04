# Báo cáo: <tên> — model `<version>` · dataset `<version>` · version trước `<version>`

Mặc định xuất Markdown (.md). Viết ngắn gọn, rõ ràng, đúng ba phần dưới đây; chỉ xuất .docx khi người dùng yêu cầu.
Ngôn ngữ theo cấu hình run (`eval.json` `lang`, fallback `plan.report_lang`, mặc định vi).

## 1. Tổng quan
- **Hiện trạng bài toán:** version trước đạt kết quả gì, trên tập đánh giá nào; các issue đang giải quyết là gì.
- **Phương pháp giải quyết:** thay đổi cụ thể để xử lý từng issue (dữ liệu, huấn luyện, schema, postprocess hoặc cách đánh giá).
- **Kết quả đạt được:** so sánh trước/sau bằng số liệu đã đo; ghi rõ phần đã cải thiện và vấn đề còn tồn tại. Chưa đo thì ghi "chưa đánh giá".
- **Hợp đồng đánh giá** (khi `eval.json` có `eval_contract`): đơn vị, chiến lược split, metric + hướng tốt, lát cắt/horizon, người chấm (human/model/rule), độ bất định, nguồn gốc tập đánh giá.

## 2. Nội dung chi tiết
### Bảng metric chi tiết
Mỗi hàng là một mục đánh giá (metric/slice; ví dụ OCR/KIE: một trường của một loại tài liệu). Dùng cùng metric và cùng tập đánh giá khi so sánh; nếu khác, ghi rõ và không kết luận tăng/giảm trực tiếp. Hướng tốt của metric ghi bằng ↑ (cao tốt) / ↓ (thấp tốt). Giá trị chưa có bằng chứng ghi N/A, không tự điền số liệu.

| Phạm vi | Mục | Metric | Số mẫu | Version trước | Version hiện tại | Thay đổi |
|---|---|---|---|---|---|---|

### Phân tích lỗi theo nhóm
Chỉ liệt kê nhóm thực sự xuất hiện; ghi rõ mẫu số của tỷ lệ lỗi (thiếu thì ghi N/A).

| Nhóm lỗi | Số lượng / tỷ lệ | Phạm vi bị ảnh hưởng | Nguyên nhân có bằng chứng | Ví dụ tiêu biểu |
|---|---|---|---|---|

## 3. Kết luận
Giải pháp khắc phục theo thứ tự ưu tiên; mỗi dòng: **mức ưu tiên → nhóm lỗi → cần làm gì → cách kiểm chứng**. Ưu tiên lỗi chặn đánh giá và nhóm lỗi ảnh hưởng nhiều trước. Không kết luận chung chung kiểu "cải thiện dữ liệu" / "tối ưu model" mà không nêu hành động cụ thể.

1. P? → nhóm lỗi → hành động cụ thể → cách kiểm chứng

Không kể lại nhật ký thực thi và không đưa log dài vào report; chỉ giữ số liệu, bằng chứng và liên kết artifact cần thiết để người đọc kiểm tra kết luận.

> Sinh từ `eval.json` bằng `scripts/render_report.py` (cũng xuất `report.html` trực quan hóa nhóm lỗi). Schema: `schemas/eval.schema.json`.
