#!/usr/bin/env python3
"""Reads a `k6 --summary-export` file and judges the STEADY phase.
Usage: k6summary.py SUMMARY.json [P95_LIMIT_MS=500]
Prints key=value lines (p95_ms, avg_ms, fail_rate, rps, requests, verdict) and exits 0 if the steady phase is good,
1 if it is not (p95 over the limit or 1% or more of requests failed), 2 if the file cannot be read."""
import json
import sys


def main():
    path = sys.argv[1]
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 500.0
    try:
        with open(path, encoding="utf-8") as fh:
            metrics = json.load(fh)["metrics"]
        dur = metrics["http_req_duration{phase:steady}"]
        failed = metrics["http_req_failed{phase:steady}"]
        total = metrics["http_reqs"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"cannot read the k6 summary: {exc}")
        return 2
    p95, avg, rate = dur["p(95)"], dur["avg"], failed.get("value", 0.0)
    good = p95 < limit and rate < 0.01
    print(f"p95_ms={p95:.0f}\navg_ms={avg:.0f}\nfail_rate={rate:.3f}\nrps={total['rate']:.1f}\nrequests={int(total['count'])}")
    print(f"verdict={'good' if good else 'bad'}")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
