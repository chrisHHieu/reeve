"""The address guard `web_fetch` goes through (`integrations/web/guard`): a URL the model wrote never
reaches this machine's own network position.

The URL comes straight from the model, whose input is untrusted (fetched pages are "data to
evaluate, not instructions"), and `web_fetch` asks nobody, so the guard is all there is. A name is
judged by every address it resolves to, the connection goes to the address that was judged, and
every redirect hop is judged again before it is requested.
"""

import socket

import pytest

from harness.integrations.web import guard
from harness.integrations.web.fetch import make_web_fetch_tool

PUBLIC = "93.184.216.34"


def _answers(monkeypatch, *ips: str):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80)) for ip in ips])


# -- the address itself --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "said"),
    [
        ("http://127.0.0.1:11434/api/tags", "loopback"),
        ("http://localhost:8000/", "loopback"),
        ("http://[::1]:8080/", "loopback"),
        ("http://169.254.169.254/latest/meta-data/", "link-local"),
        ("http://10.0.0.5/admin", "private"),
        ("http://192.168.1.1/", "private"),
        ("http://172.16.4.4/", "private"),
        ("http://0.0.0.0/", "refusing to fetch"),  # 0.0.0.0/8 is caught as private first
        ("http://100.64.0.1/", "CGNAT"),  # RFC 6598 shared space: Tailscale, carrier NAT
        ("http://100.127.255.254/", "CGNAT"),
        ("https://user:secret@example.com/x", "credentials"),  # what a redirect or proxy would pass on
        ("https://example.com/" + "a" * guard.MAX_URL_LENGTH, "maximum length"),
    ],
)
def test_an_address_of_this_machine_or_its_network_is_refused_and_the_refusal_says_why(url, said):
    refusal = guard.check_url(url)
    assert refusal and said in refusal


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/", "http://", "http://[::ffff:127.0.0.1]/"])
def test_anything_but_http_to_a_host_is_refused_and_a_mapped_v4_is_judged_as_v4(url):
    assert guard.check_url(url)


def test_a_public_address_passes_and_so_do_the_neighbours_of_the_shared_space(monkeypatch):
    assert guard.check_url(f"https://{PUBLIC}/") is None
    for neighbour in ("100.63.255.255", "100.128.0.0"):  # 100.64.0.0/10 is shut, around it is public
        _answers(monkeypatch, neighbour)
        assert guard.check_url("http://beside.example/") is None


# -- a name, by what it resolves to --------------------------------------------------------------


def test_a_public_name_with_a_private_answer_is_refused(monkeypatch):
    _answers(monkeypatch, "127.0.0.1")  # localtest.me and its like
    assert "loopback" in guard.check_url("http://sneaky.example.com/")
    _answers(monkeypatch, "169.254.169.254")
    assert guard.check_url("http://metadata.example.com/")


def test_one_private_answer_among_public_ones_is_enough_to_refuse(monkeypatch):
    _answers(monkeypatch, PUBLIC, "127.0.0.1")
    assert guard.check_url("http://split.example.com/")


def test_a_name_with_only_public_answers_passes(monkeypatch):
    _answers(monkeypatch, PUBLIC)
    assert guard.check_url("https://example.com/docs") is None


def test_a_name_that_does_not_resolve_is_refused_and_not_fetched(monkeypatch):
    def unresolved(*a, **k):
        raise socket.gaierror("nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", unresolved)
    assert "could not resolve" in guard.check_url("http://nope.invalid/")


# -- requesting it -------------------------------------------------------------------------------


class _Url(str):
    def join(self, other):
        return other


