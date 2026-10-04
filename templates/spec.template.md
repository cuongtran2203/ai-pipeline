# Spec: <tên hệ thống>

## Mục tiêu
<bài toán nghiệp vụ cần giải quyết>

## Nền tảng triển khai
<GPU / CPU / thiết bị biên; cấu hình; ràng buộc môi trường>

## Input
<dữ liệu đầu vào; cần bổ sung thành phần nào để làm rõ output>

## Output
<khách cần output gì; có tác nhân ngoài làm thay đổi output không>

## Dataset
<data real? số lượng, đã gán nhãn chưa; vài mẫu; hoặc chỉ mô tả>

## Chỉ tiêu
<độ chính xác, tốc độ, chi phí>

## Đánh giá (eval contract, tùy chọn nhưng nên có)
<đơn vị đánh giá (trang / câu hỏi / giao dịch / ...); metric + hướng tốt (cao/thấp);
lát cắt hoặc horizon; người chấm (human / model-judge / rule); độ bất định cần báo (CI/SE)>

## Chia dữ liệu & chống leakage (tùy chọn nhưng nên có)
<chiến lược: random | group (key entity) | temporal (mốc cắt) | rolling-origin (time series);
định nghĩa as-of cho feature; kiểm tra leakage đã làm>

## Ngôn ngữ báo cáo (tùy chọn, mặc định vi)
<vi hoặc en; renderer đọc eval.json `lang`, fallback plan.report_lang, rồi vi>
