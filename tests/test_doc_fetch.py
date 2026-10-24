"""Bid document chain in agents/: TinyFish Fetch first, direct PDF download only as a last resort."""
import asyncio

import httpx
import pymupdf
import pytest

from agents import client

GEM_DOC = "https://bidplus.gem.gov.in/showbidDocument/9845350"


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("FALLBACK_MODE", "false")
    calls = {"fetch": 0, "direct": []}

    async def no_fetch(url):
        calls["fetch"] += 1
        return ""
    monkeypatch.setattr(client, "_fetch", no_fetch)
    return calls


def _serve(monkeypatch, calls, status=200, body=b""):
    real = httpx.AsyncClient

    def handler(request):
        calls["direct"].append(str(request.url))
        return httpx.Response(status, content=body)
    monkeypatch.setattr(client.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_fetch_text_wins_without_direct_download(monkeypatch, live):
    async def fetch(url):
        return "EMD Amount 100000"
    monkeypatch.setattr(client, "_fetch", fetch)
    _serve(monkeypatch, live, body=_pdf("never used"))
    assert asyncio.run(client.fetch_doc_text(GEM_DOC)) == ("EMD Amount 100000", "fetch")
    assert live["direct"] == []


def test_fetch_failure_falls_back_to_direct_pdf(monkeypatch, live):
    _serve(monkeypatch, live, body=_pdf("Minimum Average Annual Turnover of the bidder 3 Lakh"))
    text, source = asyncio.run(client.fetch_doc_text(GEM_DOC))
    assert source == "direct" and "Annual Turnover of the bidder 3 Lakh" in text
    assert live["fetch"] == 1 and live["direct"] == [GEM_DOC]


@pytest.mark.parametrize("url", ["https://evil.example.com/x.pdf", "http://bidplus.gem.gov.in/showbidDocument/1"])
def test_direct_download_only_https_on_swept_portals(monkeypatch, live, url):
    _serve(monkeypatch, live, body=_pdf("x"))
    assert asyncio.run(client.fetch_doc_text(url)) == ("", "")
    assert live["direct"] == []


@pytest.mark.parametrize("status,body", [(200, b"<html>login</html>"), (503, b"%PDF-")])
def test_direct_download_rejects_non_pdf_and_errors(monkeypatch, live, status, body):
    _serve(monkeypatch, live, status=status, body=body)
    assert asyncio.run(client.fetch_doc_text(GEM_DOC)) == ("", "")


def test_fallback_mode_reads_fixture_docs_only(monkeypatch):
    monkeypatch.setenv("FALLBACK_MODE", "true")
    monkeypatch.setattr(client, "_fixture_docs", lambda: {GEM_DOC: "EMD Amount 1"})
    assert asyncio.run(client.fetch_doc_text(GEM_DOC)) == ("EMD Amount 1", "fixture")
    assert asyncio.run(client.fetch_doc_text("https://bidplus.gem.gov.in/showbidDocument/0")) == ("", "")


def test_agent_limits_never_send_max_steps():
    """The account rejects a custom max_steps with HTTP 403, which would fail every live sweep."""
    assert client.AGENT_LIMITS.max_steps is None and client.AGENT_LIMITS.max_duration_seconds == 300
