---
name: ai-pipeline-monitor
description: Giám sát sau release theo control band (1σ/2σ/3σ) + drift PSI/KS cho mọi dự án AI. Dùng khi model đã release cần theo dõi chất lượng/tín hiệu vận hành, phát hiện drift và mở incident -> intent tái nhập pipeline; khi người hỏi "sau release theo dõi thế nào / khi nào báo động / drift tính ra sao".
---

# Giám sát sau release (control band + drift + incident -> intent)

Sau khi release, chất lượng không cố định. Giám sát để phát hiện lệch **sớm** và đưa sự cố **trở lại pipeline** thành một intent (không tự sửa). Đây là lớp bổ sung cho `supervisor.py` (giám sát worker/budget) và knowledge graph (nhớ vì sao).

## Khi nào bật
- Ngay sau khi có release (phase release), khi đã có lưu lượng thật hoặc mẫu gán nhãn định kỳ.
- Bật khi có: (a) tín hiệu có nhãn (accuracy/EM trên mẫu gán nhãn), (b) tín hiệu không nhãn (confidence, reject/fallback, latency p95, 4xx/5xx), (c) dữ liệu vào (drift PSI/KS).
- Không bật khi chưa có baseline đáng tin — đặt baseline rồi duyệt ở G2 trước.

## 3 lớp
1. Dữ liệu: `runs/<id>/monitor/metrics.jsonl` append-only, mỗi dòng `{ts, metric, value, labels{model_version, slice}, n}`.
2. Policy: `runs/<id>/monitor_policy.json` (copy `templates/monitor_policy.template.json`, duyệt ở G2; schema `schemas/monitor_policy.schema.json`).
3. Check: `scripts/monitor.py check` tính band theo hướng, ghi state/actions, mở incident khi >= diagnose.

## Lệnh
```sh
python scripts/monitor.py record runs/<id> --metric accuracy --value 0.93 --n 120 \
  --label model_version=rec-v1 --label slice=hw
python scripts/monitor.py ingest runs/<id> metrics_dump.csv
python scripts/monitor.py drift --ref ref.json --cur cur.json
python scripts/monitor.py check runs/<id> --now 2026-10-04T12:00:00Z
python scripts/monitor.py check runs/<id> --json
python scripts/monitor.py list runs/<id>
python scripts/monitor.py dismiss runs/<id> incident:monitor-... --reason "báo động giả do mùa vụ"
python scripts/monitor.py resolve runs/<id> incident:monitor-...
```

## Đặt baseline & ngưỡng theo loại dự án
Baseline = giai đoạn đầu sau release đã biết là ổn (`window` cuốn) hoặc giá trị đã duyệt (`value`, thường đi với `bands_abs`).

- **OCR/KIE**: `em_field`/`accuracy` theo field (higher_is_better), `window` 10-20, `min_n` >= 50; drift trên đặc trưng ảnh (độ sáng, kích thước, tỷ lệ khung) và phân phối confidence. PSI 0.1/0.2/0.25.
- **Phân loại (tabular/text)**: `accuracy`/`f1` (higher), `reject_rate`/`fallback_rate` (lower); drift trên phân phối feature và tỷ lệ lớp. Chú ý mùa vụ và lệch phân bố lớp.
- **Forecast/time-series**: `mape`/`mae` (lower, theo horizon/slice); drift trên residual và feature ngoại sinh. Cảnh báo theo mùa, không tính xu hướng dài là drift.
- **RAG/LLM**: `accuracy`/`em` trên bộ gán nhãn nhỏ định kỳ; tín hiệu không nhãn: `reject_rate`, tỷ lệ trả lời thiếu nguồn, latency p95, `error_rate_5xx`; drift trên phân phối câu hỏi/chủ đề (PSI phân loại).
- **min_n**: cửa sổ nhỏ KHÔNG kích hoạt tầng cao — dưới `min_n` chỉ còn `warn`.
- **stale_after_minutes**: đặt theo nhịp đổ dữ liệu (vd. 1440 cho batch ngày, 30 cho realtime); dữ liệu ngừng chảy là tín hiệu `stale` riêng.

