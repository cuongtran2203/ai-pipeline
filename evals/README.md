# EVAL cấu hình agent — định dạng case

Thư mục này chứa **eval cho chính cấu hình agent** (skill, AGENTS.md, roles, mirror, hook),
không phải eval model. Hai tầng:

- **Static** (`python scripts/evals.py run --static`): không cần agent, chạy CI/pre-commit.
- **Behavioral** (`evals/cases/*.jsonl`): chạy trên agent thật, chấm bằng scorer rule;
  chấm lại offline bằng `--replay <transcript>`.

## Định dạng `evals/cases/*.jsonl`

Mỗi dòng một case JSON:

| Khóa | Bắt buộc | Ý nghĩa |
|---|---|---|
| `id` | có | định danh duy nhất |
| `prompt` | có | tình huống đưa cho agent |
| `expect` | có | danh sách scorer rule |
| `source` | không | `incident` (mặc định `manual`) |
| `description`, `trigger`, `setup`, `version` | không | mô tả / glob kích hoạt / bối cảnh / version |

Scorer rule (regex, không phân biệt hoa thường):

- `must_call` — phải gọi tool khớp `pattern` (tuỳ chọn `tool`).
- `must_not_call` — không được gọi tool khớp `pattern`.
- `must_mention` — phải nhắc `pattern`.
- `must_ask_before` — `ask_pattern` phải xuất hiện trước `before_pattern`.

## Lệnh

```sh
python scripts/evals.py run --static
python scripts/evals.py run --list
python scripts/evals.py run --changed [--base REF]
python scripts/evals.py run --behavior --agent command-code --case host-install-ask
python scripts/evals.py run --replay runs/<id>/evals/<ts>/<case>.transcript.json --case host-install-ask
python scripts/evals.py add-incident --run-dir runs/<id> --title "..." --from-notebook <entry id>
```

12 case hiện có đều sinh từ sự cố thật của `runs/ai-pipeline-v2` và `runs/timesheet-ocr`.
Xem skill `skills/ai-pipeline-evals/SKILL.md` để biết khi nào chạy và giới hạn.
