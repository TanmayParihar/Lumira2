"""
Unit tests for the text processing pipeline.
Ollama is mocked — no GPU or running model needed.
"""
from __future__ import annotations

import json
import pytest

from processing.text_pipeline import _parse_llm_json, analyze_text
from processing.schemas import TextAnalysisResult


# ── _parse_llm_json ───────────────────────────────────────────────────────

def test_parse_clean_json():
    raw = '{"event_type": "VIOLENCE", "severity": 3, "confidence": 0.8, ' \
          '"description": "test", "locations": [], "entities": {}, "language": "en"}'
    result = _parse_llm_json(raw)
    assert result["event_type"] == "VIOLENCE"
    assert result["severity"] == 3


def test_parse_json_with_markdown_fence():
    raw = '```json\n{"event_type": "PROTEST", "severity": 2, "confidence": 0.7, ' \
          '"description": "rally", "locations": [], "entities": {}, "language": "en"}\n```'
    result = _parse_llm_json(raw)
    assert result["event_type"] == "PROTEST"


def test_parse_json_with_leading_text():
    raw = 'Here is the analysis:\n{"event_type": "DISASTER", "severity": 5, ' \
          '"confidence": 0.95, "description": "flood", "locations": [], ' \
          '"entities": {}, "language": "en"}'
    result = _parse_llm_json(raw)
    assert result["event_type"] == "DISASTER"


def test_parse_invalid_json_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse_llm_json("not json at all")


# ── analyze_text (mocked Ollama) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_text_returns_result(mock_ollama):
    result = await analyze_text("Armed clashes near LoC in Kupwara district, J&K.")
    assert isinstance(result, TextAnalysisResult)
    assert result.event_type == "VIOLENCE"
    assert result.severity == 4
    assert result.confidence == pytest.approx(0.88, abs=0.01)
    assert any(loc.name == "Kupwara" for loc in result.locations)


@pytest.mark.asyncio
async def test_analyze_empty_text_returns_unknown(mock_ollama):
    result = await analyze_text("")
    assert result.event_type == "UNKNOWN"
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_analyze_text_clamps_severity(mock_ollama, monkeypatch):
    """Even if LLM returns out-of-range severity, we clamp to 1–5."""
    import json
    bad_response = json.dumps({
        "event_type": "CRIME",
        "severity": 99,
        "confidence": 0.5,
        "description": "test",
        "locations": [],
        "entities": {"people": [], "organizations": [], "keywords": []},
        "language": "en",
    })
    mock_ollama.post.return_value.json.return_value = {"response": bad_response}
    result = await analyze_text("Some crime event.")
    assert 1 <= result.severity <= 5


@pytest.mark.asyncio
async def test_analyze_unknown_event_type_normalised(mock_ollama, monkeypatch):
    """Unknown event_type from LLM must be normalised to UNKNOWN."""
    import json
    bad_response = json.dumps({
        "event_type": "ALIEN_INVASION",
        "severity": 2,
        "confidence": 0.3,
        "description": "weird",
        "locations": [],
        "entities": {"people": [], "organizations": [], "keywords": []},
        "language": "en",
    })
    mock_ollama.post.return_value.json.return_value = {"response": bad_response}
    result = await analyze_text("Something weird happened.")
    assert result.event_type == "UNKNOWN"
