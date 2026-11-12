# Guards the properties of nginx/nginx.conf that the security argument depends on.
import re
from pathlib import Path

from src.gateway.serve import MAX_BODY_BYTES
from src.ingress import PROXY_READ_SECONDS, CLIENT_TIMEOUT, ENDPOINT_TIMEOUT

CONF = (Path(__file__).resolve().parents[2] / "nginx" / "nginx.conf").read_text()
CODE = re.sub(r"#.*", "", CONF)  # ignore comments


def test_only_submission_and_status_routes_are_proxied():
    assert len(re.findall(r"\bproxy_pass\b", CODE)) == 2
    assert re.search(r"location\s*=\s*/submit\s*\{", CODE)
    assert re.search(r"location\s+\^~\s+/submissions/\s*\{\s*limit_except\s+GET\s*\{\s*deny all;", CODE)


def test_everything_else_is_404():
    assert re.search(r"location\s*/\s*\{\s*return\s+404;\s*\}", CODE)


def test_rate_limit_status_is_not_503():
    # 503 is the gateway's own "capacity" / "sandbox unavailable" status; keep them distinguishable
    assert re.search(r"limit_req_status\s+429;", CODE)


def test_body_cap_matches_the_gateway():
    assert re.search(rf"client_max_body_size\s+{MAX_BODY_BYTES // 1024}k;", CODE)


def test_post_is_never_retried():
    assert re.search(r"proxy_next_upstream\s+off;", CODE)


def test_read_timeout_outlasts_a_dry_run():
    seconds = int(re.search(r"proxy_read_timeout\s+(\d+)s;", CODE).group(1))
    assert seconds == PROXY_READ_SECONDS
    assert 10 + sum(ENDPOINT_TIMEOUT) + 5 < seconds < CLIENT_TIMEOUT[1]


def test_concurrency_bounds_gateway_load():
    from src.gateway.serve import choose_threads
    # Nginx does not account for empty keys; $server_name must have a value.
    assert re.search(r"server_name\s+pipeline-gateway;", CODE)
    for zone in ("client_connections", "total_connections"):
        limit = int(re.search(rf"limit_conn\s+{zone}\s+(\d+);", CODE).group(1))
        assert 6 <= limit <= choose_threads(None)
    assert re.search(r"limit_conn_status\s+429;", CODE)
