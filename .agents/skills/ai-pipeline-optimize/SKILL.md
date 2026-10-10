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
| 2. Quyet dinh | `python scripts/optimize.py next <run_dir> [--json] [--apply]` | STOP (5 dieu kien) hoac GO; `--apply` ghi vong vao plan.json **trong mot khoa giao dich** (doc trong khoa, idempotent), roi chay `python scripts/plan_to_orca.py --create/--start-ready` nhu thuong |
| 3. Chay task | Orca workers | DIAG truoc (neu thieu diagnosis), roi hanh dong theo nhanh, ket vong bang evaluate + report |
| 4. Ghi nhan | `python scripts/optimize.py record <run_dir> --round N --gpu-hours X` | Gain **co dau** + kiem hoi quy; ghi `optimize/rounds.jsonl` (append-only, idempotent) + state **duoi mot khoa**; KG/so sau, loi thi `reconcile` |
| 4b. Reconcile | `python scripts/optimize.py reconcile <run_dir>` | Ghi lai phan KG/notebook thieu (co `kg_pending` trong state) |

## Dieu kien STOP (coordinator ap dung, ghi vao rounds/state)

1. **Thanh cong**: moi target dat → task cuoi `I-final` (test khoa **mot lan** qua `python scripts/seal.py grant`) roi release.
2. **Hoi nguoi**: `ceiling.json` (C1/C2) thap hon target, hoac verdict OBJECTIVE/NOISE can nguoi (doi metric/spec hoac ha target).
3. **Het hieu qua can bien**: cai thien < epsilon trong `patience` vong lien tiep (epsilon mac dinh = 1/2 sai so chuan bo danh gia).
4. **Het max_rounds** (mac dinh 3; nguoi mo rong).
5. **Het budget** (gpu_hours that do duoc / so task).

## Ky luat test (khong thuong luong)

- Lap cai thien va phan tich loi **chi tren val that hoac OOF** (cross-fit). `error_analysis_split` trong policy chi nhan `val|oof`; dat `test` bi tu choi.
- **Split fail-closed**: moi hang metric phai co split `val|oof` (row.split, table.split, `metrics_source.split_value`, hoac `eval_contract.split`); hang thieu split hoac split `train`/rong bi **tu choi** kem thong bao ro (khong am tham cho qua). Hang khai split/test bi bo qua (ky luat test).
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

`R<NN>-<slug toi da 24 ky tu, bo dau>-<hash 6>-<action>` (vd. `R01-hw-start-time-a96699-diag`); cung dau vao → cung id (`--apply` idempotent, giao dich duoi mot khoa). Worker ghi `diagnosis.json` voi `component` dung bang field key trong eval.

Moi task co `predicted_gain` bat buoc (so du doan tang metric, do tren val/OOF). `record` so predicted vs measured (gain co dau), hieu chinh ti le measured/predicted cho lan sau (chi vong duong), nhanh bi bac bo (verdict `bac-bo`) khong sinh lai.

## Gain co dau + kiem hoi quy (record)

- `record` tinh gain **CO DAU** theo huong tot cua metric (`higher`/`lower_is_better`); chi verdict **`giu`** khi gain duong cua field muc tieu >= epsilon (epsilon theo sai so chuan/CI neu co). Suy giam (vd. 54/60 → 48/60) → **`bac-bo`**, khong bao gio "giu" nho tri tuyet doi.
- **Kiem hoi quy**: field khac giam qua epsilon (hoac `max_regression` neu policy dat) → `bac-bo` kem ly do + field bi hai; nhanh bi bac bo khong sinh lai.
- Hieu chuan measured/predicted chi dung vong gain duong; vong am ghi rieng (`calibration_negative`), khong bao gio lam tang du doan.
- Cai thien trong nhieu (< epsilon) khong tinh tien bo → dem vao plateau nhu cu.

## Record idempotent + crash-safe

- `record` chay **duoi mot khoa theo run**: kiem round chua ghi + digest eval chua co, round khop `pending_round`, bo artifact day du (`eval.json` + `report.md` + `report.html` cung thu muc round, ten round dung) → moi ghi `rounds.jsonl` + `state.json`. Goi hai lan → lan hai tra "da ghi" (exit 0, khong ghi them).
- **3 tai lieu round** (`require_round_docs`): policy co `"require_round_docs": true` (template run moi mac dinh true; policy cu thieu khoa = false, hanh vi cu; khoa phai la bool, sai kieu bi tu choi) thi `record` tu choi neu round thieu `data_report.html`/`method_report.html`/`results_report.html` hoac con placeholder `{{...}}` (tai dung `round_docs.py check`; thong bao neu ro file nao). Tao bang `python scripts/round_docs.py init <thu muc round>` roi dien het. Task `R<NN>-eval` do `next --apply` sinh se liet ke 3 file trong `outputs` + `acceptance`; `project_status.py` coi round chua du 3 file la thieu va goi y lenh.
- Ghi KG/notebook sau; neu loi dat co `kg_pending` trong state + in lenh `optimize.py reconcile <run_dir>` (khong canh bao roi bo qua). Crash giua cac buoc: lan `record` sau tu hoan tat state nho digest.

## Cong duyet nguon du lieu ngoai (gate nguoi that)

- DATA/STRUCTURE can du lieu ngoai sinh chuoi: `R<NN>-research` (researcher **de xuat, KHONG tai**, dang ky card qua `data_provenance.py register`) → **GATE** `kind: gate` "duyet nguon du lieu ngoai" → build/aux/integrate.
- Cong do **coordinator hoi NGUOI THAT bang ask** (co che gate G1/G2/G3 cua `plan_to_orca`), ghi `decisions.md`, them id gate vao `done.json`. Task train/aux co `deps` gom gate va acceptance bat buoc ``python scripts/data_provenance.py use-check <run_dir> <DATASET_ID>`` thanh cong (da register+approve, hash khop) truoc khi train.
- `policy.approved_sources` la danh sach **ID registry** da duyet tu truoc (**so khop chinh xac**, khong substring); thieu → luon sinh gate.
- **Gioi han that**: CLI khong xac thuc danh tinh nguoi duyet (chuoi `person:<ten>` ai cung go duoc); cong nguoi that la co che gate cua coordinator, khong phai lenh `data_provenance approve`.

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
5. Do lai e2e tren val/OOF → `record --gpu-hours X` (so gio GPU that; hoac worker viet `usage.json` trong thu muc round; thieu ma co cap → tu choi); dat thi giu, khong thi bac-bo nhanh.

## Bay hay gap

- **Lap tren test**: lam test mat gia tri khoa; chi val/OOF cho toi `I-final`.
- **Cai thien nam trong nhieu**: duoi epsilon (~1/2 SE) la nhieu do luong, khong phai tien bo → plateau STOP.
- **Sua nhieu thu mot luc**: khong biet cai gi gay tac dung; 1 thay doi moi vong.
- **Du lieu sinh/ngoai lam te di**: luon ablation tren val that; te thi bo, khong co them.
- **Thu lai nhanh da bi bac bo**: `record` da danh dau; `next` se khong sinh lai.
- **Vong truoc chua record**: `--apply` tu choi (giao dich, 2 coordinator cung apply → 1 thang, 1 nhan pending); chay `record --round N --gpu-hours X` truoc.
