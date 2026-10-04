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
| `label_protection` | Read/Edit/Write/Bash dung duong dan nhan test (`eval_manifest.json`, thu muc nhan, `*.sealed.json`, `seal_audit.jsonl`, `recipe_lock.json`); tru `integrator`/`evaluator` |
| `host_install` | Bash cai goi tren host (`pip/npm/yarn/apt/conda/brew/cargo/go install`); cho phep trong `docker exec/run`, `ssh <host> docker ...`, hoac goi trong `allowed_packages` |
| `dangerous_docker` | `--privileged`, `--net/--network host`, `docker system prune`, `docker rm -f` container khong phai `aipipeline-<run>-*` |
| `ownership` | Edit/Write ngoai duong dan `owns` cua task (thieu file ownership -> bo qua) |
| `bugfix_tests` | Task bugfix sua file trong `tests/` |
| `destructive` | `rm -rf` ngoai thu muc tam/worktree, `git push --force/-f`, `git reset --hard` tren nhanh chinh |

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
4. Role lay tu `AI_PIPELINE_ROLE` hoac file task context
   (`<run>/task_context.json`), KHONG tu tham so tu khai trong tool call
   (hook bo qua truong role trong event).

## Doc audit

Moi quyet dinh (allow/deny + ly do + rule id) append vao
`runs/<id>/guard_audit.jsonl` (hoac `.ai-pipeline/guard_audit.jsonl`):

```sh
tail -5 runs/<id>/guard_audit.jsonl
python scripts/pipeline_guard.py --check --tool Bash --input '{"command":"pip install x"}'
```

## Cai/go hook (Claude Code)

```sh
ai-pipeline hooks install --path <du-an>    # merge, giu hook cua ban
ai-pipeline hooks status --path <du-an>
ai-pipeline hooks uninstall --path <du-an>  # go sach muc cua ai-pipeline
```

Khoi mau: `templates/hooks.settings.template.json`.

## Codex (khong co hook tuong duong da xac minh)

Codex khong co co che hook PreToolUse tuong duong da duoc xac minh, nen
KHONG bia co che tu dong. Chi co:

- Quy tac van ban trong skill nay + `ai-pipeline-sandbox` (agent tu tuan thu).
- Chay thu cong/CI: `python scripts/pipeline_guard.py --check ...` (exit 2 = vi pham).

## Gioi han (hook KHONG phai sandbox that)

- Bash co the vong qua bang cach ma hoa/giau lenh: `base64 -d`,
  bien moi truong (`$X=pip; $X install`), noi chuoi, `eval`, chay qua
  `sh -c` long nhau, hoac goi truc tiep binary (`/usr/bin/pip` van khop
  pattern `pip install`, nhung `python -c "import pip..."` thi khong).
- Tool khong thuoc Read/Edit/Write/Bash (vd. NotebookEdit, MCP tool ghi
  file) hien khong bi kiem tra.
- Hook het timeout thi Claude Code CHO QUA (khong chan) — giu guard nhanh,
  dung coi la lop bao ve duy nhat cho lenh pha hoai.
- Tat hook bang cach sua `.claude/settings.json` la co the (nguoi dung
  toan quyen may minh); audit giup phat hien sau su co, khong ngan truoc.
- Moi su co vuot hook thanh mot eval case vinh vien cho cau hinh agent
  (theo huong AI-native SDLC): them ca test vao `tests/test_hooks.py`.
