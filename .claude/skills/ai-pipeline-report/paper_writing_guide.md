# Hướng dẫn viết technical report (bài báo khoa học cho cả dự án)

Chắt lọc từ [Master-cai/Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) (MIT), chuyển sang bối cảnh dự án AI nhiều round. Cấu trúc mẫu: `templates/technical_report.template.md`.

## Nguyên tắc cứng
1. **Khẳng định phải có bằng chứng.** Mọi khẳng định ở Tóm tắt/Giới thiệu/Kết luận đúng về kỹ thuật và có số liệu hỗ trợ (Phụ lục D). Không có bằng chứng thì thêm thí nghiệm hoặc giảm nhẹ/bỏ khẳng định.
2. **Một đoạn một thông điệp**, nêu thông điệp ở câu đầu. Danh từ tự đứng được: định nghĩa thuật ngữ trước khi dùng lại, giữ thuật ngữ nhất quán suốt bài. Mỗi câu nối với câu trước bằng quan hệ rõ (nguyên nhân, tương phản, hệ quả, làm rõ, ví dụ).
3. **Reverse outline sau mỗi mục:** viết ra luận điểm chính, câu chủ đề từng đoạn, ý chứng minh dưới từng đoạn; đoạn nào không ánh xạ được về luận điểm thì sửa hoặc xoá.
4. **Số liệu so sánh** cùng tập, cùng metric, cùng script chấm; nếu khác thì nói rõ và không kết luận hơn/kém. Báo cả điểm mạnh lẫn failure case; kết quả âm và round bị revert vào Mục 8.
5. **Không viết như vá từng lỗi của baseline ngây thơ** ("thêm X rồi thêm Y"): gom thành vài đóng góp có insight chung.
6. Hình và bảng là nội dung chính, không phải trang trí.

## Tóm tắt (chọn một khuôn)
Trước khi viết trả lời: bài toán kỹ thuật là gì và vì sao chưa có giải pháp sẵn · đóng góp là gì · vì sao nó hiệu quả về bản chất · lợi thế + insight mới.
- **V1 Thách thức → Đóng góp:** nhiệm vụ → khó khăn của cách cũ → 1-2 câu đóng góp (chỉ nêu tên kỹ thuật, không giải thích từng bước) → lợi ích → tóm tắt thực nghiệm.
- **V2 Thách thức → Insight → Đóng góp:** như V1 nhưng thêm một câu insight trước khi nêu đóng góp hiện thực hoá insight.
- **V3 Nhiều đóng góp:** nhiệm vụ → (tương phản với cách cũ) → mỗi đóng góp một câu kèm lợi thế kỹ thuật → tóm tắt thực nghiệm.
Kiểm: người đọc nhận ra nhiệm vụ, thách thức, insight/đóng góp, kết quả chỉ sau một lượt đọc? Tên kỹ thuật tự giải thích được? Có câu nào trộn quá nhiều thông điệp?

## Giới thiệu
Nhiệm vụ + ứng dụng → vì sao khó (thách thức kỹ thuật có số đo, nhắm vào chính cái dự án giải quyết) → hướng thông thường và vì sao chưa đủ → insight + hệ thống → kết quả + đóng góp (mỗi đóng góp là kết quả kiểm chứng được) → lộ trình các phần. Khi đã chẩn đoán DATA/MODEL/STRUCTURE/OBJECTIVE/NOISE thì dùng kết luận chẩn đoán làm phần thách thức.

## Phương pháp
Trả lời trước: hệ có những module nào; với mỗi module: chạy thế nào, vì sao cần, vì sao hiệu quả. Rồi vẽ sơ đồ pipeline, suy ra các mục con từ sơ đồ. Mỗi mục con đủ ba yếu tố: **Thiết kế** (cấu trúc dữ liệu/mạng + luồng input → bước → output) viết trước để có xương sống; **Động lực** (vì có vấn đề X nên thiết kế Y: "Một vấn đề còn lại là...", "Tuy nhiên...", "Các cách trước gặp khó ở..."); **Ưu điểm** (hơn phương án thay thế ở điểm nào, gắn với số đo). Mục Tổng quan gồm: bối cảnh, đóng góp lõi, con trỏ tới hình pipeline, bản đồ các mục con. Đủ chi tiết để người khác tái lập.

## Thực nghiệm
Ba câu hỏi bắt buộc trả lời bằng thí nghiệm: (1) tốt hơn baseline mạnh không (cùng split, tiền xử lý, cách chấm) · (2) module/thiết kế nào tạo ra mức tăng (ablation bỏ/thay/tắt, báo delta; thêm ablation tương tác khi các module gắn với nhau) · (3) giới hạn ở tình huống khó hơn (ngoài phân bố, hiếm, nhiễu), kèm failure mode. Lập kế hoạch từ ngược: mỗi đóng góp → một thí nghiệm kiểm chứng; mỗi module/tham số then chốt → một ablation.

