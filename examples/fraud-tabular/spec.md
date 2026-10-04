# Spec: phát hiện gian lận thẻ (tabular)

## Mục tiêu
Chấm điểm rủi ro gian lận cho mỗi giao dịch thẻ để chặn giữ tiền theo ngân sách false-positive cho phép.

## Nền tảng triển khai
Batch offline trên CPU server (8 vCPU, 32GB RAM), Python, container `fraud-xgb-v0.1`. Train gradient boosting, không cần GPU.

## Input
Dòng giao dịch: số tiền, kênh, mã merchant, giờ, lịch sử 30 ngày của thẻ (as-of thời điểm giao dịch).

## Output
Điểm rủi ro 0–1 + ngưỡng chặn. Output không bị tác nhân ngoài thay đổi sau khi chốt ngưỡng.

## Dataset
2,1M giao dịch 6 tháng, 0,31% gian lận đã gán nhãn (đối soát chargeback trễ 30 ngày). Nhãn tháng gần nhất chưa đủ độ chín.

## Chỉ tiêu
PR-AUC ≥ 0,55 trên tháng eval; chi phí (gian lận lọt + chặn nhầm) thấp nhất tại ngưỡng chọn; FPR ≤ 2% tại recall 0,8.

## Đánh giá — eval contract (tùy chọn)
Đơn vị: giao dịch. Metric + hướng tốt: PR-AUC ↑, chi phí/1000 GD ↓, FPR@recall=0.8 ↓. Lát cắt: theo tháng, kênh. Người chấm: rule (nhãn chargeback). Độ bất định: bootstrap theo ngày, level 0.95.

## Chia dữ liệu & chống leakage (tùy chọn)
Chiến lược temporal: train ≤ 2024-06, eval tháng 2024-07; embargo nhãn 30 ngày. Feature as-of thời điểm giao dịch; leakage audit: cấm tổng hợp dùng giao dịch tương lai, kiểm tra trùng thẻ giữa fold chỉ qua stateless split theo thời gian.
