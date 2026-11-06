#!/bin/sh
# A queue worker: take a job from the Redis list "jobs", "work" on it, count it. KEDA starts and stops these.
# Runs in redis:7-alpine (which has redis-cli) from a ConfigMap.
# On SIGTERM (KEDA scaling down) finish the job in hand, count it, then exit: a job must never be taken and then lost.
STOP=0
trap 'STOP=1' TERM INT
HOST="${REDIS_HOST:-redis}"
PORT="${REDIS_PORT:-6379}"
WORK="${WORK_SECONDS:-2}"
while [ "$STOP" = 0 ]; do
  job="$(redis-cli -h "$HOST" -p "$PORT" lpop jobs 2>/dev/null)"
  if [ -n "$job" ]; then
    sleep "$WORK"
    redis-cli -h "$HOST" -p "$PORT" incr processed >/dev/null 2>&1
  else
    sleep 1
  fi
done