Quy tắc bảng: chú thích **trên** bảng, chú thích nói về thiết lập/giao thức chứ không bàn dài · không kẻ dọc, ít kẻ ngang (chỉ phân nhóm) · hướng metric trong tiêu đề cột (↑/↓), có đơn vị · cùng số chữ số thập phân trong một cột · cột chữ canh trái, cột số canh thẳng · tô đậm số tốt nhất, không tô quá nhiều ô · một bảng một thông điệp.

## Kết luận
Nhắc bài toán + ý kỹ thuật cốt lõi → bằng chứng mạnh nhất → tác động thực tế/insight mới → một đoạn giới hạn → hướng tiếp theo cụ thể. Giới hạn nên gắn với phạm vi bài toán (chế độ dữ liệu, giả định, phạm vi triển khai); tách *lỗi kỹ thuật* (thua baseline mạnh ở chỉ số then chốt) khỏi *giới hạn phạm vi* (vẫn cạnh tranh nhưng bị chặn bởi thiết lập hiện tại).

## Tự rà soát phản biện (Phụ lục D, bắt buộc trước khi coi là xong)
Đọc như phản biện khó tính; mỗi câu trả lời kèm bằng chứng từ chính bài viết; đánh dấu `pass` / `cần sửa` / `cần thí nghiệm mới`; lặp đến khi không còn rủi ro bị bác lớn.
1. **Đóng góp:** người đọc học được gì mới? Có giải quyết lỗi đáng kể (không tầm thường/quá phổ biến)? Ý tưởng có không hiển nhiên? Mức tăng có đáng ngạc nhiên hay chỉ là dự đoán được? Có ít nhất một loại mới (bài toán / pipeline / module / phát hiện thiết kế / insight)?
2. **Độ rõ ràng:** người am hiểu có tái lập được? Mỗi module đủ chi tiết và động lực nối với một thách thức? Thuật ngữ/ký hiệu nhất quán? Mỗi đoạn một thông điệp?
3. **Sức mạnh thực nghiệm:** tăng so với baseline mạnh có ý nghĩa (so với khoảng tin cậy)? Điểm tuyệt đối đủ cạnh tranh? Nhất quán qua nhiều tập/thiết lập/metric? Báo cả failure case?
4. **Độ đầy đủ đánh giá:** ablation cho mọi lựa chọn thiết kế chính? Baseline mạnh/gần nhất cùng điều kiện? Metric chuẩn và đủ? Tập đủ khó? Giao thức so sánh/ablation được ghi rõ?
5. **Độ hợp lý thiết kế:** thiết lập có thực tế? Có giả định vô lý hay lỗi kỹ thuật ẩn? Bền vững mà không cần chỉnh siêu tham số theo từng ca? Lợi ích có lớn hơn độ phức tạp và giới hạn mới? Phản biện có thể lập luận lợi ích ròng âm không?

Các lý do bị bác thường gặp: đóng góp không đủ (lỗi nhắm tới quá phổ biến, kỹ thuật đã được khai thác kỹ) · viết thiếu chi tiết/ thiếu động lực module · cải thiện biên so với baseline hoặc điểm tuyệt đối vẫn yếu · thiếu ablation/baseline/metric, tập quá dễ · thiết lập phi thực tế, phương pháp có lỗ hổng hoặc cần chỉnh tay theo từng kịch bản.

## Quy trình agent
1. `python scripts/tech_report.py init runs/<id> --set TITLE=... --set AUTHORS=... --set PROJECT=... --set PERIOD=...`.
2. Gom nguồn: `requirements.md`, `analysis`, `decisions.md`, `notebook/` (kể cả kết quả âm), `reports/round-*/{report.md,eval.json,data_report.html,method_report.html,results_report.html}`, `release/`. Không có số thì ghi N/A.
3. Lập mini-outline và bảng "khẳng định → bằng chứng" trước khi viết văn; viết Phương pháp và Thực nghiệm trước, Tóm tắt/Giới thiệu sau cùng.
4. Với từng mục: viết → reverse outline → sửa. Sơ đồ pipeline/kiến trúc bằng skill `ai-pipeline-diagram` (đã `validate`).
5. Tự rà soát năm chiều ở Phụ lục D, xử lý hết mục `cần sửa`.
6. `python scripts/tech_report.py check runs/<id>` phải OK; báo người dùng đường dẫn + liệt kê khẳng định còn "Cần bằng chứng".
