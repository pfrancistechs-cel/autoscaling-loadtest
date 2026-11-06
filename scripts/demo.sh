#!/usr/bin/env bash
# Scripted autoscaling tour. Usage: ./scripts/demo.sh [baseline|hpa|keda|gameday]
# Keep the console (http://<EC2-PUBLIC-IP>:8080/console/) open while it runs: you will see pods and queue move.
#   baseline  the SAME load on a fixed single pod, no autoscaler: k6 judges it, and it fails (slow answers)
#   hpa       the same load with the Horizontal Pod Autoscaler: pods go 1 -> several, k6 passes, then pods go back to 1
#   keda      60 jobs land in a queue: workers go 0 -> several, the queue drains, every job is counted once, workers go back to 0
# Each load test takes about 2 minutes (70 s ramp-up, 40 s judged steady phase, then the cool-down).
set -uo pipefail
. "$(dirname "$0")/lib.sh"

FAILURES=0
assert() { # LABEL COMMAND...
  local label="$1"; shift
  if "$@"; then ok "$label"; else fail "$label"; FAILURES=$((FAILURES + 1)); fi
}
finish() { # NAME
  if [ "$FAILURES" -eq 0 ]; then printf '\n\033[1;32mPASS\033[0m  %s\n\n' "$1"; else printf '\n\033[1;31mFAIL\033[0m  %s\n\n' "$1"; exit 1; fi
}
gt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a > b) }'; }
ge() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a >= b) }'; }
eq() { [ "$1" = "$2" ]; }
kv() { sed -n "s/^$1=//p" <<<"$2"; }                    # kv KEY "k1=v1\nk2=v2"

need_stack() {
  api_ready || die "The cluster is not running. Run ./scripts/up.sh first."
  command -v k6 >/dev/null || die "k6 is missing. Run ./scripts/setup.sh first."
  web_answers || die "The web app does not answer on $APP_URL. Run ./scripts/up.sh"
}

# Start every test from a fresh single pod, so leftovers of the previous test cannot colour the result.
fresh_web() {
  kd scale deploy/web --replicas=1 >/dev/null
  kd rollout restart deploy/web >/dev/null
  wait_until 120 web_at_most 1 && wait_until 120 web_at_least 1 && wait_until 60 web_answers
  sleep 5
}

load_test() { # NAME   runs k6, prints the steady-phase numbers, sets K6_RC and SUMMARY
  local name="$1" out
  SUMMARY="$RESULTS_DIR/k6-$name.json"
  say "k6: ramp to $LOAD_RATE req/s of $LOAD_MS ms CPU each over ${WARM}s, then judge ${STEADY}s (p95 must stay under ${P95_LIMIT} ms)"
  run_k6 "$SUMMARY" >"$RESULTS_DIR/k6-$name.txt" 2>&1; K6_RC=$?
  out="$(python3 "$ROOT/scripts/k6summary.py" "$SUMMARY" "$P95_LIMIT")"
  SUM_P95="$(kv p95_ms "$out")"; SUM_FAIL="$(kv fail_rate "$out")"; SUM_RPS="$(kv rps "$out")"; SUM_VERDICT="$(kv verdict "$out")"
  say "steady phase: p95 ${SUM_P95:-?} ms, failed ${SUM_FAIL:-?}, $SUM_RPS req/s overall"
}

scenario_baseline() {
  log "SCENARIO baseline: the load, one fixed pod, no autoscaler"
  kd delete hpa web --ignore-not-found >/dev/null
  fresh_web
  start_sampler "$RESULTS_DIR/timeline-baseline.txt"
  load_test baseline
  stop_sampler
  assert "the app stayed at exactly 1 pod"               eq "$(max_pods "$RESULTS_DIR/timeline-baseline.txt")" 1
  assert "k6 judged the steady phase as BAD (too slow or failing)" eq "${SUM_VERDICT:-none}" bad
  post_result "baseline (no autoscaler)" "$([ "$FAILURES" -eq 0 ] && echo true || echo false)" "1 pod, p95 ${SUM_P95:-?} ms: k6 says bad, as expected"
  apply_file hpa.yaml                                  # put the autoscaler back for the next test
  finish baseline
}

scenario_hpa() {
  log "SCENARIO hpa: the same load with the Horizontal Pod Autoscaler (CPU target 70%, 1 to 6 pods)"
  apply_file hpa.yaml
  fresh_web
  start_sampler "$RESULTS_DIR/timeline-hpa.txt"
  load_test hpa
  stop_sampler
  local peak; peak="$(max_pods "$RESULTS_DIR/timeline-hpa.txt")"
  say "the autoscaler went from 1 pod up to $peak pods"
  assert "the HPA added pods (at least 3 at the peak)"   ge "$peak" 3
  assert "k6 judged the steady phase as GOOD (p95 under ${P95_LIMIT} ms, under 1% failed)" eq "${SUM_VERDICT:-none}" good
  say "load has stopped; waiting for the HPA to remove the extra pods (about 1-2 minutes)"
  assert "the pods went back down to 1"                  wait_until 180 web_at_most 1
  post_result "hpa (CPU autoscaling)" "$([ "$FAILURES" -eq 0 ] && echo true || echo false)" "1 -> $peak -> 1 pods, p95 ${SUM_P95:-?} ms, k6 good"
  finish hpa
}

scenario_keda() {
  log "SCENARIO keda: a queue of 60 jobs, workers scale from ZERO"
  apply_file scaledobject.yaml
  assert "workers start at zero pods (nothing to do)"    wait_until 120 workers_at_most 0
  kd exec deploy/redis -- redis-cli del jobs processed >/dev/null
  say "pushing 60 jobs (2 seconds of work each)"
  kd exec deploy/redis -- sh -c 'i=0; while [ $i -lt 60 ]; do echo "RPUSH jobs job-$i"; i=$((i + 1)); done | redis-cli' >/dev/null
  assert "KEDA starts workers (at least 2)"              wait_until 60 workers_at_least 2
  say "workers now: $(ready_of workers), queue: $(queue_len)"
  assert "the queue drains to zero"                      wait_until 150 queue_empty
  assert "every job was processed exactly once (60)"     wait_until 30 processed_is 60
  say "queue is empty; waiting for the workers to go back to zero (cooldown)"
  assert "the workers scaled back to ZERO pods"          wait_until 120 workers_at_most 0
  post_result "keda (queue autoscaling)" "$([ "$FAILURES" -eq 0 ] && echo true || echo false)" "0 -> workers -> 0, 60 jobs processed once each"
  finish keda
}

need_stack
mkdir -p "$RESULTS_DIR"
case "${1:-gameday}" in
  baseline) scenario_baseline ;;
  hpa)      scenario_hpa ;;
  keda)     scenario_keda ;;
  gameday)  scenario_baseline; scenario_hpa; scenario_keda
            printf '\033[1;32mALL SCENARIOS PASSED\033[0m\n' ;;
  *) echo "Usage: $0 [baseline|hpa|keda|gameday]" >&2; exit 2 ;;
esac
