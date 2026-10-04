# Spec: dự báo nhu cầu hàng ngày theo SKU

## Mục tiêu
Dự báo nhu cầu 14 ngày tới cho từng SKU để đặt hàng tồn kho, giảm thiếu hàng mà không dồn kho.

## Nền tảng triển khai
Batch đêm trên CPU server (4 vCPU, 16GB RAM), Python, container `fcst-v0.1`. Model thống kê + học máy nhẹ, không cần GPU.

## Input
Lịch sử bán theo SKU-ngày 2 năm, lịch khuyến mãi, ngày lễ. Mọi feature chỉ dùng thông tin có trước ngày dự báo (as-of).

## Output
Dự báo điểm + khoảng 90% cho h=1..14 ngày tới, cho 800 SKU. Tác nhân ngoài (khuyến mãi đột xuất) có thể làm lệch output — ghi rõ trong báo cáo.

## Dataset
800 SKU × 730 ngày, đã chốt số bán; 3 đợt khuyến mãi lớn và Tết trong lịch sử.

## Chỉ tiêu
MASE ≤ 0.85 ở h=7 trên rolling-origin 4 folds; độ phủ khoảng 90% đạt 85–93%.

## Đánh giá — eval contract (tùy chọn)
Đơn vị: SKU-ngày. Metric + hướng tốt: MASE ↓, coverage-90% ↑ (mục tiêu khoảng). Horizon: h=1..14, báo cáo h=1/7/14. Người chấm: rule (số bán thực tế). Độ bất định: SE theo fold.

## Chia dữ liệu & chống leakage (tùy chọn)
Chiến lược rolling-origin 4 folds (mỗi fold train 18 tháng, eval 14 ngày tiếp theo). Cấm feature dùng tương lai; leakage audit: kiểm tra date_max(feature) < origin mỗi fold.
