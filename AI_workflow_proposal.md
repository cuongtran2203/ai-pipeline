**Quy trình phát triển dự án AI (phù hợp cho các dự án cần xây dựng pipeline dùng các model AI (Computer Vision, NLP,...))**

1. **Tổng quan**

   File này dùng để nên ý tưởng phát triển workflow AI agent cho vấn đề nghiên cứu và phát triển các mô hình AI. Workflow này đi từ vấn đề lên ý tưởng, ước lượng mục tiêu hướng đến (độ chính xác, tốc độ xử lý, chi phí phát triển,...), vấn đề dữ liệu và huấn luyện và việc triển khai trên hạ tầng theo nhu cầu của khách hàng. 
2. **Quy trình sản xuất mô hình AI** 

   **2.1 Từ ý tưởng của khách hàng cho đến phát triển mô hình giải quyết bài toán** 

   Khi nhận được yêu cầu từ khách hàng ta cần làm rõ:

   Nền tảng triển khai hệ thống: Cần làm rõ luôn là triển khai trên GPU hay CPU hay thiết bị biên (mobile) =&gt; Liên quan đến giải pháp và lựa chọn mô hình  
   \+ Input: Bài toán có gì, từ input đó có cần bổ sung các thành phần gì để làm rõ output hay không =&gt; Cái này rất quan trọng vì 1 module trong pipeline có thể là 1 mô hình AI cần được xây dựng trong đấy

- Output: Khách hàng cần output gì? Output này có bị thay đổi bởi các tác nhân khác bên ngoài tác động hay không (Với những bài toán về computer vision sẽ gặp vấn đề như thế này)
- Làm rõ vấn đề datasets: Khách hàng có cung cấp được data real cho mình hay không hay là chỉ qua mô tả của khách (Dựa vào đó làm các tools sinh dữ liệu =&gt; Cái này sẽ chỉ đảm bảo model sẽ nhận diện được các partern đó mà k thể đảm bảo 1 độ chính xác cao trên prod được)

  **2.2 Lên kế hoạch phát triển hệ thống AI giải quyết bài toán**
  - Datasets: Chiếm đến 90% sự thành công của dự án. Cần làm rõ chi tiết, phân tích chi tiết dữ liệu cần xử lý để có 1 kế hoạch chi tiết và chính xác để làm hệ thống AI
  - Lựa chọn module: Trong 1 hệ thống AI thì có thể sử dụng model AI hoặc các thuật toán truyền thống để giải quyết vấn đề. Việc lựa chọn các phương án cần dựa vào resource triển khai của khách hàng và data cần xử lý 
  - Phần hậu xử lý và tiền xử lý: Cái này cx đến từ việc phân tích chi tiết data và sự quan sát đầu ra của mô hình để đưa ra các giải pháp tiền xử lý và hậu xử lý chính xác nhất =&gt; đẩy độ chính xác của cả hệ thống lên
  - Lựa chọn model và huấn luyện mô hình và kiểm thử: Kiểm soát nghiêm ngặt phần này thì chất lượng của từng module mới được đảm bảo (Xem chi tiết mục Quy trình phát triển 1 model AI)

Chung quy lại việc phát triển 1 giải phát AI cần qua các khâu:  
Phân tích nghiệp vụ và kiểm định datasets-&gt; Lên kế hoạch phát triển và xây dựng pipeline (Mức độ plan) -&gt; Phát triển từng module theo quy trình phát triển model AI -&gt; Kiểm thử toàn pipeline và phân tích chi tiết lỗi sai -&gt; Tối ưu để đạt mục tiêu -&gt; Release pieline

Nhắc lại 1 lần nữa việc làm chủ dữ liệu sẽ đóng vai trò trọng yếu cho sự thành công của pipeline khi triển khai trên production



&nbsp;

3. **Quy trình phát triển 1 model AI**

