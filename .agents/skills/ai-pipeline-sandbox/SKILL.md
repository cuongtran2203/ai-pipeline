---
name: ai-pipeline-sandbox
description: Mandatory execution rule for the AI pipeline - every deployment, test, benchmark, training or install step runs inside a sandbox container (on the GPU server, or local only if the human explicitly allows). Never install libraries without permission. Use before running any code that is not read-only analysis.
---

# Sandbox: mọi việc triển khai/kiểm thử chạy trong container

## Quy tắc (bắt buộc)
1. **Mọi việc triển khai, kiểm thử, benchmark, huấn luyện, export model đều chạy trong 1 sandbox container**: mặc định trên server (container tên `aipipeline-<run>-*`, xem architecture §môi trường train). Chỉ tạo container **trên máy local khi người dùng cho phép rõ ràng** (ghi vào `decisions.md`).
   Tài nguyên khai báo theo task (`plan.json` `mode` + `resources.compute`): task needs_g3 (`mode: train` hoặc `compute: gpu`) mới cần G3/GPU; các task còn lại (`evaluate-only | retrieve-only | inference-service | monitor` với `compute: cpu`) chạy container CPU thường, không chờ G3.
2. **Không tự ý cài đặt thư viện** (pip/conda/npm/apt, tải wheel/model, `pip install` vào Python hệ thống/host). Cần gói nào → liệt kê (tên, phiên bản, lý do, nguồn) và hỏi người bằng Orca `ask`; chỉ cài sau khi được đồng ý, và **chỉ cài vào trong container**, không cài vào host.
3. Ghi mọi gói đã được phép vào `requirements.lock`/`environment.md` của module (tên==phiên bản, image tag) để tái lập; image/container gắn version (vd. `env-v0.1`).
4. Không động tới container/tiến trình không phải của mình; không `--privileged`, `--net=host`, `docker system prune`.
5. Cho phép trên host: đọc file, phân tích tĩnh, chạy các script **đã có sẵn** bằng thư viện **đã có sẵn** chỉ khi người đã cho phép chạy local (ghi vào `decisions.md`). Không có phép → ask.

## Bẫy đã gặp (Paddle)
`paddle2onnx` mặc định chạy optimizer Polygraphy và tự `pip install onnx_graphsurgeon` (kéo theo đổi numpy) — luôn export với `--optimize_tool None`; sau mỗi export so `pip freeze` với baseline `requirements.lock`; lệch ⇒ báo `--type error`, gỡ đúng gói lệch trong container của mình.

## Quy trình
1. Đầu task: đọc `mode`/`resources` của task trong plan + kiểm tra `decisions.md` có (a) thông tin server/container khi task needs_g3 (G3), (b) phép chạy local (nếu cần), (c) danh sách gói được phép. Task không cần G3 thì cứ chạy; G3 chặn task needs_g3 (`mode: train` **hoặc** `resources.compute: gpu`, kể cả `evaluate-only` trên GPU). Thiếu thứ task mình cần → `ask`, không đoán và không "tạm cài".
2. Dựng/dùng container, mount thư mục module + dữ liệu chỉ đọc; chạy lệnh qua `docker exec`; log image tag, lệnh, kết quả vào sổ (`notebook.py log`).
3. Số đo hiệu năng (latency, v.v.) ghi rõ môi trường đo: container/host, giới hạn CPU/RAM (`--cpus`, `--memory`), vì container có thể khác CPU khách.
4. Vi phạm đã xảy ra (đã cài/chạy ngoài sandbox) → báo ngay coordinator, ghi `--type error` vào sổ, gỡ phần đã cài nếu được phép; không giấu.

## Hợp đồng launch huấn luyện (train/eval)

Áp dụng cho mọi lần train/eval/benchmark; chi tiết vai trò ở `skills/ai-pipeline-module-dev/SKILL.md`. Không mâu thuẫn các quy tắc sandbox ở trên.

1. **Snapshot commit bất biến:** lần chạy phải xuất phát từ một commit đã chốt (không chạy từ worktree đang sửa dở). Trước khi chạy ghi **commit SHA**; kết quả chỉ hợp lệ khi SHA có trong sổ và trong `eval.json`/`report.md` (+ `report.html`). Không có SHA ⇒ lần chạy **không tính là bằng chứng**.
2. **Lệnh chạy cố định ghi trước khi chạy:** entrypoint/launcher + tham số nằm trong file đã commit (vd `train.sh`, cấu hình trong `config/`). Chạy đúng lệnh đó; **không chạy tay các biến thể ngoài launcher**. Muốn thử biến thể → sửa launcher/config, commit, rồi chạy lại (để lệnh chạy luôn truy được).
3. **Log + exit code là bằng chứng duy nhất:** kết luận chỉ từ log thật, **exit code**, và artifact/checkpoint (kèm hash). **Không** kết luận từ `status` của dashboard/tracker, trạng thái tiến trình, hay trí nhớ. Log lưu trong run dir/artifact (đường dẫn tuyệt đối), không chỉ trên màn hình.
4. **So sánh công bằng:** mỗi nhánh con chỉ đổi **MỘT yếu tố** so với nhánh tốt nhất hiện tại (baseline/winner); giữ nguyên seed, split, epoch, image/container. Đổi nhiều yếu tố cùng lúc ⇒ kết quả không quy được cho nguyên nhân.
5. **Dừng nhánh sớm:** sau **~3 lần fail/OOM/lỗi liên tiếp** trên cùng một nhánh → dừng, chuyển sang chẩn đoán (`ai-pipeline-diagnose`) thay vì thử mò; ghi lý do dừng vào sổ.
6. **Giới hạn tài nguyên + container:** mọi run trong container của mình (`aipipeline-<run>-*`) với giới hạn CPU/RAM/GPU khai báo theo task; không `--privileged`, không `--net=host`; image/container gắn version. Cần gói chưa có → `ask`, cài trong container.
7. **Sổ:** mỗi run ghi `python scripts/notebook.py log <run_dir> --type experiment --title "..." --body "<commit SHA + lệnh + exit code + số đo chính>" --author module-dev`.
