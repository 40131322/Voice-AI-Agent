"""Text-to-speech route: synthesize assistant reply text to MP3 (output only)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, status
from fastapi.responses import Response

from ..logging_config import logger
from ..services.tts import DEFAULT_VOICE, synth
from ..utils import error_response

router = APIRouter(prefix="/tts", tags=["tts"])


@router.post("", summary="Synthesize assistant reply text to speech (MP3)")
async def synthesize(payload: Dict[str, Any]) -> Response:
    text: Optional[str] = (payload or {}).get("text")
    if not isinstance(text, str) or not text.strip():
        return error_response("Missing 'text'", status_code=status.HTTP_400_BAD_REQUEST)

    voice = (payload or {}).get("voice") or DEFAULT_VOICE
    try:
        audio = await synth(text, voice)
    except Exception as exc:  # network / synthesis failure
        logger.warning("tts synth failed: %s", exc)
        return error_response(
            "TTS synthesis failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )

    if not audio:
        return error_response("No audio produced", status_code=status.HTTP_502_BAD_GATEWAY)

    return Response(content=audio, media_type="audio/mpeg")
