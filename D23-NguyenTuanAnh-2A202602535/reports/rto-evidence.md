# RTO/RPO Evidence — Lab 23

Quy tắc duy nhất: mỗi con số ở đây phải trỏ được về **một dòng log thật**
(`đường/dẫn.jsonl:số_dòng`). `pytest tests/test_rto_evidence.py` sẽ mở từng file ra kiểm tra.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | `2026-10-09T03:57:49` | chaos kill | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | `+0.5s` | dòng `ok:false` đầu tiên sau t_outage | `reports/drill-1-nodr.jsonl:18` |
| Request thành công sau đó | không có | không có dòng `ok:true` nào sau t_outage | `reports/measure-drill-1.json` |
| RTO | `NO_RECOVERY` | `tools/measure_rto.py` | `reports/measure-drill-1.json` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---|---|---|
| t_outage (mốc 0) | 0 | `action:kill` | `chaos/chaos-events.jsonl:3` |
| User thấy lỗi đầu tiên | 0.0s | dòng `ok:false` đầu | `reports/drill-2-withdr.jsonl:25` |
| Health check phát hiện | 19.2s | `to:UNHEALTHY, region:a` | `reports/health-events.jsonl:2` |
| Snapshot restore xong | 20.4s | `step:2_restore_snapshot` | `reports/failover-events.jsonl:10` |
| Region phụ ready | 20.4s | `step:4_wait_ready` | `reports/failover-events.jsonl:12` |
| DNS cutover | 20.4s | `step:5_dns_cutover` | `reports/failover-events.jsonl:13` |
| **RTO đo được** | 22.2s | dòng `ok:true` đầu sau lỗi | `reports/drill-2-withdr.jsonl:36` |

| Chỉ số | Đo được | Mục tiêu (slide §1) | Verdict |
|---|---|---|---|
| RTO — Inference API | `22.2s` | 300s (5 phút) | PASS |
| RPO — Vector DB | `6.0s` / `3` doc | 300s (5 phút) | PASS |

## 3. RTO của tôi gồm những gì (bắt buộc — đây là phần chấm điểm hiểu bài)

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---|---|---|
| Health-check detect floor | 15.0s | `interval_s × threshold` trong `reports/health-events.jsonl:2` | Giảm interval hoặc threshold (chấp nhận rủi ro flapping) |
| Snapshot restore | 0.0s | 2_restore → 3_scale | Dùng ổ SSD NVMe nhanh hơn, incremental sync |
| GPU pool warm-up | 0.02s | `waited_s` ở `4_wait_ready` trong `reports/failover-events.jsonl:12` | Giữ pool ở warm state có sẵn mô hình trên VRAM |
| DNS/LB TTL cache | 1.8s | t_recovered − t_cutover (22.2s - 20.4s) | Giảm DNS/edge TTL (tăng tải query DNS) |
