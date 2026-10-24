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


def test_clean_text_strips_nul_bytes():
    assert clean_text("EMD\x00 Amount") == "EMD Amount"


def test_llm_module_loads_dotenv(tmp_path, monkeypatch):
    """`python -m api.eligibility` must see keys from .env (it doesn't import the server)."""
    import subprocess, sys, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    (tmp_path / ".env").write_text("LLM_EFFORT=from-dotenv\n")
    out = subprocess.run([sys.executable, "-c", "import api.llm as l; print(l.LLM_EFFORT)"],
                         cwd=tmp_path, env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert out.stdout.strip() == "from-dotenv", out.stderr


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
            if text.startswith("!400 "):
                out = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                                                             "message": text[5:]}}).encode()
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
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
    from api import llm
    assert body["model"] == llm.LLM_MODEL
    assert body["output_config"]["effort"] == llm.LLM_EFFORT
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


def test_rejected_fallbacks_param_retries_without_it(stub_api):
    from api.eligibility import parse_eligibility
    seen, replies = stub_api
    replies.extend(["!400 fallbacks: not available for this account", json.dumps(GOOD)])
    assert asyncio.run(parse_eligibility("doc"))["min_turnover"]["value"] == 4000000
    assert "fallbacks" in seen[0]["body"] and "fallbacks" not in seen[1]["body"]


@pytest.fixture
def stub_groq(monkeypatch):
    """Fake Groq (OpenAI-compatible) chat completions API. Same queue/record shape as stub_api."""
    seen, replies = [], []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({"path": self.path, "body": body})
            out = json.dumps({
                "id": "chatcmpl-stub", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": replies.pop(0)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
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
    from api import llm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(llm, "GROQ_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(llm, "_groq_client", None)
    yield seen, replies
    srv.shutdown()


def test_provider_choice(monkeypatch):
    from api import llm
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert llm.provider() == "anthropic"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert llm.provider() == "groq" and llm.concurrency() == 1
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert llm.provider() == "anthropic" and llm.max_doc_chars() is None


def test_groq_request_is_strict_schema_and_result_validates(stub_groq):
    from api import llm
    from api.eligibility import parse_eligibility
    seen, replies = stub_groq
    replies.append(json.dumps(GOOD))
    assert asyncio.run(parse_eligibility("Minimum Average Annual Turnover of the bidder 40 Lakh (s)")) == GOOD

    body = seen[0]["body"]
    assert seen[0]["path"].endswith("/chat/completions")
    assert body["model"] == llm.GROQ_MODEL
    fmt = body["response_format"]["json_schema"]
    assert fmt["strict"] is True
    crit = fmt["schema"]["$defs"]["NumberCriterion"]
    assert crit["additionalProperties"] is False and set(crit["required"]) == {"value", "source_clause"}
    assert fmt["schema"]["additionalProperties"] is False
    assert "<bid_document>" in body["messages"][1]["content"]


def test_groq_long_document_is_truncated(stub_groq, monkeypatch):
    from api import llm
    from api.eligibility import parse_eligibility
    seen, replies = stub_groq
    monkeypatch.setattr(llm, "GROQ_MAX_DOC_CHARS", 50)
    replies.append(json.dumps(GOOD))
    asyncio.run(parse_eligibility("EMD Amount 100000 " + "x" * 500))
    sent = seen[0]["body"]["messages"][1]["content"]
    assert "EMD Amount 100000" in sent and "x" * 100 not in sent


def test_groq_invalid_answer_retried_once_then_llmerror(stub_groq):
    from api.eligibility import parse_eligibility
    from api.llm import LLMError
    seen, replies = stub_groq
    replies.extend(['{"min_turnover": "lots"}', "not json"])
    with pytest.raises(LLMError):
        asyncio.run(parse_eligibility("doc"))
    assert len(seen) == 2