## Incident -> intent -> vòng sửa
- Tầng `warn`: chỉ log + theo dõi.
- Tầng `diagnose`/`propose`/`stale`: `check` ghi `monitor/incidents/<id>.md` (vấn đề, metric, bằng chứng, phạm vi ảnh hưởng, đề xuất, người cần duyệt), thêm Incident vào knowledge graph (`evidenced_by` -> artifact metrics) và ghi sổ `type=error`.
- Người duyệt (G2) quyết định: rollback model_version, hay mở một vòng sửa (dùng `ai-pipeline-diagnose` để chẩn đoán rồi `ai-pipeline-planning`). `monitor.py` KHÔNG tự rollback, không tự chạy.
- Đóng incident: `dismiss` (bắt buộc lý do, append-only) khi báo động giả; `resolve` khi đã xử lý.

## Bẫy
- **Drift không nhãn ≠ lỗi**: input đổi chưa chắc chất lượng giảm (có thể đúng với dữ liệu mới). Chỉ mở vòng sửa khi có tín hiệu chất lượng hoặc đã lấy mẫu gán nhãn.
- **Mùa vụ/chu kỳ**: so baseline cùng kỳ (cùng giờ/ngày/tuần), tránh tính đỉnh bình thường là bất thường.
- **Mẫu nhỏ**: dưới `min_n` không kết luận; độ bất định lớn.
- **Quá nhạy -> báo động giả**: bắt đầu 1σ/2σ/3σ nhưng nới `warn` nếu nhiễu; theo dõi tỷ lệ `dismiss`.
- **σ=0**: baseline hằng số — mọi lệch bị coi là bất thường (band `σ=0`); nên xem lại baseline.
- **Thiếu dữ liệu**: metric không chảy là tín hiệu `stale` riêng, không bỏ qua.

## Chạy định kỳ (in sẵn, KHÔNG tự cài lịch)
Cron (Linux/macOS):
```cron
# mỗi giờ: check và ghi log; exit >= 2 nghĩa là cần người
0 * * * * cd /duong/dan/repo && python scripts/monitor.py check runs/<id> >> runs/<id>/monitor/cron.log 2>&1
```
Windows Task Scheduler:
```powershell
schtasks /Create /SC HOURLY /TN "ai-pipeline-monitor-<id>" /TR "python C:\duong\dan\repo\scripts\monitor.py check runs\<id>" /F
```
`monitor.py` không gọi mạng, không tự rollback; chỉ ghi file trong run dir.

## Định dạng `check --json` (ổn định cho supervisor/coordinator)
```json
{
  "run_dir": "...", "policy_version": "v1", "now": "...",
  "metrics": [
    {"metric": "accuracy", "direction": "higher_is_better", "value": 0.8, "n": 120,
     "ts": "...", "labels": {"model_version": "rec-v1", "slice": "hw"},
     "tier": "propose", "band": "15.00\u03c3", "action": "...", "reason": "...",
     "center": 0.95, "sigma": 0.01, "measure": 15.0}
  ],
  "summary": {"ok": 0, "no_data": 0, "warn": 0, "diagnose": 0, "propose": 1, "stale": 0,
              "max_tier": "propose", "incidents_created": ["incident:monitor-..."]},
  "exit_level": 2
}
```
Exit code: 0 ổn · 1 warn · 2 >= diagnose/propose/stale (cần người) · 3 lỗi policy/dữ liệu (fail-closed).

## Giới hạn
- Không thay thế giám sát worker/budget (`supervisor.py`); đây là giám sát **chất lượng/tín hiệu sau release**.
- Band σ dùng độ lệch chuẩn mẫu của cửa sổ baseline; không phải kiểm định thống kê đầy đủ (dùng KS/PSI cho drift phân phối).
- Chưa tự nối vào `project_status.py` (coordinator nối sau, đọc `check --json`).
- Đánh giá có nhãn cần mẫu gán nhãn định kỳ; không có thì chỉ còn tín hiệu không nhãn + drift.
