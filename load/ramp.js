// k6 load test. Open model: requests arrive at a fixed RATE whether or not the app keeps up, like real users.
//   phase "warm"   (0-70 s): ramps 2 -> RATE req/s. The autoscaler is allowed to catch up here. Not judged.
//   phase "steady" (70-110 s): holds RATE req/s. Judged: p95 latency and error rate must be good.
// Usage: k6 run -e BASE=http://127.0.0.1:8080 -e RATE=10 -e MS=30 load/ramp.js
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE || 'http://127.0.0.1:8080';
const RATE = parseInt(__ENV.RATE || '10');
const MS = __ENV.MS || '30';
const WARM = parseInt(__ENV.WARM || '70');
const STEADY = parseInt(__ENV.STEADY || '40');
const P95 = __ENV.P95 || '500';

export const options = {
  scenarios: {
    warm: {
      executor: 'ramping-arrival-rate', startRate: 2, timeUnit: '1s', preAllocatedVUs: 20, maxVUs: 150,
      stages: [{ target: RATE, duration: WARM + 's' }], exec: 'warm', gracefulStop: '5s',
    },
    steady: {
      executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: STEADY + 's', startTime: WARM + 's',
      preAllocatedVUs: 20, maxVUs: 150, exec: 'steady', gracefulStop: '15s',
    },
  },
  thresholds: {
    'http_req_duration{phase:steady}': ['p(95)<' + P95],
    'http_req_failed{phase:steady}': ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'p(90)', 'p(95)', 'max'],
};

function hit(phase) {
  const res = http.get(BASE + '/work?ms=' + MS, { tags: { phase: phase }, timeout: '20s' });
  check(res, { 'status 200': (r) => r.status === 200 });
}
export function warm() { hit('warm'); }
export function steady() { hit('steady'); }
