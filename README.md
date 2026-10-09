# ai-pipeline

Workflow đa agent cho dự án AI: **file spec → kế hoạch → thực thi song song**, chạy được trên **Claude Code** và **Codex**, điều phối bằng **[Orca](https://github.com/stablyai/orca)**. `ai-pipeline` là CLI cài workflow này vào **bất kỳ project nào** (Linux, macOS, Windows) mà không đụng tới file của bạn.

Nguyên tắc: data trước, không làm phức tạp hoá, ước lượng giới hạn khả thi trước khi tối ưu, mọi thí nghiệm có `report.md` + `report.html`, người duyệt ở 3 cổng G1/G2/G3, mọi việc chạy trong sandbox container.

## Quy trình sản xuất pipeline AI (tổng quan)

Đầu vào là một file spec. Coordinator (Claude Code hoặc Codex) chạy các phase; mỗi phase là một đợt worker song song trên Orca. Ba cổng **G1/G2/G3** luôn cần người duyệt.

```mermaid
flowchart TD
    S["spec.md"] --> P0["Phase 0 Intake<br/>validate_spec, hỏi câu còn thiếu"]
    P0 --> G1{{"G1 Người làm rõ<br/>nền tảng, input, output, dataset, mục tiêu"}}
    G1 --> P1["Phase 1 Phân tích song song<br/>data-analyst, requirements-analyst, researcher"]
    P1 --> F0["Ước lượng giới hạn khả thi C0<br/>ceiling.json"]
    F0 -->|"mục tiêu vượt ceiling"| ASK1["Hỏi người: hạ mục tiêu / thêm data thật / đổi bài toán"]
    ASK1 --> P2
    F0 --> P2["Phase 2 Lập kế hoạch<br/>2 model-proposer + critic debate, architect chốt"]
    P2 --> PB["plan.json + playbook.json<br/>sơ đồ xử lý thành phần yếu"]
    PB --> G2{{"G2 Người duyệt plan, cách triển khai, playbook"}}
    G2 --> G3{{"G3 Thông tin GPU server, CUDA, framework<br/>trước khi huấn luyện"}}
    G3 --> B0["B0 Probe rẻ trên data thật, ceiling C1"]
    B0 --> P3["Phase 3 Build module song song<br/>mỗi module 1 worker, 1 git worktree"]
    P3 --> P4["Phase 4 Tích hợp<br/>ghép pipeline, test end-to-end, phân cụm lỗi"]
    P4 --> P5{"Phase 5 Tối ưu<br/>đạt mục tiêu?"}
    P5 -->|"chưa, còn trong ceiling"| DIAG["Chẩn đoán thành phần yếu<br/>sửa theo nhánh playbook đã duyệt"]
    DIAG --> P3
    P5 -->|"đạt, hoặc hết hiệu quả cận biên"| P6["Phase 6 Release<br/>đóng gói, báo cáo cuối"]
    P6 --> PROD(["Chạy production"])
```

Quy tắc xuyên suốt: data trước, mỗi dataset/model gắn version, sau **mỗi vòng thí nghiệm** phải có `report.md` + `report.html` rồi mới sang vòng sau, mọi việc chạy trong sandbox container.

## Phát triển một model AI đến production

Mỗi module AI (ví dụ detector, OCR, phân loại, retrieval) đi qua cùng một vòng đời do skill `ai-pipeline-module-dev` hướng dẫn. Điểm quan trọng: đánh giá trên **data thật có nhãn (≥ 50 mẫu)**, và chỉ chạm tập test khóa một lần ở bước tích hợp.

```mermaid
flowchart TD
    A["Hiểu bài toán và data<br/>phân bố train vs test có lệch không?"] --> B["Version hóa dataset và split<br/>split theo trang/nhóm/thời gian, chống rò rỉ"]
    B --> C["Baseline rẻ + learning curve<br/>ước lượng trần khả thi"]
    C --> D["Huấn luyện trong container GPU<br/>sau G3, giới hạn VRAM, có khóa dùng chung"]
    D --> E["Đánh giá trên val thật<br/>metric theo từng field/lát cắt, khoảng tin cậy"]
    E --> R["Báo cáo vòng: report.md + report.html<br/>sổ thí nghiệm, kể cả kết quả âm"]
    R --> F{"Đạt mục tiêu và còn trong ceiling?"}
    F -->|"chưa"| W["Phân tích lỗi trên tập test<br/>weakness-diagnostician"]
    W --> H["Chọn nhánh hành động đã duyệt<br/>DATA / MODEL / STRUCTURE / OBJECTIVE / NOISE"]
    H --> D
    F -->|"đạt"| G["Đóng băng model + recipe + ngưỡng<br/>khóa nhãn test bằng seal"]
    G --> I["Tích hợp end-to-end<br/>chấm test khóa đúng một lần"]
    I --> J{"Test đạt mục tiêu?"}
    J -->|"không"| W
    J -->|"có"| K["Export và đóng gói<br/>ONNX, container có version, requirements.lock"]
    K --> L["Dịch vụ suy luận<br/>vd. LitServe cho model + FastAPI cho API nghiệp vụ"]
    L --> M["Kiểm thử service<br/>parity với model gốc, độ trễ, tải, lỗi 4xx/5xx"]
    M --> N(["Release + báo cáo cuối"])
```

## Chẩn đoán thành phần yếu (trước khi sửa)

Không sửa theo cảm tính. Khi một thành phần dưới mục tiêu, agent `weakness-diagnostician` chạy thí nghiệm phân biệt nguyên nhân, rồi chọn nhánh hành động trong playbook đã được duyệt ở G2.

```mermaid
flowchart TD
    W["Thành phần dưới mục tiêu"] --> O["Oracle và hồ sơ lỗi<br/>lỗi nằm ở đâu: input, model hay hậu xử lý?"]
    O --> LC["Learning curve<br/>thêm data có cải thiện không?"]
    O --> BR["Đọc mù + adjudicate mẫu lỗi<br/>nhãn có mơ hồ không?"]
    O --> AB["Ablation đổi một yếu tố<br/>độ phân giải, ngữ cảnh, quy mô model"]
    LC --> V
    BR --> V
    AB --> V{"Nguyên nhân chính"}
    V -->|"DATA"| D1["Thu thêm data thật, bổ sung lát cắt thiếu<br/>data sinh chỉ dùng khi ablation chứng minh có ích"]
    V -->|"MODEL"| D2["Tăng năng lực, quy mô, độ phân giải, ngữ cảnh"]
    V -->|"STRUCTURE"| D3["Tách cấu trúc hoặc thêm module phụ trợ"]
    V -->|"OBJECTIVE"| D4["Hỏi người: metric, ngưỡng hoặc spec lệch"]
    V -->|"NOISE"| D5["Làm sạch nhãn, chấp nhận trần do nhãn mơ hồ"]
    D1 --> NEXT["Vòng mới có giả thuyết và mức cải thiện dự đoán"]
    D2 --> NEXT
    D3 --> NEXT
    D4 --> NEXT
    D5 --> NEXT
    NEXT --> STOP{"Còn hiệu quả cận biên?"}
    STOP -->|"hết"| END(["Dừng, báo người dùng: mục tiêu vượt khả năng"])
    STOP -->|"còn"| W
```

Ví dụ thực tế: bài timesheet OCR có mục tiêu 99%/field vượt ceiling; thí nghiệm bác bỏ giả thuyết "sinh data tổng hợp giúp được" và "thêm module tách ô giúp được", nên pipeline dừng vòng lặp thay vì chạy mãi.

## Cách coordinator điều phối worker với Orca

```mermaid
flowchart TD
    PL["plan.json: DAG task + deps"] --> CR["plan_to_orca --create<br/>tạo Run và Task"]
    CR --> SR["plan_to_orca --start-ready<br/>worker-start cho task đủ điều kiện"]
    SR --> CK["orca check --wait<br/>worker_done, question, escalation"]
    CK --> Q{"Loại tin nhắn"}
    Q -->|"question"| AQ["Trả lời, hoặc hỏi người nếu là gate<br/>orca reply"]
    Q -->|"worker_done"| RV["Review độc lập: phạm vi file, test, diff<br/>acceptance có đạt?"]
    Q -->|"escalation"| ESC["Xử lý chặn: duyệt gói, quyết định giao thức"]
    AQ --> CK
    ESC --> CK
    RV -->|"succeeded và đạt"| DN["settle: ghi done.json"]
    RV -->|"failed hoặc không đạt"| RT["Giữ task, retry hoặc sửa<br/>dependents vẫn bị chặn"]
    RT --> SR
    DN --> BC["Đóng và xóa nhánh worker đã review<br/>merge hoặc tag archive trước khi xóa"]
    BC --> SR
```

Mỗi task build chạy trong git worktree riêng; worker không bao giờ sửa chung một checkout. Agent nào khả dụng do `scripts/agent_roster.py` kiểm tra, người dùng chọn orchestrator và worker trước khi chạy song song.

## Người giám sát (human-on-the-loop)

```mermaid
flowchart LR
    GATES["G1, G2, G3 bắt buộc<br/>người duyệt trực tiếp"] --> AUTO["Ngoài gate: pipeline tự chạy<br/>trong autonomy_policy.json"]
    AUTO --> CAP{"Trong giới hạn?<br/>số task, mode theo phase, cap"}
    CAP -->|"có"| RUN["Worker chạy, ghi audit.jsonl"]
    CAP -->|"không"| BLOCK["Từ chối khởi động,<br/>cần người nâng cap"]
    RUN --> SUP["supervisor.py chỉ đọc<br/>+ orca_snapshot.py"]
    SUP --> ALERT["Cảnh báo: worker failed, im lặng,<br/>gần ngưỡng cap"]
    ALERT --> HUMAN["Người xem và can thiệp"]
```

## Tri thức dự án

```mermaid
flowchart LR
    CODE["Code dự án"] -->|"Graphify, --code-only"| CG["graphify-out/<br/>graph code"]
    DOCS["Spec, decisions, báo cáo, sổ thí nghiệm"] --> NB["runs/&lt;id&gt;/notebook/"]
    NB --> KG["runs/&lt;id&gt;/knowledge/<br/>đồ thị tri thức có kiểu, append-only<br/>decision, experiment, artifact, incident"]
    CG --> OV["vault/ cho Obsidian<br/>docs + sổ + code graph"]
    NB --> OV
    KG -->|"truy vấn neighbors, path, explain, timeline"| AG["Agent truy nguyên<br/>quyết định và bằng chứng"]
    CG -->|"graphify query"| AG
```

## Vòng tối ưu tự động sau baseline

Sau khi có baseline, pipeline **tự động** lặp cải tiến tới độ chính xác cao nhất đạt được một cách trung thực. Mọi vòng đo trên **val thật hoặc OOF**; tập test khóa chỉ chạm một lần ở cuối (lặp trên test sẽ làm con số cuối bị thổi phồng).

```mermaid
flowchart TD
    BASE["Baseline trên val/OOF"] --> NEXT["optimize.py next<br/>chọn thành phần yếu theo khoảng cách x trọng số"]
    NEXT --> STOPQ{"Điều kiện dừng?"}
    STOPQ -->|"mọi field đạt target"| FINAL["Test khóa một lần<br/>rồi release"]
    STOPQ -->|"target vượt ceiling<br/>hoặc nhãn mơ hồ"| ASK["Dừng, hỏi người"]
    STOPQ -->|"hết hiệu quả cận biên,<br/>hết vòng, hết ngân sách"| REPORT["Dừng, báo cáo kết quả tốt nhất"]
    STOPQ -->|"tiếp tục"| DIAG["Chẩn đoán thành phần yếu<br/>DATA / MODEL / STRUCTURE / NOISE / OBJECTIVE"]
    DIAG --> ACT{"Hành động"}
    ACT -->|"MODEL"| RET["Retrain biến thể<br/>độ phân giải, quy mô, ngữ cảnh"]
    ACT -->|"hậu xử lý được"| POST["Thêm luật hậu xử lý<br/>chuẩn hóa, ràng buộc định dạng"]
    ACT -->|"DATA / STRUCTURE<br/>cần dữ liệu ngoài"| RES["Researcher tìm dataset công khai<br/>giấy phép, độ lệch miền"]
    RES --> GATE{{"Gate: người duyệt nguồn"}}
    GATE --> PROV["Tải trong container<br/>data_provenance use-check"]
    PROV --> AUX["Train module phụ<br/>vd. bản phân loại chữ số viết tay"]
    AUX --> ROUTE["Định tuyến theo độ tin cậy<br/>ngưỡng hiệu chỉnh trên val"]
    RET --> EVAL["Đánh giá val/OOF + report"]
    POST --> EVAL
    ROUTE --> EVAL
    EVAL --> REC["optimize.py record<br/>gain có dấu, kiểm hồi quy các field khác"]
    REC -->|"giữ"| NEXT
    REC -->|"bác bỏ: nhánh không thử lại"| NEXT
```

Ví dụ: OCR đọc kém chữ số viết tay → chẩn đoán STRUCTURE → researcher tìm dataset chữ số viết tay → người duyệt nguồn và giấy phép → train bản phân loại chữ số trong container → định tuyến theo độ tin cậy vào các field số → đo lại trên val thật; nếu gain âm hoặc làm tụt field khác thì vòng bị bác bỏ.

| Lệnh | Việc |
|---|---|
| `python scripts/optimize.py init <run>` | tạo policy (target lấy từ spec, nguồn metric khai báo; liệt kê bảng ứng viên để bạn chọn) |
| `python scripts/optimize.py status <run>` | bảng baseline / mới nhất / target / khoảng cách theo field |
| `python scripts/optimize.py next <run> --apply` | quyết định bước kế tiếp và ghi vòng vào `plan.json` (giao dịch, idempotent) |
| `python scripts/optimize.py record <run> --round N --gpu-hours X` | ghi kết quả vòng (gain có dấu, hồi quy, idempotent) |
| `python scripts/data_provenance.py register\|approve\|verify\|use-check` | đăng ký dataset ngoài có truy vết (giấy phép, hash, đánh giá lệch miền) |

## Cưỡng chế, eval và giám sát (vòng kín sau release)

```mermaid
flowchart LR
    CFG["Đổi skill, roles, AGENTS.md, hook"] --> EV["evals.py run --static<br/>CI + pre-commit"]
    EV -->|"xanh"| MERGE["Merge"]
    EV -->|"đỏ"| FIX["Sửa cấu hình"]
    WORK["Agent làm việc"] --> HOOK["pipeline_guard (PreToolUse)<br/>chặn nhãn test, cài gói host,<br/>docker nguy hiểm, ghi ngoài owns"]
    HOOK --> AUD["guard_audit.jsonl"]
    PROD(["Model production"]) --> MON["monitor.py check<br/>band 1σ / 2σ / 3σ + PSI / KS"]
    MON -->|"1σ"| LOG["Ghi log"]
    MON -->|"2σ"| DIA["Chẩn đoán chỉ đọc"]
    MON -->|"3σ"| INC["Incident = intent<br/>đề xuất rollback hoặc vòng sửa"]
    INC --> G1["Người duyệt G1/G2<br/>rồi vào lại pipeline"]
    INC --> CASE["evals.py add-incident<br/>sự cố thành case vĩnh viễn"]
    CASE --> EV
```

| Lớp | Lệnh | Chức năng |
|---|---|---|
| Hook | `python scripts/diagram.py render\|validate\|from-plan\|from-model` | sơ đồ Excalidraw/SVG cho report pipeline và kiến trúc mô hình (skill `ai-pipeline-diagram`) |
| `ai-pipeline hooks install\|status\|uninstall` | merge nhóm hook riêng vào `.claude/settings.json` (không đụng hook của bạn); `python scripts/pipeline_guard.py context write <run_dir>` cấp role/ownership theo task |
| Eval | `python scripts/evals.py run --static` / `--changed` / `--replay <transcript> --case ID` / `add-incident` | kiểm cấu hình agent; chấm hành vi theo event tool; sự cố thành case |
| Giám sát | `python scripts/monitor.py record\|ingest\|check\|drift\|list\|dismiss\|resolve\|reconcile` | band theo hướng tốt/xấu, `min_n`, stale, PSI/KS; incident ghi vào đồ thị tri thức và sổ |

Hook cho Claude Code; Codex không có hook tương đương đã xác minh nên dùng `pipeline_guard.py --check` thủ công hoặc trong CI. Hook là bộ lọc lệnh chứ không phải sandbox: các cách vòng đã biết (lệnh mã hoá base64, biến shell, `python -c` gọi subprocess) được liệt kê trong skill `ai-pipeline-hooks`.

### Những gì quy trình chưa tự động hóa

Để không hiểu nhầm mức hoàn thiện: **CI/CD cho model** (huấn luyện lại, canary, rollback tự động) chưa có, giám sát mới dừng ở phát hiện và mở incident; experiment tracker/model registry chỉ ở dạng sổ thí nghiệm và đồ thị tri thức; behavioral eval chưa chạy hàng loạt trên agent thật; hook chưa được kiểm trong phiên Claude Code tương tác thật; `seal` bảo vệ nhãn test ở mức quy trình, không phải ranh giới bảo mật filesystem (cần mount/ACL ngoài checkout).

## Cài đặt

Yêu cầu: Python ≥ 3.9 và git. Không có phụ thuộc Python nào khác.

```bash
# khuyến nghị: pipx (cô lập, có lệnh ai-pipeline toàn cục)
pipx install git+https://github.com/cuongtran2203/ai-pipeline

# hoặc pip
pip install git+https://github.com/cuongtran2203/ai-pipeline

# hoặc không cài, chạy thẳng từ bản clone
git clone https://github.com/cuongtran2203/ai-pipeline && cd ai-pipeline
python -m ai_pipeline --help
```

Để chạy worker song song cần thêm: [Orca](https://github.com/stablyai/orca) và ít nhất một trong `claude` (Claude Code) / `codex`. Docker là tuỳ chọn (sandbox). `ai-pipeline doctor` kiểm tra tất cả.

## Bắt đầu nhanh

```bash
cd my-project            # project có sẵn hoặc thư mục mới
git init                 # nếu chưa là git repo (worker song song dùng git worktree)
ai-pipeline init         # cài workflow
ai-pipeline doctor       # kiểm tra môi trường
cp templates/spec.template.md spec.md   # điền spec (nền tảng, input/output, dataset, mục tiêu)
```

Rồi mở thư mục bằng Claude Code hoặc Codex và nói:

> Chạy ai-pipeline với spec `spec.md`

Agent sẽ chạy skill `ai-pipeline`: validate spec → hỏi bạn ở G1/G2/G3 → tạo Run trên Orca → chạy worker song song. Kết quả nằm ở `runs/<run_id>/`. Hỏi *"dự án đang ở bước nào"* để chạy skill `ai-pipeline-status`.

## `ai-pipeline init` KHÔNG ghi đè hay sửa file của bạn

| Đã có trong project | Hành vi |
|---|---|
| `AGENTS.md`, `CLAUDE.md` | **Không sửa, không xoá.** Luật của workflow nằm ở `.ai-pipeline/AGENTS.md`. Nếu chưa có `AGENTS.md`/`CLAUDE.md` thì tạo file chỉ chứa một dòng trỏ tới đó. Muốn agent đọc luật trong `AGENTS.md` sẵn có: thêm dòng `@.ai-pipeline/AGENTS.md`, hoặc `ai-pipeline init --link` (thêm một khối có marker, gỡ được bằng `uninstall`). Các skill vẫn chạy được độc lập. |
| Skill khác trong `.claude/skills`, `.agents/skills`, `skills/` | **Giữ nguyên.** Chỉ thêm skill `ai-pipeline*` mới. Nếu bạn đã có skill trùng tên, cả thư mục skill đó được bỏ qua (`skip-dir`). |
| File trùng tên trong `scripts/`, `roles/`, `templates/`, `schemas/` | **Bỏ qua** file của bạn (báo `skip`). Dùng `--force` mới ghi đè, và luôn tạo `<file>.bak`. |
| `.gitignore` | Không sửa; chỉ gợi ý dòng cần thêm. `--gitignore` để tự thêm. |

Tính chất: chạy lại `init` nhiều lần cho kết quả giống nhau (idempotent); `--dry-run` chỉ in việc sẽ làm; mọi file đã cài được ghi hash trong `.ai-pipeline.json`.

## Lệnh

| Lệnh | Việc |
|---|---|
| `ai-pipeline init [path] [--agent claude\|codex\|both] [--link] [--gitignore] [--force] [--dry-run] [-v]` | cài workflow vào project |
| `ai-pipeline update [path]` | nâng cấp file framework. File bạn đã sửa được giữ nguyên, bản mới ghi ra `<file>.new` để tự merge |
| `ai-pipeline uninstall [path] [--dry-run]` | gỡ các file đã cài mà bạn chưa sửa; file của bạn và `runs/` không bị đụng |
| `ai-pipeline doctor [path]` | kiểm tra python/git/orca/claude/codex/docker, bản cài, skills mirror |
| `ai-pipeline new-run <tên> <spec.md>` | tạo `runs/<tên>/` từ spec |
| `ai-pipeline status [run_dir]` | phase hiện tại + việc cần làm tiếp |
| `ai-pipeline validate <spec>` | liệt kê câu hỏi còn thiếu cho G1 |
| `ai-pipeline plan plan.json [--dry-run \| --create \| --start-ready]` | DAG → task/worker trên Orca |
| `ai-pipeline report eval.json` | `report.md` (3 phần, tiếng Việt) + `report.html` (gom cụm lỗi) |
| `ai-pipeline hooks install\|status\|uninstall` | cài/gỡ hook cưỡng chế (merge, không đè settings) |
| `ai-pipeline pack list\|status\|install\|build\|uninstall [graphify\|obsidian] [--yes]` | cài/build pack tuỳ chọn (xem mục Pack) |
| `ai-pipeline run <script> [args]` | chạy bất kỳ `scripts/<script>.py`; alias: `notebook kg agents diagram cleanup autonomy supervisor seal vault sync-skills` |

Các lệnh `status/validate/plan/...` chỉ gọi script tương ứng trong `scripts/` của project, nên chạy ở thư mục project (hoặc `--path`).

## Pack tuỳ chọn: Graphify và Obsidian

Cài và build qua CLI, **luôn hỏi trước** (`--yes` để đồng ý không hỏi; không có terminal mà thiếu `--yes` thì từ chối, không cài gì):

```bash
ai-pipeline pack status                      # pack nào đã cài / đã build
ai-pipeline pack install graphify            # venv riêng .venv-graphify, graphifyy==0.9.74, lock, .graphifyignore
ai-pipeline pack build graphify              # graph code của cả dự án -> graphify-out/ (--code-only)
ai-pipeline pack install obsidian            # winget / brew --cask / flatpak (hoặc hướng dẫn tải)
ai-pipeline pack build obsidian              # vault/: tài liệu + sổ thí nghiệm + code graph -> mở bằng Obsidian
ai-pipeline init --with graphify,obsidian --yes   # cài cùng lúc với init
ai-pipeline pack uninstall graphify          # xoá venv do CLI tạo
```

- **Graphify**: gói PyPI `graphifyy` (hai chữ y) cài vào venv trong project, không đụng Python hệ thống; cần Python ≥ 3.10 (CLI tự tìm). Build chỉ phân tích code cục bộ (`--code-only`) và gỡ mọi API key LLM khỏi môi trường, nên không có gì rời máy. Không bao giờ chạy `graphify claude|codex install` (lệnh đó sửa `AGENTS.md`/`CLAUDE.md`). `.graphifyignore` có sẵn của bạn được giữ nguyên.
- **Obsidian**: là ứng dụng desktop; CLI chỉ phát hiện/cài bằng trình quản lý gói của hệ điều hành khi bạn đồng ý, rồi dựng `vault/` bằng `scripts/obsidian_vault.py` (stdlib, cục bộ). Thêm `.venv-graphify/`, `graphify-out/`, `vault/` vào `.gitignore`.
- Cần mạng tới pypi.org khi cài Graphify. Mọi pack được ghi vào `.ai-pipeline.json`.

## Thêm skill riêng cho bài toán

Tạo thư mục `skills/<tên-skill>/SKILL.md` rồi `ai-pipeline sync-skills` (chép sang `.claude/skills` và `.agents/skills`; chỉ các skill có trong `skills/` bị thay, skill khác của bạn không bị đụng). `ai-pipeline update` không xoá skill của bạn. Vai trò worker nằm ở `roles/*.md`, template ở `templates/`.

## Những gì được cài

```
.ai-pipeline/AGENTS.md   luật chung của workflow (hoặc đọc qua AGENTS.md của bạn)
skills/                  23 skill: ai-pipeline, -intake, -analysis, -planning, -module-dev, -integration,
                         -report, -orca, -status, -feasibility, -notebook, -graph, -agents, -sandbox,
                         -diagnose, -knowledge, -autonomy, -hooks, -evals, -monitor, -optimize, -research, -diagram
.claude/skills/  .agents/skills/   bản sao cho Claude Code / Codex (theo --agent)
roles/                   prompt role dùng chung cho 2 runtime
templates/  schemas/     spec, report, playbook, autonomy policy; plan/eval/kg schema
scripts/                 validate_spec, plan_to_orca, render_report, project_status, notebook, kg, seal,
                         autonomy, supervisor, ... (chỉ cần Python stdlib)
.ai-pipeline.json        manifest (version + hash file đã cài)
```

## Ghi chú vận hành

- Orca: `orca status --json` phải chạy được. Worker `pi`/`antigravity` chỉ chạy ổn với worktree `current`; `command-code` chạy headless (`scripts/run_headless_agent.py`). Xem skill `ai-pipeline-agents`.
- Sandbox: mọi huấn luyện/benchmark/cài gói chạy trong container, không cài gói lên host khi chưa được duyệt (skill `ai-pipeline-sandbox`).
- `seal.py` bảo vệ nhãn test ở mức quy trình, **không phải** ranh giới bảo mật filesystem.
- Windows: dùng Git Bash hoặc PowerShell; mọi script là Python thuần, không cần symlink.

## Phát triển repo này

```bash
python -m unittest discover -s tests     # 450+ test, stdlib
python scripts/evals.py run --static     # eval cấu hình agent (CI)
python scripts/sync_skills.py --check    # skills/ là nguồn chuẩn, .claude/ và .agents/ là bản sao
pip install .                            # build wheel (đóng gói skills/roles/... vào ai_pipeline/payload)
```

`skills/`, `roles/`, `templates/`, `schemas/`, `scripts/`, `AGENTS.md` ở thư mục gốc là nguồn chuẩn; `setup.py` đóng gói chúng vào wheel khi build.
