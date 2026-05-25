"""
Video processing pipeline.

Flow:
  video file (MinIO) → ffmpeg frame extraction + audio strip →
    frames → image_pipeline (each frame) +
    audio → audio_pipeline (Whisper) →
  → merge results → highest-severity TextAnalysisResult
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import structlog

from config.settings import settings
from processing.schemas import ImageAnalysisResult, TextAnalysisResult, TranscriptionResult
from storage.minio_client import download_bytes

logger = structlog.get_logger(__name__)


def _run_ffmpeg(args: list[str]) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error"] + args,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("video_pipeline.ffmpeg_error", stderr=result.stderr[:300])
            return False
        return True
    except FileNotFoundError:
        logger.error("video_pipeline.ffmpeg_not_found", msg="Install ffmpeg: sudo apt install ffmpeg")
        return False
    except subprocess.TimeoutExpired:
        logger.error("video_pipeline.ffmpeg_timeout")
        return False


def extract_frames(video_path: str, output_dir: str, interval: int) -> list[str]:
    """
    Extract one frame every `interval` seconds.
    Returns list of frame file paths.
    """
    output_pattern = os.path.join(output_dir, "frame_%04d.jpg")
    success = _run_ffmpeg([
        "-i", video_path,
        "-vf", f"fps=1/{interval}",
        "-q:v", "3",
        output_pattern,
    ])
    if not success:
        return []
    return sorted(Path(output_dir).glob("frame_*.jpg"))


def extract_audio(video_path: str, output_path: str) -> bool:
    """Extract audio track as MP3."""
    return _run_ffmpeg([
        "-i", video_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-q:a", "4",
        output_path,
    ])


async def process_video(
    minio_path: Optional[str] = None,
    video_bytes: Optional[bytes] = None,
) -> tuple[list[ImageAnalysisResult], TranscriptionResult, TextAnalysisResult]:
    """
    Full video pipeline:
      1. Download from MinIO
      2. Extract frames + audio strip via ffmpeg
      3. Analyse each frame through image pipeline
      4. Transcribe audio through audio pipeline
      5. Pick the highest-severity text result

    Returns (frame_image_results, transcription, best_text_analysis).
    """
    from processing.audio_pipeline import process_audio
    from processing.image_pipeline import process_image

    if video_bytes is None and minio_path:
        try:
            video_bytes = download_bytes(minio_path)
        except Exception as e:
            logger.error("video_pipeline.download_failed", path=minio_path, error=str(e))
            return [], TranscriptionResult(text=""), TextAnalysisResult()

    if not video_bytes:
        return [], TranscriptionResult(text=""), TextAnalysisResult()

    tmp_dir = tempfile.mkdtemp(prefix="lumira_video_")
    try:
        video_file = os.path.join(tmp_dir, "input.mp4")
        audio_file = os.path.join(tmp_dir, "audio.mp3")
        frames_dir = os.path.join(tmp_dir, "frames")
        os.makedirs(frames_dir)

        # Write video to disk
        with open(video_file, "wb") as f:
            f.write(video_bytes)

        # Extract frames
        frame_paths = extract_frames(
            video_file, frames_dir, settings.video_frame_sample_interval
        )
        logger.info("video_pipeline.frames_extracted", count=len(frame_paths))

        # Extract audio
        audio_ok = extract_audio(video_file, audio_file)

        # ── Process frames ────────────────────────────────────────────────
        frame_results: list[ImageAnalysisResult] = []
        text_results: list[TextAnalysisResult] = []

        for frame_path in frame_paths[:10]:  # cap at 10 frames
            img_bytes = Path(str(frame_path)).read_bytes()
            img_result, txt_result = await process_image(image_bytes=img_bytes)
            frame_results.append(img_result)
            text_results.append(txt_result)

        # ── Process audio ─────────────────────────────────────────────────
        transcription = TranscriptionResult(text="")
        if audio_ok and os.path.exists(audio_file):
            audio_bytes_data = Path(audio_file).read_bytes()
            transcription, audio_text = await process_audio(audio_bytes=audio_bytes_data)
            text_results.append(audio_text)

        # ── Pick best result ──────────────────────────────────────────────
        best = _pick_best_text_result(text_results)
        logger.info(
            "video_pipeline.complete",
            frames=len(frame_results),
            audio_duration=transcription.duration_seconds,
            event_type=best.event_type,
            severity=best.severity,
        )
        return frame_results, transcription, best

    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _pick_best_text_result(results: list[TextAnalysisResult]) -> TextAnalysisResult:
    """Return the result with the highest severity × confidence score."""
    if not results:
        return TextAnalysisResult()
    valid = [r for r in results if r.event_type != "UNKNOWN"]
    pool = valid or results
    return max(pool, key=lambda r: r.severity * r.confidence)