- **Lựa chọn model và hiểu bài toán:** Từ những phân tích ban đầu chúng ta cần hiểu được là với đề bài này ta cần lựa chọn model gì (cân bằng giữa độ chính xác và tốc độ xử lý): Research kĩ là có ai đã làm bài này hoặc 1 vấn đề tương tự hay chưa?, Có datasets public cho vấn đề này hay không? Cần có các agent phối hợp debate để thống nhất luận điểm này?
- **Datasets:** Data chiếm đến 90% sự thành công của model ra prod. Vấn đề này cần được làm rõ ngay từ đầu trước khi đưa ra phương án và dự trù độ chính xác của các hệ thống AI. Cần đặt ra câu hỏi:  
  \- Bạn có datasets cho bài toán này không? Nếu có tình trạng của datasets này như thế nào (Số lượng, các phân tích dựa trên các công thức tính toán, đã được gán nhãn hay chưa). Nếu không có nhiều thì có 1 vài samples mẫu hay không? Nếu không có dữ liệu thì bạn có thể mô tả chi tiết dữ liệu được hay không? Việc nhận định và hiểu dữ liệu sẽ giúp cho agent có những hướng đi chính xác trong việc xây dựng các công cụ tạo sinh dữ liệu và chiến lược training mô hình
- **Huấn luyện model:** Việc huấn luyện model sẽ được thực thi trên các server GPU. Lúc này agent cần thông báo đến người phát triển về việc cung cấp thông tin server, loại GPU, version cuda , framework sử dụng (Nếu phát triển model từ đầu hoặc cung cấp link opensource)
- **Kiểm thử model:** Nếu trong trường hợp có dữ liệu real thì sẽ có 1 bộ val riêng cho real (Nếu chi phí gán nhãn cho bộ dữ liệu rất lớn mà có dữ liệu real thì nhất định phải gán nhãn dữ liệu real để eval -&gt; ít nhất là 50 samples =&gt; Chỉ có dữ liệu real mới đánh giá rõ về điểm mạnh yếu của model). Nếu không có real thì hãy thảo luận thật kĩ về việc sinh dữ liệu sao cho bao quát được phân bố của dữ liệu sinh giống dữ liệu real nhất có thể. Lưu ý để tiện cho việc tracking và lên phương án cải tiến model thì tất cả model và datasets training đề được gán version và sau mỗi lần đánh giá thì agent luôn export 2 bản report:  
  **Bản báo cáo bằng file md có template như sau:**  
  \## Template báo cáo

  Mọi báo cáo gửi user (sau training, đánh giá hoặc tổng hợp) viết bằng **tiếng Việt**, theo đúng ba phần:

  \### 1. Tổng quan

  \- **Hiện trạng bài toán:** mục tiêu, baseline đang dùng, các vấn đề chính, bộ đánh giá và giới hạn nhãn.

  \- **Phương pháp đang tiến hành:** data (nguồn, số lượng, tỉ lệ trộn, chia train/val/test), model và checkpoint khởi tạo, thay đổi so với lần trước, cấu hình pipeline.

  \- **Kết quả hiện tại:** số đo module và end-to-end chính, so với baseline; kết luận ngắn (tốt hơn / chưa tốt hơn).

  \### 2. Nội dung chi tiết

  \- **Bảng độ chính xác chi tiết từng thành phần:** module (layout, char-det, REC…) và từng field end-to-end (ngày, giờ bắt đầu, giờ kết thúc, giờ nghỉ, work\_code, tên công ty, họ tên, mã NV). Ghi số đúng/tổng, %, baseline và bản mới trên cùng bộ đánh giá.

  \- **Các lỗi sai còn tồn đọng:** nhóm lỗi, số lượng, ví dụ cụ thể (trang/dòng), nguyên nhân đã xác nhận hoặc giả thuyết cần kiểm chứng, các trường hợp bị giảm chất lượng.

  **3. Kết luận**

  \- Nêu lại các lỗi sai còn tồn đọng.

  \- Giải pháp khắc phục cho từng lỗi, thứ tự ưu tiên, cách đo để xác nhận.

  \- Có đề xuất dùng checkpoint/cấu hình mới hay không; việc triển khai theo phạm vi user cho phép.
- **Bản báo cáo bằng file htm**l : Visualize các lỗi sai chi tiết theo từng nhóm sai đã phân cụm 

**Triển khai mô hình:** Dựa vào yêu cầu của khách hàng mà ta lựa chọn phương thức triển khai (Agent hỏi trực tiếp human trong quá trình draft lên plan thực thi dự án)



&nbsp;
