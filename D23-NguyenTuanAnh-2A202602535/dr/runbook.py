"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr import health_checker as hc  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n: int, name: str, **kw):
    """Ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "step": n,
        "name": name,
        **kw
    }
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"RUNBOOK [{n}] {name}: {json.dumps(kw)}")
    return rec


def confirm(auto: bool, msg: str) -> bool:
    """auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    if auto:
        return True
    ans = input(f"{msg} [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """7 bước runbook theo slide."""
    t_start = time.time()

    # Lấy t_outage gần nhất nếu có
    t_outage = None
    chaos_file = pathlib.Path("chaos/chaos-events.jsonl")
    if chaos_file.exists():
        for line in chaos_file.read_text().splitlines():
            if line.strip():
                try:
                    d = json.loads(line)
                    if d.get("action") == "kill" and d.get("region") == primary:
                        t_outage = d.get("ts")
                except Exception:
                    pass

    # 1 xac_nhan_outage — chờ alert từ health_checker (chống cutover trước khi alert)
    health_file = pathlib.Path("reports/health-events.jsonl")
    detected = False
    start_wait = time.time()
    # Chờ health checker ghi nhận UNHEALTHY (tối đa 25s kể từ start_wait)
    while time.time() - start_wait < 25.0:
        if health_file.exists():
            lines = health_file.read_text().splitlines()
            for l in reversed(lines):
                if l.strip():
                    try:
                        ev = json.loads(l)
                        if (ev.get("event") == "state_change" and
                            ev.get("to") == "UNHEALTHY" and
                            ev.get("region") == primary and
                            (t_outage is None or ev.get("ts") >= t_outage)):
                            detected = True
                            break
                    except Exception:
                        pass
            if detected:
                break
        time.sleep(0.5)

    # Probe xác nhận primary và target
    ok_p, _ = hc.probe(primary, timeout=1.0)
    ok_t, _ = hc.probe(target, timeout=1.0)
    step(1, "xac_nhan_outage", primary=primary, primary_alive=ok_p,
         target=target, target_alive=ok_t, health_alert_detected=detected)

    # 2 thong_bao_incident — operator biết tin, LUÔN SAU t_outage trong chaos-events
    t_notice = time.time()
    notice_delay_s = round(t_notice - t_outage, 2) if t_outage else None
    step(2, "thong_bao_incident", t_notice=t_notice, t_outage=t_outage, notice_delay_s=notice_delay_s)

    if not confirm(auto, f"Xac nhan thuc hien failover tu {primary} sang {target}?"):
        print("Failover bi huy bo boi operator.")
        return {"ok": False, "cancelled": True}

    # 3 scale_gpu_pool — gọi HÀM failover.failover(...) MỘT LẦN DUY NHẤT
    fo_res = fo.failover(target=target, backend=backend, wait=60.0)
    step(3, "scale_gpu_pool", failover_result=fo_res)
    if not fo_res.get("ok"):
        step(7, "post_incident", ok=False, error="failover_failed")
        return {"ok": False, "error": "failover_failed", "detail": fo_res}

    # 4 verify_state_replica — chỉ ĐỌC kết quả từ dict mà bước 3 trả về + check state
    st = fo.state_of(target)
    step(
        4, "verify_state_replica",
        target=target,
        rpo_seconds=fo_res.get("rpo_seconds"),
        docs_lost=fo_res.get("docs_lost"),
        embed_model_version=fo_res.get("embed_model_version"),
        vector_count=st.get("count"),
        weights=st.get("weights"),
        pool_state=st.get("pool_state")
    )

    # 5 dns_cutover — cũng chỉ đọc lại: kết quả cutover có ok hay không
    active_path = pathlib.Path("edge/active_region")
    active = active_path.read_text().strip() if active_path.exists() else None
    step(5, "dns_cutover", active_region=active, cutover_ok=(active == target))

    # 6 verify_golden_signals — 10 request thật vào region phụ: p95 latency + error rate
    latencies = []
    errors = 0
    for i in range(10):
        t0_req = time.time()
        try:
            r = httpx.get(f"{URL[target]}/v1/infer", params={"q": f"golden signal {i}"}, timeout=2.0)
            if r.status_code == 200:
                latencies.append((time.time() - t0_req) * 1000)
            else:
                errors += 1
        except Exception:
            errors += 1
    latencies.sort()
    p95 = round(latencies[int(len(latencies) * 0.95)] if latencies else 0.0, 1)
    err_rate = round(errors / 10.0, 2)
    step(6, "verify_golden_signals", total_requests=10, p95_latency_ms=p95, error_rate=err_rate)

    # 7 post_incident — elapsed_s + lệnh đo RTO
    elapsed_s = round(time.time() - t_start, 2)
    measure_cmd = "python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300"
    step(7, "post_incident", elapsed_s=elapsed_s, measure_cmd=measure_cmd)

    return {"ok": True, "elapsed_s": elapsed_s, "target": target, "fo_res": fo_res}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
