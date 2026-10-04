---
name: ai-pipeline-autonomy
description: Human-on-the-loop for the AI pipeline - per-run autonomy policy, append-only audit log and read-only supervisor. Use when starting/stopping workers, changing roster or policy, handling gates, or when the user asks "ai được tự làm gì / xem cảnh báo ở đâu / can thiệp thế nào".
---

# Autonomy: human-on-the-loop (không phải human-in-the-loop)

**Human-in-the-loop** = người phải trả lời từng chặng (G1/G2/G3, `ask`, chọn agent) thì việc mới chạy tiếp.
**Human-on-the-loop** = pipeline tự chạy trong chính sách đã duyệt (`bounded_auto` + gate);
người chỉ giám sát qua cảnh báo và can thiệp khi lệch chính sách.
ai-pipeline mặc định **bounded_auto + gate**: gate G1/G2/G3 vẫn bắt buộc, còn lại tự chạy tới khi chạm trần.

## Live supervision (read-only)
`python scripts/orca_snapshot.py runs/<id> --out runs/<id>/workers.json` turns Orca's `worker-list` into the snapshot the supervisor reads (live workers, last agent-status time, failed ones; a failed dispatch already replaced by a retry is skipped), then `python scripts/supervisor.py runs/<id> --workers runs/<id>/workers.json`. Run both at each coordinator checkpoint (after every wait) and before starting a wave. Neither command starts, stops, retries or releases anything.

## 1. Policy theo run (`autonomy_policy.json`)

Copy `templates/autonomy_policy.template.json` thành `runs/<id>/autonomy_policy.json`, duyệt ở G2:

- `default_mode` + `modes` theo phase: `manual` (người làm, không tự động),
  `bounded_auto` (tự chạy trong cap), `approval_required` (cần `approve` trước).
  Mặc định: intake/release `manual`, optimize `approval_required`, còn lại `bounded_auto`.
- `allowed_actions`: loại thao tác được phép (`ask`, `start`, `stop`, `release`,
  `roster_change`, `policy_change`, `override`, `gate`). Ngoài danh sách → hỏi người.
- `caps`: trần tích lũy (`max_tasks`, `max_api_cost_usd`, `max_elapsed_hours`,
  `max_gpu_hours`, `max_tokens`; `null` = không giới hạn).
- `warn_at` (vd. 0.8): ngưỡng cảnh báo; `on_cap`: hành vi khi chạm trần
  (`require_approval` | `pause` | `stop` — chỉ là đề xuất, cần người xác nhận).
- `policy_version` (v1, v2...): mỗi lần đổi policy tăng version, ghi `audit.jsonl`,
  nối cạnh `supersedes` vào KG. Đổi policy/roster = hỏi người trước.

```sh
python scripts/autonomy.py validate runs/<id>/autonomy_policy.json
python scripts/autonomy.py check runs/<id> --action start --phase build --task M1
python scripts/autonomy.py approve runs/<id> --scope M1 --decision approve --reason "..."
```

`check` exit 0 = được phép, 2 = bị cấm (lý do in ra). `plan_to_orca.py --start-ready`
tự gọi `check` trước khi start và từ chối task bị cấm; run chưa có policy vẫn chạy
(tương thích ngược).

## 2. Audit log (`audit.jsonl`, append-only)

Mọi `ask`/`answer`, gate G*, đổi roster/policy, start/stop/override/release đều ghi
1 dòng: actor, UTC timestamp, scope, policy version, quyết định, lý do, refs:

```sh
python scripts/autonomy.py log runs/<id> --event gate --scope G2 --decision approved --reason "..."
```

Chỉ append, không sửa/xóa lịch sử. Event approve/gate tự nối cạnh `approved_by` /
`decided_by` vào `knowledge/` (lớp KG của `ai-pipeline-knowledge`).

## 3. Supervisor (`supervisor.py`: chỉ đọc + thông báo)

```sh
python scripts/supervisor.py runs/<id> --workers workers.json   # snapshot worker-list
```

Phát hiện: worker im lặng quá `stale_worker_minutes`, gần/vượt cap, worker failed.
In cảnh báo cô đọng + gợi ý hành động (lệnh `orca` tương ứng). Đề xuất pause/kill ở
mức Run nhưng **cần người xác nhận**; supervisor không tự kill/retry, lỗi/timeout
không tự chuyển thành retry vô hạn.

## 4. Người dùng xem cảnh báo và can thiệp thế nào

- `python scripts/project_status.py runs/<id>`: dòng `Autonomy:` hiện policy/budget
  (% từng cap) và chặn start khi vượt trần.
- `python scripts/supervisor.py runs/<id> --workers <snapshot>`: cảnh báo worker + budget.
- Can thiệp: trả lời `ask`/gate, `autonomy.py approve ...`, sửa `autonomy_policy.json`
  (tăng version, duyệt lại), hoặc xác nhận pause/kill khi supervisor đề xuất.
