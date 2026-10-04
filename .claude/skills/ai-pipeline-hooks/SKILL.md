---
name: ai-pipeline-hooks
description: Enforced guard hooks for the AI pipeline - when pipeline_guard blocks a tool call, how to request an exception via ask, how to read the audit log, and the known limits (hook is not a real sandbox). Use when a hook denies an action, when installing/removing hooks, or when working in Codex without native hooks.
---

# Hook cuong che ai-pipeline (pipeline_guard)

Quy tac trong skill (`ai-pipeline-sandbox`, seal nhan test) chi la chu~
neu agent tuan thu. Hook `scripts/pipeline_guard.py` (PreToolUse, Claude Code)
cuong che bang may: chan truoc khi tool chay (exit code 2 + stderr).

## Khi nao hook chan

| Rule | Chan khi |
| ---- | -------- |
| `label_protection` | Read/Edit/Write/Bash dung duong dan nhan test (`eval_manifest.json`, thu muc nhan, `*.sealed.json`, `seal_audit.jsonl`, `recipe_lock.json`); KECA tim kiem DE QUY (`rg`, `grep -r/-R`, `egrep -r`, `ag`, `git grep`, `find ... -exec`, `findstr /s`, `dir /s`, `Select-String -Recurse`, `Get-ChildItem -Recurse | ...`) co goc tim kiem bao phu file nhan; tru `integrator`/`evaluator` |
| `host_install` | Bash cai goi tren host (`pip/pipx/npm/yarn/apt/conda/brew/cargo/go/uv/poetry/pdm install|add|download|inject|sync`, `uv pip install`, `easy_install`); CHI tinh tu-lenh that — chuoi trong quote cua lenh khac (`echo`, `git commit -m`, `grep`, heredoc) khong bi chan; payload `-c`/`/c` duoc tach (`; && || |`) va quet tung lenh; cho phep trong `docker exec/run`, `ssh <host> docker ...`, hoac khi MOI goi deu khop CHINH XAC `allowed_packages` theo argv |
| `dangerous_docker` | `--privileged`, `--net/--network host`, `docker system prune`, `docker rm -f` container khong phai `aipipeline-<run>-*` |
| `ownership` | Edit/Write ngoai duong dan `owns` cua task (thieu file ownership -> bo qua) |
| `bugfix_tests` | Task bugfix sua file trong `tests/` |
| `destructive` | `rm -rf` ngoai thu muc tam he thong/worktree (duoc xoa BEN TRONG tmp: `tempfile.gettempdir()`, `/tmp`, `/var/folders`, `%TEMP%`; cam `/`, `~`, `.`, `..`, goc o dia, `*`, duong dan chua `..`, to tien cua cwd), `git push --force/-f`, refspec `+` (`+master`, `+HEAD:master`), `git push --mirror`, `git reset --hard` tren nhanh chinh |

Tat/bat tung rule trong `runs/<id>/guard_policy.json` hoac
`.ai-pipeline/guard_policy.json` (mac dinh BAT het).

## Bi chan thi lam gi

1. Doc ly do trong stderr: co rule id + cach khac phuc.
2. Neu la viec chinh dang (vd. integrator can cham test): hoi coordinator
   bang Orca `ask` (khong tu sua policy/guard de vuot):
   `orca orchestration ask --question "..." ...`
3. Ngoai le hop le do human duyet: them goi vao `allowed_packages`,
   mo rong `owns`, hoac tat rule trong `guard_policy.json` (ghi vao
   `decisions.md`). TUYET DOI khong sua `scripts/pipeline_guard.py`
   de vuot kiem tra.
4. Role CHI tu task trong
   `<run>/task_context.json` (`{"tasks": {<TASK_ID>: {role, owns, bugfix}}}`),
   KHONG tu bien moi truong `AI_PIPELINE_ROLE` (da bo: tin cay yeu, tu khai)
   va KHONG tu tham so tu khai trong tool call (hook bo qua truong role
   trong event). Khong tim duoc task -> role rong (fail-closed: van chan nhan).

## Cap role tu dong (coordinator chay truoc khi start worker)

Hook khong tu biet worker dang lam task nao, nen coordinator cap 1 lan
sau khi plan duoc duyet (G2) va truoc moi dot `worker-start`:

```sh
python scripts/pipeline_guard.py context write runs/<id>   # plan.json -> task_context.json (atomic)
```

`context write` doc `runs/<id>/plan.json` (hoac `artifacts/*/plan.json`),
ghi `<run>/task_context.json`. Khi hook chay, task duoc chon theo:

1. Bien moi truong `AI_PIPELINE_TASK=<TASK_ID>` (uu tien; coordinator truyen
   cho worker luc start, cung voi `AI_PIPELINE_RUN_DIR=<run_dir>`; so khop
   khong phan biet hoa thuong), hoac
2. Ten worktree/thu muc cwd dang `<run_id>-<task_id>` (chu thuong, vi du
   `rr-i1` cho run `rr` task `I1`, `my-run-t2` cho run `my-run` task `T2`;
   `plan_to_orca.py` dat ten worktree `--name <run_id>-<task_id lowercase>`
   nen khop tu dong; so khop khong phan biet hoa thuong, ho tro run_id
   co dau `-`).

