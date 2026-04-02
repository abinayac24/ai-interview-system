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

try:
    import nemo
    import nemo.collections.asr as nemo_asr
    from nemo.core.classes import ModelPT
    NEMO_AVAILABLE = True
except ImportError:
    NEMO_AVAILABLE = False


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
        "incapsulation",
        "encapsuation",
        "encapsulaton",
    ],
    "inheritance": [
        "inheritence",
        "in heritance",
        "inneritance",
        "inheritanc",
        "ineritance",
        "inheretance",
    ],
    "polymorphism": [
        "poly morphism",
        "polly morphism",
        "polymorfism",
        "polimorphism",
        "polymorphysm",
        "polymorphim",
    ],
    "abstraction": [
        "abstractions",
        "abstrakshan",
        "abstract shun",
        "abstraktion",
        "abstracshun",
    ],
    "REST API": [
        "rest api",
        "rest ap i",
        "rest a p i",
        "restapi",
        "rest a p i",
        "rest a p eye",
    ],
    "microservices": [
        "micro service",
        "micro services",
        "micros services",
        "microservice is",
        "micro service's",
        "microservis",
    ],
    "API": [
        "a p i",
        "ap i",
        "a p eye",
    ],
    "database": [
        "data base",
        "data bases",
        "databace",
    ],
    "algorithm": [
        "algo rhythm",
        "algo rithm",
        "algoritm",
        "algorythm",
    ],
    "framework": [
        "frame work",
        "frame werk",
        "framwork",
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

@lru_cache(maxsize=1)
def get_nemo_model():
    if not NEMO_AVAILABLE:
        return None
    try:
        logger.info("Loading Nemo QuartzNet model")
        model = nemo_asr.models.EncDecCTCModel.from_pretrained(model_name="stt_en_quartznet15x5_base")
        logger.info("Nemo model loaded successfully")
        return model
    except Exception as e:
        logger.warning(f"Nemo model loading failed: {e}")
        return None


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


def transcribe_with_whisper(audio_path: str | Path) -> str:
    try:
        result = get_whisper_model().transcribe(
            str(audio_path),
            fp16=False,
            language=WHISPER_LANGUAGE,
            temperature=0.0,
            best_of=1,
            beam_size=1,
            initial_prompt=(
                "Technical interview answer. Terms may include encapsulation, inheritance, "
                "polymorphism, abstraction, REST API, microservices, algorithm, database, framework, API."
            ),
        )
        return polish_transcript(apply_technical_corrections(result.get("text", "")))
    except Exception as e:
        logger.error(f"Whisper transcription failed: {e}")
        return ""

def transcribe_with_nemo(audio_path: str | Path) -> str:
    if not NEMO_AVAILABLE:
        return ""
    try:
        model = get_nemo_model()
        if not model:
            return ""
        
        # Convert audio to the format Nemo expects
        import torch
        import librosa
        
        # Load and preprocess audio
        audio, sample_rate = librosa.load(str(audio_path), sr=16000)
        audio_tensor = torch.from_numpy(audio).unsqueeze(0)
        
        # Transcribe
        transcription = model.transcribe([audio_tensor])
        text = transcription[0] if transcription else ""
        
        return polish_transcript(apply_technical_corrections(text))
    except Exception as e:
        logger.error(f"Nemo transcription failed: {e}")
        return ""

def transcribe_audio(audio_path: str | Path) -> str:
    # Try Whisper first
    text = transcribe_with_whisper(audio_path)
    
    # If Whisper fails or returns empty, try Nemo as fallback
    if not text or len(text.strip()) < 3:
        logger.info("Whisper failed, trying Nemo as fallback")
        text = transcribe_with_nemo(audio_path)
    
    if len(text.strip()) < 3:
        raise HTTPException(status_code=422, detail="No usable speech detected. Please repeat the answer.")
    return text


@app.get("/health")
def health():
    return {
        "status": "ok", 
        "whisper_model": WHISPER_MODEL_NAME,
        "nemo_available": NEMO_AVAILABLE,
        "service": "speech_service"
    }

@app.get("/test")
def test_models():
    results = {}
    
    # Test Whisper
    try:
        whisper_model = get_whisper_model()
        results["whisper"] = {"status": "loaded", "model": WHISPER_MODEL_NAME}
    except Exception as e:
        results["whisper"] = {"status": "error", "error": str(e)}
    
    # Test Nemo
    if NEMO_AVAILABLE:
        try:
            nemo_model = get_nemo_model()
            results["nemo"] = {"status": "loaded", "model": "QuartzNet15x5"}
        except Exception as e:
            results["nemo"] = {"status": "error", "error": str(e)}
    else:
        results["nemo"] = {"status": "not_available"}
    
    return {"models": results}


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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)
