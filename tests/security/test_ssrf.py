from __future__ import annotations

import socket

import pytest

from research_registry.ingestion.fetch_policy import (
    FetchPolicy,
    UrlAddressDenied,
    UrlPortDenied,
    UrlSchemeDenied,
    validate_url,
)
from research_registry.ingestion.web import (
    FetchTimeout,
    FetchTooLarge,
    HardenedWebFetcher,
    MediaTypeDenied,
    ParserFailed,
    parse_json_document,
)


def _resolver(*addresses: str):
    def resolve(host: str, port: int):
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, address)
            for address in addresses
        ]

    return resolve


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
    ],
)
def test_non_public_address_matrix_is_denied(address: str) -> None:
    with pytest.raises(UrlAddressDenied, match="URL_ADDRESS_DENIED"):
        validate_url(
            "https://source.example/research",
            FetchPolicy(),
            resolver=_resolver(address),
        )


def test_one_prohibited_dns_answer_rejects_the_entire_target() -> None:
    with pytest.raises(UrlAddressDenied, match="URL_ADDRESS_DENIED"):
        validate_url(
            "https://source.example/research",
            FetchPolicy(),
            resolver=_resolver("93.184.216.34", "127.0.0.1"),
        )


def test_scheme_credentials_local_names_and_nonstandard_ports_are_denied() -> None:
    policy = FetchPolicy()
    with pytest.raises(UrlSchemeDenied, match="URL_SCHEME_DENIED"):
        validate_url("file:///etc/passwd", policy)
    with pytest.raises(UrlAddressDenied, match="URL_ADDRESS_DENIED"):
        validate_url("https://user:secret@source.example/", policy)
    with pytest.raises(UrlAddressDenied, match="URL_ADDRESS_DENIED"):
        validate_url("https://metadata.google.internal/", policy)
    with pytest.raises(UrlPortDenied, match="URL_ADDRESS_DENIED"):
        validate_url(
            "https://source.example:8443/",
            policy,
            resolver=_resolver("93.184.216.34"),
        )


class _Response:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self._headers = headers or {"content-type": "text/plain"}
        self._chunks = list(chunks or [b"safe source text"])

    def getheaders(self):
        return list(self._headers.items())

    def read(self, amount: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _Connection:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.request_headers: dict[str, str] | None = None

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        assert method == "GET"
        self.request_headers = headers

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        pass


def test_redirect_target_is_revalidated_before_second_connection() -> None:
    connections: list[str] = []

    def resolver(host: str, port: int):
        if host == "public.example":
            return [(socket.AF_INET, "93.184.216.34")]
        return [(socket.AF_INET, "127.0.0.1")]

    def connection_factory(target, policy):
        connections.append(target.address)
        return _Connection(
            _Response(
                status=302,
                headers={
                    "location": "https://private.example/secrets",
                    "content-type": "text/plain",
                },
                chunks=[],
            )
        )

    fetcher = HardenedWebFetcher(
        FetchPolicy(),
        resolver=resolver,
        connection_factory=connection_factory,
    )
    with pytest.raises(UrlAddressDenied, match="URL_ADDRESS_DENIED"):
        fetcher.fetch("https://public.example/start")

    assert connections == ["93.184.216.34"]


def test_transport_receives_validated_address_and_ignores_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    targets = []

    def factory(target, policy):
        targets.append(target)
        return _Connection(_Response())

    result = HardenedWebFetcher(
        FetchPolicy(),
        resolver=_resolver("93.184.216.34"),
        connection_factory=factory,
    ).fetch("https://public.example/source")

    assert result.body == b"safe source text"
    assert targets[0].address == "93.184.216.34"
    assert targets[0].host == "public.example"


def test_oversized_stream_fails_before_a_source_version_can_be_created() -> None:
    fetcher = HardenedWebFetcher(
        FetchPolicy(max_response_bytes=8),
        resolver=_resolver("93.184.216.34"),
        connection_factory=lambda target, policy: _Connection(
            _Response(chunks=[b"12345", b"67890"])
        ),
    )

    with pytest.raises(FetchTooLarge, match="FETCH_TOO_LARGE"):
        fetcher.fetch("https://public.example/large")


def test_media_and_total_elapsed_time_are_bounded() -> None:
    denied_media = HardenedWebFetcher(
        FetchPolicy(),
        resolver=_resolver("93.184.216.34"),
        connection_factory=lambda target, policy: _Connection(
            _Response(headers={"content-type": "application/pdf"})
        ),
    )
    with pytest.raises(MediaTypeDenied, match="MEDIA_TYPE_DENIED"):
        denied_media.fetch("https://public.example/document")

    times = iter([0.0, 0.0, 31.0])
    timed = HardenedWebFetcher(
        FetchPolicy(max_duration_seconds=30),
        resolver=_resolver("93.184.216.34"),
        connection_factory=lambda target, policy: _Connection(_Response()),
        clock=lambda: next(times),
    )
    with pytest.raises(FetchTimeout, match="FETCH_TIMEOUT"):
        timed.fetch("https://public.example/slow")


def test_deep_json_parser_bomb_is_rejected() -> None:
    nested = (b"[" * 101) + b"0" + (b"]" * 101)

    with pytest.raises(ParserFailed, match="PARSER_FAILED"):
        parse_json_document(
            nested,
            maximum_depth=100,
            maximum_nodes=100_000,
        )