## allowed_packages: khop chinh xac theo argv

`allowed_packages` trong policy CHI cho qua khi MOI goi cua MOI tu-lenh
cai dat deu khop CHINH XAC danh sach (chuan hoa `_`/`-`/hoa-thuong;
`Safe_Lib` == `safe-lib==2.0` == `safe_lib[extra]`). Substring KHONG tinh
(`allowed ["safe"]` khong mo `pip install unsafe evil`). Cac nguon khong
kiem duoc luon DENY: `-r/--requirement file`, `-c constraints`, `-e editable`,
URL/`git+`/duong dan/file archive, `poetry install`/`uv sync` (khoa lockfile
khong liet ke goi). Flag nhan gia tri (`--index-url ...`, `-U` rieng le)
duoc bo dung cach khi parse.

## Policy hong: deny nhom ghi, van cho Read

`guard_policy.json` hong/khong doc duoc -> fail-closed: dung policy mac dinh
de chan R1/R3, DENY nhom ghi (`Edit`/`Write`/`Bash`, ke ca `MultiEdit`/
`NotebookEdit`) kem thong diep cau hinh ro (`policy_config`), van cho
`Read` thuong; tat R4/R5. Moi quyet dinh van ghi audit (`policy_corrupt: true`).
Sua/xoa file policy roi chay lai; can ngoai le hoi coordinator qua `ask`.

Kiem tra nhanh: `AI_PIPELINE_TASK=FH AI_PIPELINE_RUN_DIR=runs/<id>
python scripts/pipeline_guard.py --check --tool Read \
--input '{"file_path":"runs/<id>/seal_audit.jsonl"}'`.

## Doc audit

Moi quyet dinh (allow/deny + ly do + rule id) append vao
`runs/<id>/guard_audit.jsonl` (hoac `.ai-pipeline/guard_audit.jsonl`):

```sh
tail -5 runs/<id>/guard_audit.jsonl
python scripts/pipeline_guard.py --check --tool Bash --input '{"command":"pip install x"}'
```

## Cai/go hook (Claude Code)

```sh
ai-pipeline hooks install --path <du-an>    # tao NHOM RIENG matcher du, giu hook cua ban
ai-pipeline hooks status --path <du-an>
ai-pipeline hooks uninstall --path <du-an>  # chi go nhom/handler cua ai-pipeline
```

`install` LUON tao mot nhom `PreToolUse` RIENG voi matcher
`Read|Edit|MultiEdit|Write|NotebookEdit|Bash|Grep|Glob`; KHONG bao gio chen
guard vao nhom cua ban hay sua/de matcher cua ban (loi RV5: guard tung bi gan
vao nhom matcher `Read` nen Bash khong kich hoat guard). `uninstall` chi go
handler/nhom co marker cua ai-pipeline (van don duoc nhom tron cu).

Khoi mau: `templates/hooks.settings.template.json`.

## Codex (khong co hook tuong duong da xac minh)

Codex khong co co che hook PreToolUse tuong duong da duoc xac minh, nen
KHONG bia co che tu dong. Chi co:

- Quy tac van ban trong skill nay + `ai-pipeline-sandbox` (agent tu tuan thu).
- Chay thu cong/CI: `python scripts/pipeline_guard.py --check ...` (exit 2 = vi pham).

## Gioi han (hook KHONG phai sandbox that)

- Bash co the vong qua bang cach ma hoa/giau lenh: `base64 -d`,
  bien moi truong (`$X=pip; $X install`), noi chuoi, `eval`, chay script
  (`sh setup.sh` — noi dung script khong duoc quet), hoac `python -c`
  goi `subprocess` (payload `-c` duoc quet theo tu-lenh, nhung lenh dung
  trong chuoi Python nhu `os.system('pip install x')` thi khong).
  Tim kiem de quy duoc phan giai symlink (realpath tren POSIX; tren
  Windows symlink can quyen tao nen chi kiem chung khi tao duoc).
- Tim kiem de quy duoc CHO QUA khi tim trong thu muc con khong chua file
  nhan, hoac khi da loai nhan bang glob/--exclude khop that
  (`--glob '!*sealed*'`, `--exclude='*.json'`, `--glob '!eval_manifest.json'`
  cho file nhan cu the; pathspec `:!...`). Exclude khong lien quan
  (`--glob '!*.png'`) van bi chan; duong dan nhan that trong lenh khac
  (vd. `cat eval_manifest.json`) van bi chan du co exclude.
- `findstr` khong `/s`, `dir` khong `/s`, grep don file: khong phai tim kiem
  de quy, cho qua binh thuong.
- Tool khong thuoc Read/Edit/Write/Bash (vd. NotebookEdit, MCP tool ghi
  file) hien khong bi kiem tra.
- Hook het timeout thi Claude Code CHO QUA (khong chan) — giu guard nhanh,
  dung coi la lop bao ve duy nhat cho lenh pha hoai.
- Tat hook bang cach sua `.claude/settings.json` la co the (nguoi dung
  toan quyen may minh); audit giup phat hien sau su co, khong ngan truoc.
- Moi su co vuot hook thanh mot eval case vinh vien cho cau hinh agent
  (theo huong AI-native SDLC): them ca test vao `tests/test_hooks.py`.
