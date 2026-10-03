# Spec: OCR phiếu chấm công

## Mục tiêu
Trích xuất tự động các trường (ngày, giờ bắt đầu/kết thúc, giờ nghỉ, work_code, tên công ty, họ tên, mã NV) từ ảnh phiếu chấm công in/viết tay để nhập vào hệ thống nhân sự.

## Nền tảng triển khai
On-premise server của khách, 1 GPU RTX 3060 12GB, Linux, Python. Xử lý ≤ 3 giây/trang.

## Input
Ảnh scan A4 (JPG/PDF) 200–300 dpi, có thể nghiêng nhẹ, mờ nét in kim. Mỗi trang có bảng nhiều dòng.

## Output
JSON theo từng dòng với các field trên; nhãn tin cậy cho mỗi field. Output không bị tác nhân ngoài thay đổi, nhưng chất lượng ảnh scan thay đổi theo máy.

## Dataset
Khách cung cấp khoảng 60 trang real, chưa gán nhãn. Có mô tả mẫu form. Chưa có dữ liệu public tương tự; cần công cụ sinh dữ liệu tổng hợp.

## Chỉ tiêu
Độ chính xác field-level ≥ 95% trên bộ real; tốc độ ≤ 3s/trang; chi phí huấn luyện hợp lý (1 GPU server).
