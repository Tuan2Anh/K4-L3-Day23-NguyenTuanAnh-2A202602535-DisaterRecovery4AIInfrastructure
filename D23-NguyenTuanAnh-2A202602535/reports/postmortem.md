# Postmortem — DR Drill Lab 23

Theo đúng template §4 "Sau Failover: Blameless Postmortem". Blameless: câu hỏi là
"hệ thống/process nào cho phép chuyện này", không phải "ai làm sai".

## 1. Timeline (mọi dòng có evidence path:line thật)

| ISO time | Sự kiện | Evidence |
|---|---|---|
| `2026-10-09T04:05:38` | outage bắt đầu (Region A bị netblock) | `chaos/chaos-events.jsonl:3` |
| `2026-10-09T04:05:38` | user đầu tiên bị ảnh hưởng (request fail) | `reports/drill-2-withdr.jsonl:25` |
| `2026-10-09T04:05:57` | health check alert (chuyển sang UNHEALTHY) | `reports/health-events.jsonl:2` |
| `2026-10-09T04:05:58` | operator confirm cutover (bắt đầu failover) | `reports/runbook-run.jsonl:2` |
| `2026-10-09T04:06:00` | resolved (request đầu tiên OK từ Region B) | `reports/drill-2-withdr.jsonl:36` |

## 2. RTO/RPO đo được vs mục tiêu — gap ở bước nào?

- RTO mục tiêu: 300s · đo được: `22.2s` · gap: `277.8s` (đạt mục tiêu, nhanh hơn 277.8s)
- RPO mục tiêu: 300s · đo được: `6.0s` (`3` doc bị mất) · gap: `294.0s` (đạt mục tiêu, mất dữ liệu trong phạm vi 6 giây)
- **Bước tốn nhiều giây nhất:** `Health check detection floor` (chiếm 15.0s lý thuyết, 19.2s thực tế trên tổng 22.2s RTO) — vì cần thăm dò với ngưỡng an toàn `threshold=3` và chu kỳ `interval=5s` để phòng chống flapping.

## 3. Root cause (5 Whys)

1. **Tại sao user gặp lỗi?** Vì Region A bị cô lập mạng (netblock/SIGSTOP) không thể phản hồi request infer.
2. **Tại sao hệ thống không phục hồi tức thì?** Vì cần cơ chế xác thực outage để tránh failover giả khi mạng chập chờn.
3. **Tại sao cần chờ 15s để phát hiện?** Vì hệ thống áp dụng ngưỡng anti-flap `interval=5s` × `threshold=3` lần fail liên tiếp trước khi alert.
4. **Tại sao Region B không phục vụ ngay khi có alert?** Vì Region B là mô hình Active-Passive với warm pool, cần kéo snapshot mới nhất và kích hoạt pool sang full.
5. **Tại sao dữ liệu bị mất 3 documents?** Vì chu kỳ snapshot replication chạy định kỳ 30s (`--every 30`), những documents được ghi nhận sau snapshot gần nhất trước khi Region A sập sẽ chưa kịp đồng bộ sang storage replica.

## 4. Action items (có owner + deadline)

| # | Action | Owner | Deadline | Giảm RTO/RPO bao nhiêu giây |
|---|---|---|---|---|
| 1 | Thử nghiệm giảm health check interval từ 5s xuống 3s kèm threshold=3 | SRE Team | 2026-10-20 | Giảm RTO khoảng 6.0s |
| 2 | Giảm chu kỳ replication từ 30s xuống 10s bằng CDC stream thay vì polling snapshot | Data Infra Team | 2026-10-25 | Giảm RPO tối đa 20.0s |

## 5. Ba câu hỏi bắt buộc trả lời

1. **`interval × threshold` của bạn là bao nhiêu giây? Nó chiếm bao nhiêu % RTO?**
   - Giá trị là: $5\text{s} \times 3 = 15\text{s}$.
   - Nó chiếm: $\frac{15.0}{22.2} \times 100\% \approx 67.57\%$ tổng thời gian RTO đo được.
2. **Nếu hạ interval xuống 1s, RTO giảm mấy giây — và bạn trả giá gì (§4 flapping)?**
   - RTO lý thuyết sẽ giảm từ $15\text{s}$ xuống $1\text{s} \times 3 = 3\text{s}$ (giảm $12\text{s}$).
   - Cái giá phải trả: Tăng nguy cơ **flapping** nghiêm trọng. Khi mạng chỉ bị trễ tạm thời (jitter hoặc micro-outage vài giây), hệ thống sẽ nhầm là sự cố vùng và kích hoạt failover liên tục qua lại giữa 2 region, gây nghẽn DNS TTL, lãng phí tài nguyên và làm gián đoạn dịch vụ của người dùng nặng hơn cả việc không failover.
3. **Nếu outage kéo dài 6 giờ và region chính mất dữ liệu vĩnh viễn, `docs_lost` của bạn có nghĩa gì với khách hàng?**
   - `docs_lost` ($3$ documents trong drill) đại diện cho các giao dịch, câu hỏi hoặc dữ liệu nạp của khách hàng trong khoảng thời gian $6\text{s}$ trước sự cố đã biến mất hoàn toàn và không thể khôi phục tự động.
   - Với khách hàng, điều này đồng nghĩa với việc mất hóa đơn/ticket gần nhất, đòi hỏi đội ngũ vận hành phải thông báo cho khách hàng hoặc chạy cơ chế re-ingest/reconciliation từ log của upstream client để bù đắp dữ liệu bị khuyết thiếu.
