---
name: ai-pipeline-sandbox
description: Mandatory execution rule for the AI pipeline - every deployment, test, benchmark, training or install step runs inside a sandbox container (on the GPU server, or local only if the human explicitly allows). Never install libraries without permission. Use before running any code that is not read-only analysis.
---

# Sandbox: mọi việc triển khai/kiểm thử chạy trong container

## Quy tắc (bắt buộc)
1. **Mọi việc triển khai, kiểm thử, benchmark, huấn luyện, export model đều chạy trong 1 sandbox container**: mặc định trên server (container tên `aipipeline-<run>-*`, xem architecture §môi trường train). Chỉ tạo container **trên máy local khi người dùng cho phép rõ ràng** (ghi vào `decisions.md`).
   Tài nguyên khai báo theo task (`plan.json` `mode` + `resources.compute`): task `mode: train` hoặc `compute: gpu` mới cần G3/GPU; các mode còn lại (`evaluate-only | retrieve-only | inference-service | monitor`, `compute: cpu`) chạy container CPU thường, không chờ G3.
2. **Không tự ý cài đặt thư viện** (pip/conda/npm/apt, tải wheel/model, `pip install` vào Python hệ thống/host). Cần gói nào → liệt kê (tên, phiên bản, lý do, nguồn) và hỏi người bằng Orca `ask`; chỉ cài sau khi được đồng ý, và **chỉ cài vào trong container**, không cài vào host.
3. Ghi mọi gói đã được phép vào `requirements.lock`/`environment.md` của module (tên==phiên bản, image tag) để tái lập; image/container gắn version (vd. `env-v0.1`).
4. Không động tới container/tiến trình không phải của mình; không `--privileged`, `--net=host`, `docker system prune`.
5. Cho phép trên host: đọc file, phân tích tĩnh, chạy các script **đã có sẵn** bằng thư viện **đã có sẵn** chỉ khi người đã cho phép chạy local (ghi vào `decisions.md`). Không có phép → ask.

## Bẫy đã gặp (Paddle)
`paddle2onnx` mặc định chạy optimizer Polygraphy và tự `pip install onnx_graphsurgeon` (kéo theo đổi numpy) — luôn export với `--optimize_tool None`; sau mỗi export so `pip freeze` với baseline `requirements.lock`; lệch ⇒ báo `--type error`, gỡ đúng gói lệch trong container của mình.

## Quy trình
1. Đầu task: đọc `mode`/`resources` của task trong plan + kiểm tra `decisions.md` có (a) thông tin server/container khi task cần GPU (G3), (b) phép chạy local (nếu cần), (c) danh sách gói được phép. Task không cần G3 thì cứ chạy; G3 chặn task `mode: train` **hoặc** `resources.compute: gpu` (kể cả `evaluate-only` trên GPU). Thiếu thứ task mình cần → `ask`, không đoán và không "tạm cài".
2. Dựng/dùng container, mount thư mục module + dữ liệu chỉ đọc; chạy lệnh qua `docker exec`; log image tag, lệnh, kết quả vào sổ (`notebook.py log`).
3. Số đo hiệu năng (latency, v.v.) ghi rõ môi trường đo: container/host, giới hạn CPU/RAM (`--cpus`, `--memory`), vì container có thể khác CPU khách.
4. Vi phạm đã xảy ra (đã cài/chạy ngoài sandbox) → báo ngay coordinator, ghi `--type error` vào sổ, gỡ phần đã cài nếu được phép; không giấu.
