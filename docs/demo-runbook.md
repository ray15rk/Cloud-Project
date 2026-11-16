# Recording the EKS demo

Use the current macOS/Linux or Windows setup guide first. The gateway, dashboard,
monitor and endpoint run locally; every admitted dry run executes on EKS.

## Preflight

1. Refresh your own AWS profile and confirm EKS API access. Temporary public-IP
   access must be removed by the operator after recording.
2. Confirm Ready worker nodes and Ready Falco Pods. Use the configured immutable
   ECR digest, `EKS_ENABLED=true`, and `FALCO_ENABLED=true`.
3. For the dashboard demo, set `GATEWAY_URL=http://127.0.0.1:8000/submit`.
   Free ports 5000, 8000 and 9000. Do not run a second gateway against the same
   queue database. Use Python for the clean demo; PowerShell is currently blocked.
4. Start from the repository root:

```sh
# macOS/Linux
.venv/bin/python -m src.dashboard
```

```powershell
# Windows
.\.venv\Scripts\python.exe -m src.dashboard
```

Open http://127.0.0.1:8000. Rehearse once before recording.

## Suggested recording sequence

1. Explain that signature verification happens locally, the dry run happens in
   EKS, and only a clean monitored run is forwarded to the local endpoint.
2. Load **Python: Healthy update**, choose **Signed payload**, then **Run pipeline**.
   Show `execution_location: eks`, successful delivery and endpoint output.
3. Use the same clean sample with **Tamper test**. Show rejection at verification
   (401); the rejected attempt is not admitted to EKS.
4. Upload `src/examples/network_swallowed.py`. Run it as a signed payload. Explain
   that it catches its socket error and exits successfully, but Falco detects the
   attempted network behavior and prevents delivery (final outcome 422).
5. Optionally show **Replay test**: the duplicate nonce is rejected (401).
   Wait for each sequence to finish before starting the next one.

Accepted requests initially return HTTP 202. The CLI polls the gateway status
URL; completed polls return HTTP 200 containing the final `status_code`.
A 503 means capacity or infrastructure/monitoring failure; it is not proof of
malicious behavior. Save the result and investigate instead of presenting it as
successful threat detection.

For a CLI recording, run these in another terminal (use the Windows venv Python
path on Windows):

```sh
.venv/bin/python -m src.sender.send src/examples/good.py
.venv/bin/python -m src.sender.send src/examples/good.py --tamper
.venv/bin/python -m src.sender.send src/examples/network_swallowed.py
```

## Including NGINX

The dashboard URL uses the gateway directly. To demonstrate ingress, follow
README section 4 for the separate NGINX/Waitress stack and send CLI requests to
port 8080. Stop the dashboard stack before starting that stack because they use
the same ports. The production gateway disables dashboard routes.

## Finish

Stop local services with Ctrl+C and stop the project NGINX instance if started.
Check for remaining test Jobs and ConfigMaps in `cloudproject-sandbox`; only
remove resources identified as yours. Ask the operator to remove temporary API
allowlist access. Stopping local services does not stop AWS resource charges.
