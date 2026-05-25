"""
Image processing pipeline.

Flow:
  image bytes → PaddleOCR (text extraction) +
                Qwen2-VL via Ollama (visual caption) →
  → combine text → text_pipeline.analyze_text()
"""
from __future__ import annotations

import base64
import io
import os
import tempfile
from pathlib import Path
from typing import Optional

import httpx
import structlog
from PIL import Image
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from processing.schemas import ImageAnalysisResult, TextAnalysisResult
from storage.minio_client import download_bytes

logger = structlog.get_logger(__name__)

# Lazy OCR singleton
_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        try:
            from paddleocr import PaddleOCR

            logger.info("image_pipeline.loading_paddleocr")
            _ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
            logger.info("image_pipeline.paddleocr_ready")
        except ImportError:
            logger.warning("image_pipeline.paddleocr_not_installed")
    return _ocr


def extract_ocr_text(image_bytes: bytes) -> str:
    """Run PaddleOCR on image bytes. Returns concatenated text."""
    ocr = _get_ocr()
    if ocr is None:
        return ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            # Ensure it's JPEG-compatible
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            img.save(tmp.name, "JPEG")
            tmp_path = tmp.name
        try:
            result = ocr.ocr(tmp_path, cls=True)
            lines = []
            if result and result[0]:
                for line in result[0]:
                    if line and len(line) >= 2:
                        text_conf = line[1]
                        if isinstance(text_conf, (list, tuple)) and len(text_conf) >= 1:
                            text = text_conf[0]
                            conf = text_conf[1] if len(text_conf) > 1 else 1.0
                            if conf > 0.5:
                                lines.append(text)
            return " ".join(lines)
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        logger.warning("image_pipeline.ocr_failed", error=str(e))
        return ""


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
async def _call_vision_model(image_bytes: bytes, prompt: str) -> str:
    """Call Qwen2-VL via Ollama's multimodal API."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
        resp = await client.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.vision_model,
                "prompt": prompt,
                "images": [b64],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 256},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        # qwen3-vl thinking model returns output in `thinking` when response is empty
        return data.get("response") or data.get("thinking", "")


VISION_PROMPT = (
    "You are an intelligence analyst reviewing an image for India security monitoring. "
    "Describe: (1) what is happening in the image, (2) any visible location identifiers "
    "(signs, landmarks, text), (3) any people or vehicles present, (4) any signs of "
    "violence, protest, disaster, or unusual activity. Be concise and factual. "
    "Focus on security-relevant details."
)


async def analyze_image(image_bytes: bytes) -> ImageAnalysisResult:
    """
    Run OCR + vision model caption on image bytes.
    Returns ImageAnalysisResult with combined text.
    """
    # Resize image if too large (Ollama has input limits)
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if max(img.size) > 1024:
            img.thumbnail((1024, 1024), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            image_bytes = buf.getvalue()
    except Exception as e:
        logger.warning("image_pipeline.resize_failed", error=str(e))

    # Run OCR
    ocr_text = extract_ocr_text(image_bytes)

    # Run vision model
    caption = ""
    objects_detected = []
    try:
        caption = await _call_vision_model(image_bytes, VISION_PROMPT)
    except Exception as e:
        logger.warning("image_pipeline.vision_model_failed", error=str(e))

    combined = "\n\n".join(filter(None, [caption, f"OCR text: {ocr_text}" if ocr_text else ""]))

    return ImageAnalysisResult(
        caption=caption,
        ocr_text=ocr_text,
        combined_text=combined,
        raw_vision_output=caption,
    )


async def process_image(
    minio_path: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
) -> tuple[ImageAnalysisResult, TextAnalysisResult]:
    """
    Full image pipeline:
      1. Download from MinIO (if path given)
      2. OCR + vision caption
      3. Text analysis on combined output
    Returns (image_analysis, text_analysis).
    """
    from processing.text_pipeline import analyze_text

    if image_bytes is None and minio_path:
        try:
            image_bytes = download_bytes(minio_path)
        except Exception as e:
            logger.error("image_pipeline.download_failed", path=minio_path, error=str(e))
            return ImageAnalysisResult(), TextAnalysisResult()

    if not image_bytes:
        return ImageAnalysisResult(), TextAnalysisResult()

    image_result = await analyze_image(image_bytes)

    if not image_result.combined_text.strip():
        return image_result, TextAnalysisResult()

    text_result = await analyze_text(image_result.combined_text)
    return image_result, text_result
