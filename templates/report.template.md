# Báo cáo: <tên> — model `<version>` · dataset `<version>`

## 1. Tổng quan
- **Hiện trạng bài toán:** mục tiêu, baseline, vấn đề chính, bộ đánh giá và giới hạn nhãn.
- **Phương pháp đang tiến hành:** data (nguồn, số lượng, tỉ lệ trộn, chia train/val/test), model và checkpoint khởi tạo, thay đổi so với lần trước, cấu hình pipeline.
- **Kết quả hiện tại:** số đo module và end-to-end chính, so với baseline; kết luận ngắn.

## 2. Nội dung chi tiết
### Bảng độ chính xác chi tiết từng thành phần
| Thành phần | Đúng/Tổng | % | Baseline | Δ |
|---|---|---|---|---|

### Các lỗi sai còn tồn đọng
| Nhóm lỗi | Số lượng | Ví dụ (trang/dòng) | Nguyên nhân đã xác nhận / giả thuyết |
|---|---|---|---|

## 3. Kết luận
- Lỗi sai còn tồn đọng.
- Giải pháp cho từng lỗi, thứ tự ưu tiên, cách đo xác nhận.
- Có đề xuất dùng checkpoint/cấu hình mới hay không; triển khai theo phạm vi user cho phép.

> Dữ liệu sinh tự động từ `eval.json` bằng `scripts/render_report.py` (cũng xuất `report.html`).
