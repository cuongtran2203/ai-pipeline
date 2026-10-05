---
name: ai-pipeline-optimize
description: Mandatory post-baseline optimize loop controller (scripts/optimize.py) - policy, STOP rules, test discipline, hypothesis discipline and per-verdict actions for any AI pipeline.
---

# Vong toi uu bat buoc sau baseline (optimize loop)

Vong toi uu la buoc **BAT BUOC** sau baseline, khong phai tuy chon. Chu trinh day du:

| Buoc | Lenh | Ghi chu |
|---|---|---|
| 0. Policy | `python scripts/optimize.py init <run_dir>` | Tu `templates/optimize_policy.template.json`; nguoi duyet o **G2** cung playbook. Tu dien `default_target` tu spec neu ro; liet ke bang ung vien de chon `metrics_source` |
| 0b. Chon bang | `python scripts/optimize.py init <run_dir> --metrics-table <index\|regex> --field-col item --value-col auto` | Ghi `metrics_source` vao policy (regex khop nhieu bang thi dung tat ca) |
| 1. Trang thai | `python scripts/optimize.py status <run_dir>` | Bang tieng Viet moi field/component: baseline, moi nhat, target, khoang cach, xu huong, so vong, verdict |
| 2. Quyet dinh | `python scripts/optimize.py next <run_dir> [--json] [--apply]` | STOP (5 dieu kien) hoac GO; `--apply` ghi vong vao plan.json (idempotent), roi chay `python scripts/plan_to_orca.py --create/--start-ready` nhu thuong |
| 3. Chay task | Orca workers | DIAG truoc (neu thieu diagnosis), roi hanh dong theo nhanh, ket vong bang evaluate + report |
| 4. Ghi nhan | `python scripts/optimize.py record <run_dir> --round N` | Doc eval moi, ghi `optimize/rounds.jsonl` (append-only), cap nhat state, KG, so |

## Dieu kien STOP (coordinator ap dung, ghi vao rounds/state)

1. **Thanh cong**: moi target dat → task cuoi `I-final` (test khoa **mot lan** qua `python scripts/seal.py grant`) roi release.
2. **Hoi nguoi**: `ceiling.json` (C1/C2) thap hon target, hoac verdict OBJECTIVE/NOISE can nguoi (doi metric/spec hoac ha target).
3. **Het hieu qua can bien**: cai thien < epsilon trong `patience` vong lien tiep (epsilon mac dinh = 1/2 sai so chuan bo danh gia).
4. **Het max_rounds** (mac dinh 3; nguoi mo rong).
5. **Het budget** (gpu_hours / so task).

## Ky luat test (khong thuong luong)

- Lap cai thien va phan tich loi **chi tren val that hoac OOF** (cross-fit). `error_analysis_split` trong policy chi nhan `val|oof`; dat `test` bi tu choi.
- Neu spec noi "chay test" o baseline thi hieu la **baseline tren val/OOF**; test khoa chay cuoi o `I-final`.
- `optimize.py` khong bao gio tao task phan tich loi tren split test; bang khai split/test trong eval.json bi bo qua khi doc lich su.

## Nguon metric khai bao (khong doan bang)

- Component/field chi lay tu **(a)** `eval_contract` cua eval.json (schema v2: ban danh gia co hop dong) hoac **(b)** khai bao tuong minh `metrics_source` trong `optimize_policy.json` (`table_title_regex` hoac `table_index`, `field_column`, `value_column`, `field_regex`/`exclude_regex` tuy chon).
- Khong co khai bao ma eval.json mo ho (nhieu bang, hang ablation nhu `RPA +P +augment ... hieu -3.9d CI95 ...`): `status`/`next` **LOI fail-closed**, khong tu doan. `init` quet eval.json moi nhat va **liet ke bang ung vien** (ten, so hang, gia tri mau, danh dau bang giong ablation) de nguoi chon: `init --metrics-table <index|regex> --field-col X --value-col Y`.
- Nhieu hang cung (nhom, ten) (RC/RA/RCA...) gop mot lan: chung baseline thi lay baseline; hang tong `ALL` chi de bao cao, khong bao gio la component sua. Cot CI/sai so chuan neu co dung lam epsilon.

## Target: thieu thi STOP-hoi-nguoi (khong GO)

- Target hieu dung = `targets.<field>.target`, roi `default_target`. `init` tu dien `default_target` khi spec.md/plan viet ro (vd. `moi field >= 99%` → 0.99 + ghi nguon); khong ro thi de trong, can nguoi duyet.
- Field chua co target: `next` tra **STOP-hoi-nguoi** liet ke field thieu + goi y tu spec, khong sinh task.

## ID task ngan, on dinh

`R<NN>-<slug toi da 24 ky tu, bo dau>-<hash 6>-<action>` (vd. `R01-hw-start-time-a96699-diag`); cung dau vao → cung id (`--apply` idempotent). Worker ghi `diagnosis.json` voi `component` dung bang field key trong eval.

Moi task co `id` dang `R<NN>-<slug24>-<hash6>-<action>` (vd. `R01-hw-start-time-a96699-diag`), deps tuan tu dung thu tu, role/agent theo `agents.json` (nhom code), owns tach biet, acceptance do duoc **voi `predicted_gain` bat buoc** (so du doan tang metric, do tren val/OOF). `record` so predicted vs measured, hieu chinh ti le measured/predicted cho lan sau, nhanh bi bac bo (verdict `bo`) khong sinh lai.

## Hanh dong theo verdict (nhanh da duyet o G2)

- **Thieu diagnosis** → sinh task `DIAG-<comp>` (role weakness-diagnostician) **TRUOC**, chua lam gi khac.
- **DATA** → research du lieu ngoai (neu can) → collect/label (ask nguoi) → retrain. Synth chi khi ablation tren val that cho thay khop.
- **MODEL** → retrain bien the (do phan giai / quy mo / ngu canh / schedule), 1 thay doi moi vong.
- **STRUCTURE** → research dataset cong khai (neu can, **cong duyet nguon**: chi tai sau khi nguoi duyet) → build module phu → tich hop + dinh tuyen theo do tin cay (nguong hieu chinh tren val) → retrain.
- **POSTPROCESS** (cum loi sua duoc bang luat) → task hau xu ly.
- **OBJECTIVE/NOISE** → dung, hoi nguoi.

## Vi du: OCR kem chu so viet tay

1. Diagnosis STRUCTURE (oracle: crop vung chu so roi phan loai tot hon han end-to-end).
2. Research dataset chu so viet tay cong khai (ten skill research do task khac viet; day chi tham chieu ten `ai-pipeline-research`).
3. **Kiem tra lech phan bo** bang thi nghiem nho (train thu → do tren val that) truoc khi tin du lieu ngoai.
4. Train classifier chu so (co version) → dinh tuyen vao field do theo do tin cay, nguong hieu chinh tren val.
5. Do lai e2e tren val/OOF → `record`; dat thi giu, khong thi bo nhanh.

## Bay hay gap

- **Lap tren test**: lam test mat gia tri khoa; chi val/OOF cho toi `I-final`.
- **Cai thien nam trong nhieu**: duoi epsilon (~1/2 SE) la nhieu do luong, khong phai tien bo → plateau STOP.
- **Sua nhieu thu mot luc**: khong biet cai gi gay tac dung; 1 thay doi moi vong.
- **Du lieu sinh/ngoai lam te di**: luon ablation tren val that; te thi bo, khong co them.
- **Thu lai nhanh da bi bac bo**: `record` da danh dau; `next` se khong sinh lai.
- **Vong truoc chua record**: `--apply` tu choi; chay `record --round N` truoc.
