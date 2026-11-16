# Historical Nginx limits and local evaluation

This document records the October Docker-based synchronous implementation.
Its four-slot capacity and latency results do not describe the current EKS queue.
Do not run `scripts.check_stack` or `scripts.check_nginx` as current acceptance
tests; use the [demo runbook](demo-runbook.md) for the current request path.

## Chosen settings

Nginx handles ingress protection; the gateway retains HMAC verification, replay
protection and atomic sandbox admission. Nginx does not select local versus cloud
execution. The gateway remains one process with eight threads and four sandbox
slots, because replay and capacity state are process-local.

| Setting | Value | Reason |
| --- | --- | --- |
| Per-IP arrival rate | 20 requests/s | Bounds repeated parsing/verification attempts while allowing the tested 10 requests/s control traffic. This is an ingress budget, not sustainable sandbox throughput. |
| Immediate burst | 10 excess requests | Allows a short spike; the separate active-request cap bounds the work that can remain in progress. |
| Per-IP and server-wide active requests | 8 each | Matches the default gateway threads and still admits the six-request capacity experiment. A nonempty server name supplies the global counter key. |
| Request body | 256 KiB | Matches the gateway's HTTP envelope limit; decoded scripts are separately capped at 64 KiB. |
| Client header/body inactivity | 5 seconds | Disconnects stalled uploads before forwarding; request buffering remains enabled. |
| Proxy connect / send / read inactivity | 2 / 5 / 45 seconds | Leaves margin for the ten-second Docker dry run, endpoint forwarding and failure reporting. |
| Sender connect / read timeout | 3 / 60 seconds | Gives the local proxy time to return its own structured failure. |
| Endpoint connect / read timeout | 2 / 15 seconds | Allows the endpoint's ten-second execution budget plus response margin. |

Eight per-IP and eight globally intentionally give the same ceiling for this
single-sender demonstration. The global cap also bounds multiple clients together.
This does not guarantee fairness between clients. If gateway threads or sandbox
slots change, tune the caps together and rerun the capacity and overload probes.

These are layered connection/inactivity limits. They do not enforce an absolute
end-to-end deadline or cancel execution already dispatched to another process.
The header timeout, request size cap and active-request ceiling help bound
resource use, but do not constitute complete protection against slow trickle
uploads or distributed denial of service. Cloud-job execution will need its own
measured deadline and result-status protocol; do not blindly enlarge the proxy
timeout or retry uncertain POSTs.

## Reproducing the measurements

Start Docker Desktop, build the image, free the four service ports, activate the
venv, and run:

```sh
python -m scripts.check_stack
```

The runner prints its report directory. It contains `pytest.log`, `ingress.json`,
`summary.json`, `progress.json`, `access.json` and logs for the central server,
gateway, endpoint and sender. Reports are ignored by Git. Do not compare figures
across machines without recording hardware, Docker resources and background load.

The benchmark includes:

- Ten sequential signed `print` scripts using real Docker dry runs and endpoint
  execution. Report the median, nearest-rank p95 (the maximum for only ten samples)
  and sequential throughput. This is a smoke benchmark, not a capacity estimate.
- Twenty malformed envelopes paced at 10 requests/s. HTTP 400 is expected: they
  pass ingress and are rejected by the gateway's parser. These measure ingress
  admission, not authenticated script throughput.
- Eighty malformed envelopes offered at 40 requests/s for two seconds. Record
  every 400/429 outcome; a short burst allowance means acceptance need not equal
  exactly 20 requests in the first second.
- A separate 60-request simultaneous burst, with every outcome accounted for.
- Six simultaneous signed, slow scripts: four delivered and two gateway capacity
  rejections. This distinguishes sandbox capacity from proxy overload.
- Eight complete-header/partial-body connections followed by a ninth request.
  The ninth gets 429 without reaching the gateway. Error-log evidence confirms
  the server-wide connection zone was actually exercised.

The complete runner also tests route restrictions, JSON errors, request IDs,
body-size rejection before upstream processing, stalled clients, tampering,
replay, runaway scripts, endpoint downtime, gateway downtime, and a deliberately
silent test upstream. That controlled upstream produces a real 45-second proxy
504; exactly one upstream attempt proves that the POST was not retried. It
executes no script. Monitoring aggregates observed failures into one central
notification and checks that a second poll does not send them again.

## Monitoring and interpretation

JSON access records contain request ID, status, upstream status/header/response
timings, and rate/concurrency limiter outcomes. A 429 with no upstream proves
rejection at ingress. The limiter variables distinguish rate and concurrency
rejections. A gateway-generated 503 at stage `capacity` has a different meaning.

`python -m scripts.monitor_nginx` polls every ten seconds, aggregates new ingress
408/413/429 and proxy 502/504 failures, and notifies the central server. Gateway
502 responses with received upstream headers are excluded because the gateway
already reports them. Successful reads advance a cursor; notification failures
retain it for retry. Only one monitor should own a cursor. Delivery is at least
once: an acknowledged notification followed by a cursor-write failure can be
repeated. Log rotation must allow the old tail to be consumed before replacement.

Useful references: [connection limits](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html),
[rate limits](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html),
[proxy timeouts and retries](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
and [JSON log escaping](https://nginx.org/en/docs/http/ngx_http_log_module.html#log_format).

## Observed local run

Recorded on 24 October 2026 on the development Mac with Docker Desktop's Linux
engine. All 81 project tests and all 12 live ingress/benchmark checks passed.
These small-sample figures vary with Docker startup and background load.

| Probe | Observed result |
| --- | --- |
| Ten sequential signed scripts | median 538.55 ms; nearest-rank p95 641.56 ms |
| Sequential script throughput | 1.98 requests/s |
| Twenty ingress probes at 10 requests/s | 20 HTTP 400; no ingress rejections |
| Eighty probes at 40 requests/s | 50 HTTP 400; 30 HTTP 429 |
| Sixty simultaneous probes | 8 HTTP 400; 52 HTTP 429 |
| Six simultaneous signed scripts | 4 delivered; 2 capacity-rejected |
| Ninth active connection | HTTP 429; server-wide zone rejection in error log |
| Silent client | disconnected after 5.0 seconds |
| Silent test upstream | JSON 504 after 45.0 seconds; exactly one upstream attempt |

The 20 requests/s rate provides admission headroom above the tested 10 requests/s
control and rejects sustained 40 requests/s overload. The eight-request active
cap bounds concurrent work independently of rate; the resource manager remains
the authority on four local dry runs. These observations justify bounded demo
defaults, not an optimal rate or a production performance claim. Longer runs with
representative script durations and multiple clients are needed before deployment.

Detailed evidence from this run is in `artifacts/nginx-verified/`.
