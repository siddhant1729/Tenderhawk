"""Parser wiring tests. No API key needed: the Anthropic client talks to a local stub server."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from api.documents import clean_text

GOOD = {
    "min_turnover": {"value": 4000000, "source_clause": "Minimum Average Annual Turnover of the bidder 40 Lakh (s)"},
    "past_experience_years": {"value": 3, "source_clause": "Years of Past Experience Required 3 Year (s)"},
    "emd_amount": {"value": 100000, "source_clause": "EMD Amount 100000"},
    "emd_exemption_for_mse": {"value": True, "source_clause": "MSEs are exempted from EMD"},
    "mse_relaxation_experience_turnover": {"value": False, "source_clause": "MSE Relaxation ... No"},
    "startup_relaxation_experience_turnover": {"value": False, "source_clause": "Startup Relaxation ... No"},
    "required_certifications": {"value": [], "source_clause": None},
    "documents_required": {"value": ["Experience Criteria", "Bidder Turnover"], "source_clause": "Document required"},
    "states": {"value": [], "source_clause": None},
}


def test_clean_text_drops_hindi_keeps_english_and_bid_numbers():
    raw = "ईएमड रािश/EMD Amount\n100000\nबड सं=या/Bid Number: GEM/2026/B/8124639\n'टाट%अप के िलए"
    assert clean_text(raw) == "EMD Amount\n100000\nBid Number: GEM/2026/B/8124639"


@pytest.fixture
def stub_api(monkeypatch):
    """Fake Messages API. Replies with the queued bodies in order and records each request."""
    seen, replies = [], []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
            text = replies.pop(0)
            out = json.dumps({
                "id": "msg_stub", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10},
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from api import llm
    monkeypatch.setattr(llm, "_client", None)
    yield seen, replies
    srv.shutdown()


def test_parse_eligibility_request_shape_and_result(stub_api):
    from api.eligibility import parse_eligibility
    seen, replies = stub_api
    replies.append(json.dumps(GOOD))
    out = asyncio.run(parse_eligibility("Minimum Average Annual Turnover of the bidder 40 Lakh (s)"))

    assert out == json.loads(json.dumps(GOOD))
    req = seen[0]
    body = req["body"]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "min_turnover" in body["output_config"]["format"]["schema"]["properties"]
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in req["headers"].get("anthropic-beta", "")
    assert "<bid_document>" in body["messages"][0]["content"]


def test_invalid_answer_retried_once_then_llmerror(stub_api):
    from api.eligibility import parse_eligibility
    from api.llm import LLMError
    seen, replies = stub_api
    replies.extend(['{"min_turnover": "lots"}', '{"also": "wrong"}'])
    with pytest.raises(LLMError):
        asyncio.run(parse_eligibility("doc"))
    assert len(seen) == 2


def test_retry_recovers(stub_api):
    from api.eligibility import parse_eligibility
    seen, replies = stub_api
    replies.extend(['not json', json.dumps(GOOD)])
    assert asyncio.run(parse_eligibility("doc"))["emd_amount"]["value"] == 100000
    assert len(seen) == 2
