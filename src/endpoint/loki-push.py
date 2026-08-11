#!/usr/bin/env python3
"""Forward bumblebee NDJSON to Grafana Cloud Loki.

Entries are timestamped with ingestion-time now() (strictly increasing per
line), NOT the record's scan_time -- this avoids greater_than_max_sample_age
drops while the scan_time stays available in the JSON body. Shipping is paced
to a target COMPRESSED throughput (well under the tenant rate limit) instead
of a blind sleep.

Embedded into installer.sh at deploy time; edit deploy/loki_push.py.
"""
from __future__ import annotations
import argparse, gzip, json, sys, time, urllib.error, urllib.request
from collections import defaultdict

CHUNK_MAX_BYTES = 3_000_000
CHUNK_MAX_LINES = 20_000
TARGET_BYTES_PER_SEC = 4 * 1024 * 1024 / 60   # ~4 MiB/min compressed (under the 5 MiB/min limit)
RETRY_ATTEMPTS = 4
DEFAULT_RETRY_AFTER = 5.0


def post_chunk(url, token, stream_labels, values):
    body = json.dumps({"streams": [{"stream": stream_labels, "values": values}]}, separators=(",", ":")).encode("utf-8")
    body_gz = gzip.compress(body)
    req = urllib.request.Request(url, data=body_gz, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Content-Encoding": "gzip", "User-Agent": "bumblebee-loki-push/0.4"})
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, len(body_gz), None
        except urllib.error.HTTPError as e:
            preview = e.read()[:300].decode("utf-8", errors="replace")
            if e.code == 429 and attempt < RETRY_ATTEMPTS:
                ra = DEFAULT_RETRY_AFTER
                hdr = e.headers.get("Retry-After") if e.headers else None
                if hdr:
                    try: ra = float(hdr)
                    except ValueError: pass
                print(f"loki-push: 429 attempt={attempt} sleep={ra}s: {preview}", file=sys.stderr)
                time.sleep(ra); continue
            return e.code, len(body_gz), preview
        except Exception as e:
            return None, len(body_gz), str(e)
    return 429, len(body_gz), "exhausted retries"


def iter_chunks(lines):
    cur, cur_bytes = [], 0
    for ln in lines:
        b = len(ln.encode("utf-8")) + 28
        if cur and (cur_bytes + b > CHUNK_MAX_BYTES or len(cur) >= CHUNK_MAX_LINES):
            yield cur; cur, cur_bytes = [], 0
        cur.append(ln); cur_bytes += b
    if cur:
        yield cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True); ap.add_argument("--token", required=True)
    ap.add_argument("--profile", required=True); ap.add_argument("--hostname", required=True)
    ap.add_argument("--mode", default=""); ap.add_argument("ndjson_file")
    args = ap.parse_args()

    by_type = defaultdict(list)
    n_lines = 0
    with open(args.ndjson_file, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line: continue
            n_lines += 1
            try: rt = (json.loads(line).get("record_type") or "unknown")
            except json.JSONDecodeError: rt = "unparseable"
            by_type[rt].append(line)
    if not by_type:
        print("loki-push: no records", file=sys.stderr); return 0

    base_ns = time.time_ns(); counter = 0
    total = ok = failed = 0
    for rec_type, lines in by_type.items():
        labels = {"source": "bumblebee", "profile": args.profile, "host": args.hostname, "record_type": rec_type}
        if args.mode:
            labels["mode"] = args.mode
        for chunk in iter_chunks(lines):
            values = []
            for ln in chunk:
                values.append([str(base_ns + counter), ln]); counter += 1
            total += 1
            t0 = time.time()
            status, gz_len, err = post_chunk(args.url, args.token, labels, values)
            if status in (200, 204): ok += 1
            else:
                failed += 1
                print(f"loki-push: chunk failed record_type={rec_type} lines={len(chunk)} status={status} err={err}", file=sys.stderr)
            # pace to target compressed throughput
            min_dt = gz_len / TARGET_BYTES_PER_SEC
            dt = time.time() - t0
            if dt < min_dt: time.sleep(min_dt - dt)
    print(f"loki-push: done lines={n_lines} chunks={total} ok={ok} failed={failed}", file=sys.stderr)
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
