from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

try:
    import whisper
except ImportError as exc:  # pragma: no cover - import failure handled at startup
    raise RuntimeError(
        "openai-whisper is not installed. Create the Python 3.10 speech-service environment first."
    ) from exc


WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL_NAME", "base")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "en")
SPEECH_SERVICE_ORIGINS = os.getenv(
    "SPEECH_SERVICE_CORS_ORIGINS",
    "*",
)
TECHNICAL_CORRECTIONS = {
    "encapsulation": [
        "encaps",
        "encapsulasion",
        "encapsolation",
        "encapsulations",
        "in capsule ation",
    ],
    "inheritance": [
        "inheritence",
        "in heritance",
        "inneritance",
    ],
    "polymorphism": [
        "poly morphism",
        "polly morphism",
        "polymorfism",
    ],
    "abstraction": [
        "abstractions",
        "abstrakshan",
        "abstract shun",
    ],
    "REST API": [
        "rest api",
        "rest ap i",
        "rest a p i",
        "restapi",
    ],
    "microservices": [
        "micro service",
        "micro services",
        "micros services",
        "microservice is",
    ],
}


app = FastAPI(title="AI Interview Speech Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if SPEECH_SERVICE_ORIGINS.strip() == "*" else [origin.strip() for origin in SPEECH_SERVICE_ORIGINS.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ffmpeg_executable() -> str:
    ffmpeg_binary = shutil.which("ffmpeg")
    if not ffmpeg_binary:
        raise HTTPException(status_code=500, detail="FFmpeg is not installed or not available in PATH.")
    return ffmpeg_binary


@lru_cache(maxsize=1)
def get_whisper_model():
    logger.info(f"Loading Whisper model: {WHISPER_MODEL_NAME}")
    model = whisper.load_model(WHISPER_MODEL_NAME)
    logger.info("Whisper model loaded successfully")
    return model


def convert_audio_to_wav(source_path: str | Path, wav_path: str | Path) -> None:
    command = [
        _ffmpeg_executable(),
        "-y",
        "-i",
        str(source_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(wav_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Audio conversion failed. {(result.stderr or '').strip()}",
        )


def apply_technical_corrections(text: str) -> str:
    corrected = re.sub(r"\s+", " ", (text or "").strip())
    for canonical, variants in TECHNICAL_CORRECTIONS.items():
        for variant in variants:
            corrected = re.sub(
                rf"\b{re.escape(variant)}\b",
                canonical,
                corrected,
                flags=re.IGNORECASE,
            )
    return corrected


def polish_transcript(text: str) -> str:
    polished = re.sub(r"\s+", " ", (text or "").strip())
    polished = re.sub(r"\bi\b", "I", polished)
    if polished and polished[0].isalpha():
        polished = polished[0].upper() + polished[1:]
    if polished and polished[-1] not in ".!?":
        polished = f"{polished}."
    return polished


def transcribe_audio(audio_path: str | Path) -> str:
    result = get_whisper_model().transcribe(
        str(audio_path),
        fp16=False,
        language=WHISPER_LANGUAGE,
        initial_prompt=(
            "Technical interview answer. Terms may include encapsulation, inheritance, "
            "polymorphism, abstraction, REST API, and microservices."
        ),
    )
    text = polish_transcript(apply_technical_corrections(result.get("text", "")))
    if len(text.strip()) < 3:
        raise HTTPException(status_code=422, detail="No usable speech detected. Please repeat the answer.")
    return text


@app.get("/health")
def health():
    return {"status": "ok", "model": WHISPER_MODEL_NAME, "service": "speech_service"}


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile | None = File(default=None),
    file: UploadFile | None = File(default=None),
):
    start_time = time.time()
    source_path = None
    wav_path = None
    upload = audio or file

    try:
        if upload is None:
            raise HTTPException(status_code=422, detail="Upload an audio file using 'audio' or 'file'.")

        logger.info(f"Received transcription request: {upload.filename}")
        
        suffix = os.path.splitext(upload.filename or "")[1] or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as source_file:
            source_path = source_file.name
            source_file.write(await upload.read())

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as wav_file:
            wav_path = wav_file.name

        convert_audio_to_wav(source_path, wav_path)
        text = transcribe_audio(wav_path)
        
        elapsed = time.time() - start_time
        logger.info(f"Transcription completed in {elapsed:.2f}s: {text[:50]}...")
        
        return {"text": text, "processing_time": elapsed}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Transcription failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Speech service failed: {exc}") from exc
    finally:
        for path in (source_path, wav_path):
            if path and os.path.exists(path):
                os.remove(path)
