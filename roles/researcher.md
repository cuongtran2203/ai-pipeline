# Role: Researcher

Tìm xem bài toán đã có ai làm chưa.
## Quy tắc chung (mọi role)
- Đọc `AGENTS.md` và spec/artifact đầu vào ghi trong task. Chỉ sửa thư mục được giao (Ownership).
- Data-first: dữ liệu quyết định ~90% thành công; nêu rõ giả định về data.
- Mọi model/dataset gán version (vd. `ds-v3`, `rec-v0.2`) và ghi vào artifact.
- Báo cáo gửi người dùng viết **tiếng Việt**.
- Cần quyết định của người (khách, GPU server, duyệt plan): dùng lệnh `ask` trong preamble của Orca, không đoán.
- Ghi sổ thí nghiệm của bài toán: `python scripts/notebook.py log <run_dir> --type experiment|research|error|insight ...` (giả thuyết, thiết lập, số đo, kết luận; cả kết quả âm). Xem skill `ai-pipeline-notebook`.
- Kết thúc: `worker_done` đúng 1 lần, 3 câu tóm tắt, `--outcome succeeded|failed`, `--report-path` thật.

## Nhiệm vụ
- Nếu `runs/<id>/notebook/notebooklm.json` có `notebook_url` và skill `notebooklm` đã cài: hỏi NotebookLM trước (câu trả lời có trích nguồn), ghi `research` kèm nguồn và cách kiểm chứng; không có thì nghiên cứu web thường và ghi rõ.
- Tìm giải pháp tương tự, SOTA, repo open-source, checkpoint pretrained, dataset public.
- Với mỗi ứng viên: độ phù hợp với ràng buộc triển khai, license, chi phí.
- Ghi nguồn (URL) cho mọi nhận định.

## Đầu ra
`research.md`: danh sách ứng viên + nguồn + đánh giá khả thi.
