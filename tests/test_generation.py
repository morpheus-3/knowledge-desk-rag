import json
import httpx
import pytest
from fastapi import HTTPException
from app import generation

SOURCES = [{"citation": i, "name": "history.pdf", "page": i, "text": "Evidence from the uploaded PDF."} for i in (1, 2)]

@pytest.mark.parametrize("data", [
    {"answer": "Evidence [1, 2]", "citations": ["1", "[2]"]},
    {"answer": "Evidence [Source 1]", "citations": [{"source_id": 1}]},
    {"answer": "Evidence [1]"},
    {"answer": "Evidence", "citations": [1]},
])
def test_normalizes_supported_citations(data):
    answer, sources = generation.parse_answer("```json\n" + json.dumps(data) + "\n```", SOURCES)
    assert "[1]" in answer and sources[0] == SOURCES[0]

def fake_provider(monkeypatch, responses):
    calls = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            calls.append(kwargs)
            status, body = responses[len(calls)-1]
            return httpx.Response(status, json=body)
    monkeypatch.setenv("GROQ_API_KEY", "test-only")
    monkeypatch.setattr(generation.httpx, "Client", Client)
    return calls

def completion(content, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}

@pytest.mark.parametrize("first", [
    (200, completion("invalid JSON")),
    (200, completion(json.dumps({"answer": "Wrong [99]", "citations": [99]}))),
    (200, completion('{"answer":', "length")),
    (400, {"error": {"code": "json_validate_failed"}}),
])
def test_retries_once_and_recovers(monkeypatch, first):
    calls = fake_provider(monkeypatch, [first, (200, completion(json.dumps({"answer": "Evidence [1]", "citations": [1]})))])
    answer, sources = generation.generate("question", SOURCES, [])
    assert answer == "Evidence [1]" and sources == SOURCES[:1]
    assert len(calls) == 2

@pytest.mark.parametrize("status,code,expected", [
    (401, "invalid_api_key", "API key"),
    (429, "rate_limit_exceeded", "rate limit"),
    (413, "request_too_large", "fewer PDFs"),
    (400, "model_decommissioned", "model is unavailable"),
    (400, "invalid_request_error", "HTTP 400"),
])
def test_provider_errors_are_specific_without_retry(monkeypatch, status, code, expected):
    calls = fake_provider(monkeypatch, [(status, {"error": {"code": code}})])
    with pytest.raises(HTTPException) as error:
        generation.generate("question", SOURCES, [])
    assert expected in error.value.detail and len(calls) == 1

def test_retry_limit(monkeypatch):
    calls = fake_provider(monkeypatch, [(200, completion("broken"))] * 2)
    with pytest.raises(HTTPException, match="") as error:
        generation.generate("question", SOURCES, [])
    assert "automatic retry" in error.value.detail and len(calls) == 2
