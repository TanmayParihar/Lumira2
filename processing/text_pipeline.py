"""
Text processing pipeline using Qwen2.5-7B via Ollama.

Flow:
  raw text → prompt → Ollama /api/generate → parse JSON →
  → TextAnalysisResult (event_type, severity, locations, entities)
"""
from __future__ import annotations

import json
import re
from typing import Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from processing.schemas import ExtractedEntities, LocationEntity, TextAnalysisResult

logger = structlog.get_logger(__name__)

# ── Prompt template ──────────────────────────────────────────────────────────
EXTRACTION_PROMPT = """You are an intelligence analyst specializing in India security and public safety.
Analyze the following text and extract structured intelligence information.

Return ONLY valid JSON with EXACTLY this structure (no extra text):
{{
  "event_type": "<one of: VIOLENCE|PROTEST|ACCIDENT|DISASTER|CRIME|POLITICAL|MILITARY|TERRORISM|HEALTH|INFRASTRUCTURE|UNKNOWN>",
  "severity": <integer 1-5, where 1=minor local incident, 5=national emergency>,
  "confidence": <float 0.0-1.0, your confidence in this classification>,
  "description": "<concise summary in 1-2 sentences>",
  "locations": [
    {{"name": "<place name>", "entity_type": "<city|district|state|landmark|region>"}}
  ],
  "entities": {{
    "people": ["<name1>", "..."],
    "organizations": ["<org1>", "..."],
    "keywords": ["<keyword1>", "..."]
  }},
  "language": "<ISO 639-1 code, e.g. en|hi|ta>"
}}

Severity guide:
1 = minor local incident with no casualties
2 = low impact, localised, few casualties
3 = moderate, district-level impact, multiple casualties
4 = high impact, state-level, significant casualties or disruption
5 = critical, national significance, mass casualties or national security

Text to analyze:
---
{text}
---"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def _call_ollama(prompt: str) -> str:
    """Call Ollama generate endpoint and return the raw string response.

    qwen3.x is a "thinking" model: with format=json Ollama puts the structured
    output in the `thinking` field and leaves `response` empty.  We fall back
    to `thinking` when `response` is blank so the pipeline works for both
    regular and thinking-capable models.
    """
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
        resp = await client.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.text_model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 512,
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()
        # For thinking models (qwen3.x) the JSON output lands in `thinking`,
        # not `response`.  Try both fields.
        return data.get("response") or data.get("thinking", "")


def _parse_llm_json(raw: str) -> dict:
    """Extract JSON from LLM output — handles markdown code fences."""
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = cleaned.rstrip("`").strip()

    # Find the first { ... } block
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start != -1 and end > start:
        cleaned = cleaned[start:end]

    return json.loads(cleaned)


async def analyze_text(text: str, max_chars: int = 3000) -> TextAnalysisResult:
    """
    Run the full text analysis pipeline on a text string.
    Returns a TextAnalysisResult with event type, severity, locations, etc.
    """
    if not text or not text.strip():
        return TextAnalysisResult(event_type="UNKNOWN", confidence=0.0)

    # Truncate to avoid token limits
    truncated = text[:max_chars].strip()
    prompt = EXTRACTION_PROMPT.format(text=truncated)

    try:
        raw_output = await _call_ollama(prompt)
        parsed = _parse_llm_json(raw_output)

        # Validate event_type
        valid_types = set(settings.event_types)
        event_type = parsed.get("event_type", "UNKNOWN").upper()
        if event_type not in valid_types:
            event_type = "UNKNOWN"

        # Parse locations
        locations = []
        for loc in parsed.get("locations", []):
            if isinstance(loc, dict) and loc.get("name"):
                locations.append(
                    LocationEntity(
                        name=loc["name"].strip(),
                        entity_type=loc.get("entity_type", "unknown"),
                    )
                )

        # Parse entities
        ents = parsed.get("entities", {})
        entities = ExtractedEntities(
            people=ents.get("people", []) or [],
            organizations=ents.get("organizations", []) or [],
            keywords=ents.get("keywords", []) or [],
        )

        return TextAnalysisResult(
            event_type=event_type,
            severity=max(1, min(5, int(parsed.get("severity", 1)))),
            confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.5)))),
            description=parsed.get("description", "")[:500],
            locations=locations,
            entities=entities,
            language=parsed.get("language", "en"),
            raw_llm_output=raw_output,
        )

    except json.JSONDecodeError as e:
        logger.warning(
            "text_pipeline.json_parse_failed",
            error=str(e),
            raw_snippet=raw_output[:200] if "raw_output" in dir() else "",
        )
        return TextAnalysisResult(event_type="UNKNOWN", confidence=0.1)
    except Exception as e:
        logger.error("text_pipeline.error", error=str(e))
        return TextAnalysisResult(event_type="UNKNOWN", confidence=0.0)


async def check_ollama_health() -> bool:
    """Check if Ollama is reachable and the text model is loaded."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.ollama_url}/api/tags")
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return any(settings.text_model.split(":")[0] in m for m in models)
    except Exception as e:
        logger.warning("text_pipeline.ollama_unavailable", error=str(e))
    return False
