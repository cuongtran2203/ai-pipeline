# {{TITLE}}

**Tác giả:** {{AUTHORS}}  
**Đơn vị / dự án:** {{PROJECT}}  
**Ngày:** {{DATE}} · **Giai đoạn thực hiện:** {{PERIOD}}  
**Run:** `{{RUN_ID}}`

<!-- HINT: Tiêu đề nêu bài toán + ý chính của giải pháp, không phải tên dự án. Toàn bộ HINT và {{...}} phải được xoá/điền trước khi `tech_report.py check` OK. Số liệu lấy từ eval.json, sổ thí nghiệm, 3 tài liệu round; chưa đo thì ghi "N/A", không suy diễn. -->

---

## Tóm tắt (Abstract)

{{ABSTRACT}}

<!-- HINT: Một đoạn 150-250 từ, theo thứ tự: (1) bài toán + vì sao chưa có giải pháp sẵn; (2) khó khăn kỹ thuật đã đo được; (3) insight / đóng góp chính (chỉ nêu tên kỹ thuật, không giải thích từng bước); (4) lợi ích kỹ thuật; (5) kết quả định lượng trên tập đánh giá nào, đạt/chưa đạt chỉ tiêu; (6) giới hạn lớn nhất. Mọi con số trong Tóm tắt phải có trong Mục 5 và Phụ lục D. -->

**Từ khóa:** {{KEYWORDS}}

---

## 1. Giới thiệu (Introduction)

{{INTRO_TASK}}

<!-- HINT: Đoạn 1 - nhiệm vụ và nơi nó nằm trong hệ thống/ứng dụng, chỉ tiêu của người dùng (lấy từ requirements.md). -->

{{INTRO_CHALLENGE}}

<!-- HINT: Đoạn 2 - vì sao khó: mỗi khó khăn một câu kèm số đo (chẩn đoán DATA/MODEL/STRUCTURE/OBJECTIVE/NOISE từ analysis + diagnose). -->

{{INTRO_APPROACH}}

<!-- HINT: Đoạn 3 - hướng thông thường và vì sao chưa đủ (bằng chứng đo được), rồi insight của dự án và hệ thống gồm những tầng nào. -->

{{INTRO_RESULT}}

<!-- HINT: Đoạn 4 - kết quả chính + tập đánh giá + chỉ tiêu đạt hay chưa; nêu cả hướng không cải thiện. -->

**Đóng góp.**

- {{CONTRIBUTION_1}}
- {{CONTRIBUTION_2}}

<!-- HINT: Mỗi đóng góp = một kết quả kiểm chứng được kèm số (không phải "chúng tôi xây hệ thống X"). Xoá dòng thừa, thêm dòng nếu cần. -->

Phần 2 điểm qua các hướng liên quan. Phần 3 mô tả hệ thống. Phần 4 trình bày thiết lập thực nghiệm. Phần 5 trình bày kết quả. Phần 6 thảo luận và nêu hạn chế. Phần 7 kết luận. Phần 8 báo cáo các thực nghiệm bổ sung có kết quả âm.

---

## 2. Nghiên cứu liên quan (Related Work)

{{RELATED_WORK}}

<!-- HINT: Không khảo sát đầy đủ; 2-4 hướng gần nhất, mỗi hướng một đoạn: họ làm gì, giới hạn ở đâu so với bài toán này, dự án dùng/đối chiếu thế nào. Nguồn trích phải có trong Tài liệu tham khảo; số liệu công bố của người khác ghi rõ khác miền/khác tập đo. -->

---

## 3. Phương pháp đề xuất (Proposed Method)

### 3.1. Tổng quan hệ thống

{{METHOD_OVERVIEW}}

