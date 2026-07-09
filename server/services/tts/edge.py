"""Text-to-speech via edge-tts (Microsoft Edge neural voices).

Output-only: converts the interaction agent's reply text into MP3 bytes. It uses
the free Edge Read-Aloud endpoint, so there is NO API key — but it DOES require
outbound network access. STT (input) is handled separately in the browser
(Web Speech ``SpeechRecognition``). The loop is: STT -> Claude/OpenRouter -> here.

Voice: ``en-US-AvaNeural`` matches the "Ava" front-desk persona in the greeting.
Browse alternatives with ``edge-tts --list-voices`` (EmmaNeural, JennyNeural,
AriaNeural are also good clinic-receptionist voices).
"""

from __future__ import annotations

import re

import edge_tts

# Default neural voice — the clinic receptionist ("Ava").
DEFAULT_VOICE = "en-US-AvaNeural"


def _normalize(text: str) -> str:
    """Tidy up text so it reads naturally (drop list bullets / stray markdown)."""
    cleaned = re.sub(r"[•·▪◦*]+", " ", text)  # bullets / markdown asterisks
    cleaned = re.sub(r"\s+", " ", cleaned)  # collapse whitespace/newlines
    return cleaned.strip()


async def synth(text: str, voice: str = DEFAULT_VOICE, rate: str = "-4%") -> bytes:
    """Synthesize ``text`` to MP3 bytes with the given neural ``voice``.

    ``rate`` slows delivery slightly (-4%) for a calmer receptionist cadence.
    Raises on network/synthesis failure so the caller can surface a 502.
    """
    spoken = _normalize(text)
    if not spoken:
        return b""

    audio = bytearray()
    communicate = edge_tts.Communicate(spoken, voice, rate=rate)
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            audio += chunk["data"]
    return bytes(audio)
