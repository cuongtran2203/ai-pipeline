# EVAL cấu hình agent — định dạng case + transcript

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
| `expect` | có | danh sách scorer rule (được rỗng với case `draft`) |
| `status` | không | `active` (mặc định) hoặc `draft` |
| `source` | không | `incident` (mặc định `manual`) |
| `description`, `trigger`, `setup`, `version` | không | mô tả / glob kích hoạt / bối cảnh / version |

Case `status: draft` (vd. case vừa sinh bằng `add-incident`, `expect=[]`) **không được tính
vào pass/fail**: chấm ra `inconclusive`, `run --static` in cảnh báo, và không dùng để chặn CI.

## Scorer rule (regex, không phân biệt hoa thường)

- `must_call` — phải có **event tool** khớp `pattern` (tuỳ chọn `tool`).
- `must_not_call` — **event tool** không được khớp `pattern`.
- `must_mention` — văn bản (raw + message) phải nhắc `pattern` (đây là scorer duy nhất chấm prose).
- `must_ask_before` — theo **thứ tự event**, `ask_pattern` phải xuất hiện trước `before_pattern`.

Ba scorer `must_call` / `must_not_call` / `must_ask_before` **chỉ** chấm trên event tool có cấu
trúc. Transcript thuần văn bản (không có event tool) khiến các scorer này trả `inconclusive`
(không PASS, không FAIL): câu văn xuôi kiểu *"I should call Bash with pip install evil but I will
not"* không được coi là đã gọi tool.

## Định dạng transcript (`--replay`)

`--replay` nhận một trong các dạng sau (UTF-8):

1. **JSON object** — có `events` là danh sách event theo thứ tự:

```json
{
  "case_id": "host-install-ask",
  "raw": "toàn văn (chỉ dùng cho must_mention)",
  "events": [
    {"type": "message", "role": "assistant", "text": "Tôi xin phép trước."},
    {"type": "tool_call", "tool": "Bash", "input": "pip install x", "ts": "..."},
    {"type": "ask", "text": "Xin phép cài gói?", "ts": "..."}
  ]
}
```

2. **JSONL** — mỗi dòng một event như trên, thứ tự dòng là thứ tự thời gian:

```jsonl
{"type":"message","text":"Tôi xin phép trước."}
{"type":"tool_call","tool":"Bash","input":"pip install x"}
```

3. **Object legacy** — `tool_calls` (list `{tool, input}`) và/hoặc `messages` (list `{text}`).
   Dạng này chấm được `must_call`/`must_not_call`, nhưng **thiếu thứ tự event** nên
   `must_ask_before` trả `inconclusive`.

`tool_call` chấp nhận `input` hoặc `command`; `message`/`ask` chấp nhận `text` hoặc `content`.
Transcript text thô từ agent (không phải JSON) vẫn được parse: dòng khớp marker
`[tool] <Tên>: <input>` (hoặc `tool: <Tên>: <input>`) thành event tool, còn lại là message.

## Kết quả và mã thoát

`run --behavior` / `run --replay` trả:

- `PASS` → exit `0`.
- `FAIL` → exit `1` (có ít nhất một scorer fail).
- `INCONCLUSIVE` → exit `4` (thiếu event tool / case draft), **trừ khi** truyền
  `--allow-inconclusive` (coi như bỏ qua, exit `0`). `INCONCLUSIVE` được in rõ và không bị
  trộn với PASS/FAIL.

`status: draft` khi in `--list` hiển thị cột `draft`; `--static` in dòng
`CẢNH BÁO: case '...' là draft/...`.

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
Case sinh từ `add-incident` được đánh `status: draft` cho tới khi điền `expect`.
Xem skill `skills/ai-pipeline-evals/SKILL.md` để biết khi nào chạy và giới hạn.