class _Response:
    def __init__(self, status=200, location=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.url = _Url("https://example.com/")
        self.text = "body"
        self.extensions: dict = {}
        self.closed = False

    def raise_for_status(self):
        pass

    def close(self):
        self.closed = True


class _Client:
    """Keeps what was asked for, so a refused hop is shown never to have been requested."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, headers=None, extensions=None):
        self.calls.append({"url": url, "headers": headers or {}, "extensions": extensions or {}})
        return self._responses.pop(0)

    @property
    def requested(self):
        return [call["url"] for call in self.calls]


class _StreamingClient(_Client):
    """The httpx shape a streamed fetch uses: build_request, then send(stream=True)."""

    def build_request(self, method, url, headers=None, extensions=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "extensions": extensions or {}})
        return self.calls[-1]

    def send(self, request, stream=False):
        assert stream is True
        return self._responses.pop(0)


def test_a_redirect_into_this_machine_is_refused_before_it_is_requested(monkeypatch):
    _answers(monkeypatch, PUBLIC)
    client = _Client(_Response(302, location="http://127.0.0.1:11434/api/tags"))
    with pytest.raises(PermissionError, match="loopback"):
        guard.get_checked(client, "https://example.com/start")
    assert client.requested == [f"https://{PUBLIC}/start"]


def test_allowed_redirects_are_followed_a_bounded_number_of_times(monkeypatch):
    _answers(monkeypatch, PUBLIC)
    client = _Client(_Response(302, location="https://example.com/b"), _Response(200))
    assert guard.get_checked(client, "https://example.com/a").status_code == 200
    assert client.requested == [f"https://{PUBLIC}/a", f"https://{PUBLIC}/b"]
    with pytest.raises(RuntimeError, match="too many redirects"):
        guard.get_checked(_Client(*[_Response(302, location="https://example.com/loop")] * 50), "https://example.com/loop")


def test_the_connection_goes_to_the_judged_address_with_the_name_kept_for_host_and_sni(monkeypatch):
    _answers(monkeypatch, PUBLIC)
    client = _Client(_Response(200))
    guard.get_checked(client, "https://example.com/docs")
    [call] = client.calls
    assert call["url"] == f"https://{PUBLIC}/docs"
    assert (call["headers"]["Host"], call["extensions"]["sni_hostname"]) == ("example.com", "example.com")


def test_a_name_that_rebinds_after_it_was_judged_still_reaches_the_judged_address(monkeypatch):
    answers = iter([PUBLIC, "127.0.0.1"])  # a ~0 TTL record flipping between check and connect
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers, "127.0.0.1"), 80))])
    client = _Client(_Response(200))
    guard.get_checked(client, "http://rebind.example.com/")
    assert client.requested == [f"http://{PUBLIC}/"]


def test_a_port_is_kept_in_host_and_plain_http_sends_no_sni(monkeypatch):
    _answers(monkeypatch, PUBLIC)
    client = _Client(_Response(200))
    guard.get_checked(client, "http://example.com:8080/x")
    [call] = client.calls
    assert (call["url"], call["headers"]["Host"]) == (f"http://{PUBLIC}:8080/x", "example.com:8080")
    assert "sni_hostname" not in call["extensions"]


def test_a_v6_answer_is_written_in_brackets(monkeypatch):
    _answers(monkeypatch, "2606:2800:220:1:248:1893:25c8:1946")
    client = _Client(_Response(200))
    guard.get_checked(client, "https://example.com/")
    assert client.requested == ["https://[2606:2800:220:1:248:1893:25c8:1946]/"]


def test_a_url_that_is_already_an_address_is_requested_as_written():
    client = _Client(_Response(200))
    guard.get_checked(client, f"https://{PUBLIC}/x")
    [call] = client.calls
    assert call["url"] == f"https://{PUBLIC}/x" and "Host" not in call["headers"]


def test_the_url_reported_back_is_the_name_never_the_address(monkeypatch):
    _answers(monkeypatch, PUBLIC)  # the model is shown the final URL
    client = _Client(_Response(302, location="https://example.com/b"), _Response(200))
    assert guard.get_checked(client, "https://example.com/a").extensions["logical_url"] == "https://example.com/b"


def test_a_streamed_fetch_pins_every_hop_and_lets_go_of_each_redirect(monkeypatch):
    _answers(monkeypatch, PUBLIC)
    hop, final = _Response(302, location="https://example.com/b"), _Response(200)
    client = _StreamingClient(hop, final)
    answer = guard.get_checked(client, "https://example.com/a", stream=True)
    assert answer is final and answer.extensions["logical_url"] == "https://example.com/b"
    pinned = {"headers": {"Host": "example.com"}, "extensions": {"sni_hostname": "example.com"}}
    assert client.calls == [{"method": "GET", "url": f"https://{PUBLIC}/a", **pinned}, {"method": "GET", "url": f"https://{PUBLIC}/b", **pinned}]
    assert hop.closed is True and final.closed is False  # the caller closes what it was given


def test_a_streamed_fetch_refuses_a_bad_hop_before_sending_it(monkeypatch):
    _answers(monkeypatch, PUBLIC)
    client = _StreamingClient(_Response(302, location="http://127.0.0.1:11434/x"))
    with pytest.raises(PermissionError, match="loopback"):
        guard.get_checked(client, "https://example.com/start", stream=True)
    assert len(client.calls) == 1


# -- what the tool returns -----------------------------------------------------------------------


def test_web_fetch_gives_the_refusal_back_as_its_error(monkeypatch):
    _answers(monkeypatch, "127.0.0.1")
    refused = make_web_fetch_tool()("http://sneaky.example.com/")
    assert "loopback" in refused["error"] and "text" not in refused
    assert "http" in make_web_fetch_tool()("file:///etc/passwd")["error"]
