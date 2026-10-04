# Spec: RAG chatbot hỏi đáp chính sách nội bộ

## Mục tiêu
Chatbot trả lời câu hỏi của nhân viên dựa trên kho tài liệu chính sách nội bộ, mọi câu trả lời phải kèm trích dẫn đúng đoạn nguồn và không bịa đặt.

## Nền tảng triển khai
Cloud CPU (2 vCPU, 4GB RAM), Python, container `rag-svc-v0.2`. Không có GPU; không huấn luyện model, chỉ dùng LLM API + index truy hồi.

## Input
Câu hỏi tiếng Việt của nhân viên + kho 1.200 tài liệu chính sách (PDF, đã trích text, có version).

## Output
Câu trả lời ngắn gọn kèm danh sách trích dẫn (doc_id + đoạn). Output không bị tác nhân ngoài thay đổi sau khi chốt kho tài liệu version.

## Dataset
400 cặp hỏi–đáp chuẩn do nghiệp vụ biên soạn (đã gán nhãn đáp án + citation), 800 log hỏi thật chưa gán nhãn. Không sinh thêm dữ liệu tổng hợp.

## Chỉ tiêu
Faithfulness ≥ 0.85 (LLM-judge), citation precision ≥ 0.90, recall@5 ≥ 0.80, p95 latency ≤ 4s.

## Đánh giá — eval contract (tùy chọn)
Đơn vị: câu hỏi. Metric + hướng tốt: faithfulness ↑, citation precision ↑, recall@5 ↑, p95 latency ↓. Lát cắt: factual / how-to. Người chấm: model (LLM-judge rubric v1) + spot-check người 10%. Độ bất định: bootstrap 1000, level 0.95.

## Chia dữ liệu & chống leakage (tùy chọn)
Chiến lược group theo phiên hỏi của user (không để câu hỏi cùng phiên lọt sang hai fold). Tài liệu eval lấy từ snapshot kho v1.4, retrieval index build từ cùng snapshot.

## Ngôn ngữ báo cáo (tùy chọn, mặc định vi)
en
