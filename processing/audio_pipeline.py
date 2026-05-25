"""
Audio processing pipeline using faster-whisper.

Flow:
  audio bytes / file → faster-whisper → TranscriptionResult →
  → text_pipeline.analyze_text() → TextAnalysisResult
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Optional, Union

import structlog

from config.settings import settings
from processing.schemas import TextAnalysisResult, TranscriptionResult
from storage.minio_client import download_bytes

logger = structlog.get_logger(__name__)

# Lazy-loaded model singleton (expensive to initialise)
_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel

            logger.info(
                "audio_pipeline.loading_whisper",
                model=settings.whisper_model_size,
                device=settings.whisper_device,
            )
            _whisper_model = WhisperModel(
                settings.whisper_model_size,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
            logger.info("audio_pipeline.whisper_ready")
        except ImportError:
            logger.error("audio_pipeline.faster_whisper_not_installed")
            raise
    return _whisper_model


def transcribe_bytes(audio_bytes: bytes, language: Optional[str] = None) -> TranscriptionResult:
    """
    Transcribe raw audio bytes.
    faster-whisper needs a file path, so we write to a temp file.
    """
    model = _get_whisper()

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments_gen, info = model.transcribe(
            tmp_path,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        segments = list(segments_gen)
        full_text = " ".join(seg.text.strip() for seg in segments)

        return TranscriptionResult(
            text=full_text.strip(),
            language=info.language,
            language_probability=info.language_probability,
            duration_seconds=info.duration,
            segments=[
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text.strip(),
                    "no_speech_prob": seg.no_speech_prob,
                }
                for seg in segments
            ],
        )
    finally:
        os.unlink(tmp_path)


def transcribe_file(file_path: Union[str, Path], language: Optional[str] = None) -> TranscriptionResult:
    """Transcribe audio from a local file path."""
    audio_bytes = Path(file_path).read_bytes()
    return transcribe_bytes(audio_bytes, language=language)


async def process_audio(
    minio_path: Optional[str] = None,
    audio_bytes: Optional[bytes] = None,
) -> tuple[TranscriptionResult, TextAnalysisResult]:
    """
    Full audio pipeline:
      1. Download from MinIO (if minio_path given)
      2. Transcribe with Whisper
      3. Run text analysis on transcript
    Returns (transcription, text_analysis).
    """
    from processing.text_pipeline import analyze_text

    if audio_bytes is None and minio_path:
        try:
            audio_bytes = download_bytes(minio_path)
        except Exception as e:
            logger.error("audio_pipeline.download_failed", path=minio_path, error=str(e))
            empty = TranscriptionResult(text="")
            return empty, TextAnalysisResult()

    if not audio_bytes:
        logger.warning("audio_pipeline.no_audio")
        return TranscriptionResult(text=""), TextAnalysisResult()

    try:
        transcription = transcribe_bytes(audio_bytes)
        logger.info(
            "audio_pipeline.transcribed",
            duration=transcription.duration_seconds,
            language=transcription.language,
            text_preview=transcription.text[:100],
        )
    except Exception as e:
        logger.error("audio_pipeline.transcription_failed", error=str(e))
        return TranscriptionResult(text=""), TextAnalysisResult()

    if not transcription.text.strip():
        return transcription, TextAnalysisResult()

    text_result = await analyze_text(transcription.text)
    return transcription, text_result
