"""Server-side text-to-speech (output only).

Turns the interaction agent's reply text into MP3 audio via edge-tts. Speech
recognition (STT) stays in the browser; this module is only the TTS half.
"""

from .edge import DEFAULT_VOICE, synth

__all__ = ["synth", "DEFAULT_VOICE"]
