"""get_client_ip() picks the real client IP out of proxy headers, in trust
order, falling back to the direct socket peer."""

from starlette.requests import Request

from src.ratelimit import get_client_ip


def _request(headers=None, client=("1.2.3.4", 1234)):
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": client,
    }
    return Request(scope)


def test_prefers_x_forwarded_for_first_hop():
    req = _request({"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
    assert get_client_ip(req) == "9.9.9.9"


def test_falls_back_to_x_real_ip():
    req = _request({"X-Real-IP": "8.8.8.8"})
    assert get_client_ip(req) == "8.8.8.8"


def test_x_forwarded_for_wins_over_x_real_ip():
    req = _request({"X-Forwarded-For": "9.9.9.9", "X-Real-IP": "8.8.8.8"})
    assert get_client_ip(req) == "9.9.9.9"


def test_falls_back_to_socket_peer_with_no_proxy_headers():
    req = _request({})
    assert get_client_ip(req) == "1.2.3.4"


def test_falls_back_to_unknown_with_no_client_at_all():
    req = _request({}, client=None)
    assert get_client_ip(req) == "unknown"