<!-- HINT: 1-2 câu bài toán, 1-2 câu đóng góp lõi, sơ đồ luồng suy luận (Hình 1: dùng skill ai-pipeline-diagram, chỉ nhúng sơ đồ đã `validate` VALID; hoặc khối ``` ASCII), rồi nói mục 3.x nào làm gì. -->

### 3.2. {{MODULE_1_NAME}}

{{MODULE_1}}

<!-- HINT: Mỗi module một mục con, đủ 3 phần: **Động lực** (vì có vấn đề X nên cần Y, kèm bằng chứng đo), **Thiết kế** (cấu trúc dữ liệu/mạng + luồng input -> bước -> output, công thức nếu cần, đủ để tái lập), **Ưu điểm** (tốt hơn phương án thay thế ở điểm nào, gắn với số đo; nêu cả cái giá). Thêm/bớt mục 3.x theo số module thực tế. Giữ thuật ngữ nhất quán. -->

---

## 4. Thiết lập thực nghiệm (Experimental Setup)

### 4.1. Dữ liệu

{{DATA_SETUP}}

<!-- HINT: Nguồn, version, cách gán nhãn, kích thước train/val/test, cách chia (theo tài liệu/nhóm/thời gian), dữ liệu thật hay sinh, tập test khoá đã mở hay chưa; bảng các version dữ liệu + bằng chứng dẫn tới mỗi thay đổi. Tóm từ data_report.html các round. -->

### 4.2. Các hệ so sánh

{{BASELINES}}

<!-- HINT: Liệt kê baseline mạnh và gần nhất (không chỉ baseline yếu), mỗi hệ một dòng: tên, khác gì, cùng điều kiện chấm. -->

### 4.3. Chỉ số, độ bất định và môi trường

{{METRICS_ENV}}

<!-- HINT: Metric + hướng tốt (↑/↓), công thức nếu không chuẩn, khoảng tin cậy (cách tính), seed, phần cứng, phiên bản thư viện, siêu tham số chính (bảng đầy đủ để ở Phụ lục A). Khớp `eval_contract` trong eval.json. -->

### 4.4. Quy trình

{{PROCEDURE}}

<!-- HINT: Vòng lặp đã chạy (phân tích lỗi -> chẩn đoán -> cải tiến -> đánh giá lại), checkpoint chọn theo tập nào, tập nào chỉ dùng so sánh, mọi lần đổi bộ lọc/tập đánh giá khiến số liệu không so trực tiếp được. -->

---

## 5. Kết quả thực nghiệm (Results)

### 5.1. So sánh các hệ trên tập đánh giá

{{RESULTS_MAIN}}

<!-- HINT: Câu đầu = thông điệp của bảng. Bảng: chú thích ĐẶT TRÊN bảng, ghi hướng metric trong tiêu đề cột, cùng số chữ số thập phân, tô đậm số tốt nhất, kèm khoảng tin cậy; một bảng một thông điệp. Chênh lệch nhỏ hơn khoảng tin cậy thì nói là không phân biệt được. -->

### 5.2. Ablation: đóng góp của từng thành phần

{{RESULTS_ABLATION}}

<!-- HINT: Bỏ/thay/tắt từng module chính và báo delta so với hệ đầy đủ; mỗi khẳng định thiết kế trong Mục 3 phải có một dòng ablation. Thiếu ablation thì ghi N/A và đưa vào Hạn chế. -->

### 5.3. Phân tích lỗi còn lại và trường hợp khó

{{RESULTS_ERRORS}}

<!-- HINT: Bảng nhóm lỗi: nhóm · số lượng/tỉ lệ (nêu mẫu số) · ví dụ · nguyên nhân CÓ bằng chứng. Báo cả điểm mạnh lẫn failure case; lấy từ report.md/report.html các round. -->

---

## 6. Thảo luận (Discussion)

{{DISCUSSION}}

<!-- HINT: Mỗi đoạn một bài học in đậm ở câu đầu (vì sao kết quả như vậy, điều gì đáng tin/không, ràng buộc như giấy phép, an toàn, chi phí). Không khẳng định quá bằng chứng. -->

**Hạn chế.**

1. {{LIMITATION_1}}

<!-- HINT: Nêu thẳng: tập đánh giá nhỏ/lệch, test khoá chưa chấm, dữ liệu sinh, nhiễu nhãn/ceiling chưa đo, thiếu ablation/độ trễ, phạm vi chưa phủ, giấy phép chưa xác minh. Phân biệt lỗi kỹ thuật với giới hạn phạm vi. Thêm dòng 2, 3... nếu cần. -->

---

## 7. Kết luận và hướng phát triển (Conclusion & Future Work)

**Kết luận.** {{CONCLUSION}}

<!-- HINT: 4 ý theo thứ tự: bài toán + giải pháp; ý chính (insight); bằng chứng mạnh nhất kèm số; giới hạn phạm vi. Không thêm khẳng định mới. -->

**Hướng tiếp theo.**

1. {{FUTURE_1}}

<!-- HINT: Mỗi hướng cụ thể và kiểm chứng được (làm gì, đo bằng gì), xếp theo ưu tiên, bám vào hạn chế ở Mục 6 / playbook. -->

---

## 8. Thực nghiệm bổ sung: các hướng không cải thiện

{{NEGATIVE_RESULTS}}

<!-- HINT: Mỗi hướng đã thử mà không giúp: giả thuyết · thiết lập · số đo · nguyên nhân quan sát được · quyết định. Kết quả âm và round bị revert bắt buộc ghi ở đây để người sau không lặp lại. Không có thì ghi "Không có hướng nào bị loại trong phạm vi báo cáo này." -->

---

## Tài liệu tham khảo (References)

1. {{REFERENCE_1}}

<!-- HINT: Mỗi nguồn: tác giả/năm/tên/nơi đăng hoặc đường dẫn + giấy phép nếu là dữ liệu/mô hình. Gồm cả dataset, mô hình nền, công cụ chấm. -->

---

## Phụ lục (Appendix)

### A. Siêu tham số huấn luyện

{{APPENDIX_HYPERPARAMS}}

<!-- HINT: Bảng tham số của mô hình chính (khởi tạo, dữ liệu, optimizer/lr, batch, độ chính xác, số bước/epoch, seed, chọn checkpoint, thời gian/VRAM). -->

### B. Triển khai và kiểm tra tái lập

{{APPENDIX_REPRO}}

<!-- HINT: Cách tái lập điểm (lệnh, môi trường, sai khác cho phép) và trạng thái triển khai/độ trễ nếu có; chưa đo thì N/A. -->

### C. Mã nguồn, dữ liệu và artifact

{{APPENDIX_ARTIFACTS}}

<!-- HINT: Đường dẫn tương đối tới run dir, decisions.md, reports/round-*, notebook/, mã sinh dữ liệu/huấn luyện/chấm điểm, checksum dataset/model. Không dán secret hay dữ liệu nhạy cảm. -->

### D. Đối chiếu khẳng định và bằng chứng; tự rà soát (dành cho người soát bản thảo, bỏ khi nộp)

*Bảng D1. Các khẳng định chính trong Tóm tắt và Giới thiệu.*

| Khẳng định | Bằng chứng | Trạng thái |
|---|---|---|
| {{CLAIM_1}} | {{EVIDENCE_1}} | {{STATUS_1}} |

<!-- HINT: Mỗi khẳng định chính trong Tóm tắt/Giới thiệu/Kết luận một dòng; Trạng thái chỉ là một trong: "Đã có bằng chứng", "Đã có bằng chứng trên <phạm vi>; không kết luận cho <phạm vi khác>", "Cần bằng chứng: <thiếu gì>". Khẳng định không có bằng chứng thì giảm nhẹ hoặc bỏ khỏi Tóm tắt. -->

*Tự rà soát theo năm chiều* (đóng góp · độ rõ ràng · sức mạnh thực nghiệm · độ đầy đủ đánh giá · độ hợp lý thiết kế): {{SELF_REVIEW}}

<!-- HINT: Đóng vai phản biện khó tính, mỗi chiều 1-2 câu: điểm yếu lớn nhất và đã xử lý bằng cách nào hoặc còn mở. Đánh dấu từng mục pass / cần sửa / cần thí nghiệm mới; mục "cần thí nghiệm mới" phải xuất hiện trong Hạn chế hoặc Hướng tiếp theo. -->
