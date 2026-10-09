# Runbook 1 trang — Region chính down

Runbook phải chạy được lúc 3h sáng bởi người KHÔNG viết nó. Mỗi bước: lệnh copy-paste
được + cách biết bước đó xong.

| # | Bước | Lệnh | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `curl -s http://127.0.0.1:8001/readyz` | Trả về 503 hoặc timeout/connection refused | on-call engineer |
| 2 | Mở incident + bấm giờ RTO | `python3 -c "import time, json; print(json.dumps({'event':'incident_opened','ts':time.time()}))"` | Timestamp ghi nhận và bắt đầu đồng hồ tính RTO | on-call incident commander |
| 3 | Restore state ở region phụ | `python3 state/snapshot.py get --region b --backend fs` | Thư mục `state/region-b/` có `vectors.sqlite` và `model.bin` | on-call engineer |
| 4 | Scale pool warm→full | `echo full > state/region-b/pool_state && curl -sf http://127.0.0.1:8002/readyz` | `/readyz` của region b trả về HTTP 200 (`ready: true`) | on-call engineer |
| 5 | DNS/LB cutover | `printf b > edge/active_region` | `curl -s localhost:8080/edge/state` trả về `"active_region":"b"` | on-call engineer |
| 6 | Verify golden signals | `for i in {1..10}; do curl -sf localhost:8080/v1/infer > /dev/null && echo "OK" || echo "FAIL"; done` | 10/10 request trả về OK, p95 latency < 50ms, error rate = 0% | on-call engineer |
| 7 | Đo RTO + postmortem | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | Output JSON có `"rto_verdict":"PASS"` và `rto_measured_s <= 300` | SRE lead / incident commander |

**Rollback (failover ngược):** 
- **Điều kiện trả traffic về Region A:** 
  1. Nguyên nhân outage tại Region A đã được cô lập và khắc phục hoàn toàn.
  2. Region A đã chạy replication ngược từ Region B để đồng bộ dữ liệu mới nhất (`state/region-b` -> `state/region-a`), đảm bảo không mất dữ liệu sinh ra trong lúc failover.
  3. Endpoint `/readyz` của Region A trả về 200 ổn định liên tục trong ít nhất 15 phút.
- **Ai quyết định:** Incident Commander phối hợp cùng Tech Lead / SRE Lead ký duyệt xác nhận trước khi chuyển `edge/active_region` về `a`.
