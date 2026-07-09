'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Voice INTERFACE — a thin browser client shim, NOT an agent.
 *
 * It has no tools, no roster entry, and no reasoning. Its entire job is the
 * six-step client loop from the design:
 *   1. speech-to-text (mic -> transcript)        [browser SpeechRecognition]
 *   2. send transcript to the Interaction Agent  [onFinalTranscript -> /api/chat]
 *   3. receive the Interaction Agent's text reply [caller speaks() it]
 *   4. text-to-speech (reply -> speaker)          [server edge-tts MP3 -> <audio>]
 *   5. pause mic while speaking                   [speak() stops recognition]
 *   6. resume mic after speaking                  [audio 'ended' restarts it]
 *
 * OUTPUT is server-side: reply text is POSTed to /api/tts, which returns MP3
 * (edge-tts, the "Ava" neural voice), played through a single HTMLAudioElement.
 * Only the INPUT half uses the browser (Web Speech SpeechRecognition), so the
 * feature is "supported" whenever SpeechRecognition exists.
 *
 * Two ways to talk:
 *   - Hold the mic button           -> push-to-talk (listens while held, sends on release)
 *   - Hold Alt/Option + Space        -> same push-to-talk via keyboard
 * Holding also interrupts any in-progress reply audio (barge-in).
 *
 * All reasoning stays in the Interaction Agent (Claude via OpenRouter, unchanged).
 */

/**
 * After reply audio finishes, ignore recognition results for this long so the
 * tail of the assistant's own audio (speaker latency / room echo) is not
 * transcribed back in.
 */
const POST_SPEECH_COOLDOWN_MS = 600;

interface Options {
  /** Called with a final recognized utterance; wire this to the chat send handler. */
  onFinalTranscript: (text: string) => void;
}

export interface VoiceInterface {
  supported: boolean;
  voiceEnabled: boolean;
  isListening: boolean;
  isSpeaking: boolean;
  /** True while push-to-talk is held (mic button held, or Alt/Option+Space). */
  isHolding: boolean;
  interim: string;
  toggleVoice: () => void;
  /** Begin push-to-talk (wire to the mic button's pointer-down). */
  startHold: () => void;
  /** End push-to-talk and send what was captured (wire to pointer-up/cancel). */
  stopHold: () => void;
  /** Speak a piece of assistant text (pauses the mic while speaking). */
  speak: (text: string) => void;
  /** Immediately halt any reply audio and clear the queue (barge-in / interrupt). */
  stopSpeaking: () => void;
}

export function useVoiceInterface({ onFinalTranscript }: Options): VoiceInterface {
  const [supported, setSupported] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isHolding, setIsHolding] = useState(false);
  const [interim, setInterim] = useState('');

  const recognitionRef = useRef<any>(null);
  const voiceEnabledRef = useRef(false);
  const speakingRef = useRef(false);
  // Hold-to-talk: holdModeRef => mic listens only while held (no continuous
  // auto-restart when idle); holdingRef => the button/chord is down now.
  const holdModeRef = useRef(false);
  const holdingRef = useRef(false);
  // Queue of pending reply texts (the agent emits several messages per turn).
  const speakQueueRef = useRef<string[]>([]);
  // Single reused audio element + the object URL currently loaded into it.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const currentUrlRef = useRef<string | null>(null);
  // Bumped on every barge-in/stop so in-flight fetch/playback callbacks bail.
  const playTokenRef = useRef(0);
  // Wall-clock time before which recognition results are dropped (echo cooldown).
  const ignoreResultsUntilRef = useRef(0);
  const onFinalRef = useRef(onFinalTranscript);
  onFinalRef.current = onFinalTranscript;

  const getAudio = (): HTMLAudioElement | null => {
    if (typeof window === 'undefined') return null;
    if (!audioRef.current) audioRef.current = new Audio();
    return audioRef.current;
  };

  const revokeUrl = () => {
    if (currentUrlRef.current) {
      try {
        URL.revokeObjectURL(currentUrlRef.current);
      } catch {
        // ignore
      }
      currentUrlRef.current = null;
    }
  };

  const startListening = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec || speakingRef.current) return;
    try {
      rec.start();
      setIsListening(true);
    } catch {
      // start() throws if already started — safe to ignore.
    }
  }, []);

  const stopListening = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec) return;
    try {
      rec.stop();
    } catch {
      // ignore
    }
    setIsListening(false);
  }, []);

  // Immediately halt reply audio and clear the queue. Bumps the play token so any
  // in-flight /api/tts fetch or queued playback callback bails out instead of
  // playing stale audio after the caller has barged in.
  const stopSpeaking = useCallback(() => {
    playTokenRef.current += 1;
    speakQueueRef.current = [];
    const audio = audioRef.current;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      try {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
      } catch {
        // ignore
      }
    }
    revokeUrl();
    speakingRef.current = false;
    setIsSpeaking(false);
  }, []);

  // Build the recognition object once (client only). TTS is server-side, so the
  // feature only needs SpeechRecognition for input.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setSupported(false);
      return;
    }
    setSupported(true);

    const rec = new SR();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';

    rec.onresult = (event: any) => {
      // Echo guard: never treat audio captured while the assistant is speaking
      // (or in the brief cooldown right after) as caller input. Without this, the
      // reply audio — or its tail flushed by rec.stop() — loops back in as a new
      // "user" message and the agent starts talking to itself. Also drop results
      // when voice is off (defensive: recognition should already be stopped).
      if (!voiceEnabledRef.current || speakingRef.current || Date.now() < ignoreResultsUntilRef.current) {
        setInterim('');
        return;
      }
      let finalText = '';
      let interimText = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) finalText += result[0].transcript;
        else interimText += result[0].transcript;
      }
      setInterim(interimText);
      const trimmed = finalText.trim();
      if (trimmed.length > 0) {
        setInterim('');
        onFinalRef.current(trimmed);
      }
    };

    rec.onend = () => {
      setIsListening(false);
      // Keep listening only when: voice is on, we're not speaking, and either
      // we're in continuous mode OR push-to-talk is still held. In hold-mode with
      // the button/chord released, we intentionally stay idle.
      const wantListen =
        voiceEnabledRef.current &&
        !speakingRef.current &&
        (!holdModeRef.current || holdingRef.current);
      if (wantListen) {
        try {
          rec.start();
          setIsListening(true);
        } catch {
          // ignore
        }
      }
    };

    rec.onerror = () => {
      setIsListening(false);
    };

    recognitionRef.current = rec;

    return () => {
      try {
        rec.stop();
      } catch {
        // ignore
      }
      const audio = audioRef.current;
      if (audio) {
        try {
          audio.pause();
        } catch {
          // ignore
        }
      }
      revokeUrl();
    };
  }, []);

  const toggleVoice = useCallback(() => {
    // Barge-in by tap: while the assistant is speaking, a mic tap means "stop
    // talking and listen to me now" — cancel the audio and open the mic — rather
    // than turning voice off.
    if (speakingRef.current && voiceEnabledRef.current) {
      stopSpeaking();
      ignoreResultsUntilRef.current = 0; // don't suppress the words you're about to say
      startListening();
      return;
    }

    setVoiceEnabled((prev) => {
      const next = !prev;
      voiceEnabledRef.current = next;
      if (next) {
        holdModeRef.current = false; // the button means continuous listening
        startListening();
      } else {
        holdModeRef.current = false;
        holdingRef.current = false;
        setIsHolding(false);
        stopListening();
        stopSpeaking();
      }
      return next;
    });
  }, [startListening, stopListening, stopSpeaking]);

  // Speak the next queued reply: fetch its MP3 from the backend and play it.
  // When the queue drains, arm the echo cooldown and resume the mic (Step 6).
  const drainSpeakQueue = useCallback(async () => {
    const next = speakQueueRef.current.shift();

    if (next === undefined) {
      speakingRef.current = false;
      setIsSpeaking(false);
      ignoreResultsUntilRef.current = Date.now() + POST_SPEECH_COOLDOWN_MS;
      if (voiceEnabledRef.current && (!holdModeRef.current || holdingRef.current)) {
        startListening();
      }
      return;
    }

    const token = playTokenRef.current;
    let advanced = false;
    const advance = () => {
      if (advanced) return; // one terminal event per chunk (ended XOR error XOR play-reject)
      advanced = true;
      if (token === playTokenRef.current) void drainSpeakQueue();
    };

    try {
      const res = await fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: next }),
      });
      if (token !== playTokenRef.current) return; // barged-in during fetch
      if (!res.ok) throw new Error(`tts ${res.status}`);
      const blob = await res.blob();
      if (token !== playTokenRef.current) return;

      const audio = getAudio();
      if (!audio) {
        advance();
        return;
      }
      revokeUrl();
      const url = URL.createObjectURL(blob);
      currentUrlRef.current = url;
      audio.src = url;
      audio.onended = advance;
      audio.onerror = advance;
      try {
        await audio.play();
      } catch (e) {
        // Autoplay can be blocked before a user gesture; holding the mic is a
        // gesture, so replies normally play fine. Log and continue on failure.
        // eslint-disable-next-line no-console
        console.warn('[tts] audio play failed:', e);
        advance();
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[tts] synthesis failed:', e);
      advance();
    }
  }, [startListening]);

  const speak = useCallback(
    (text: string) => {
      if (!text.trim()) return;

      // Queue this reply. The agent emits several messages per turn; queueing
      // (rather than interrupting) keeps the mic paused for the WHOLE spoken
      // sequence and plays every message in order.
      speakQueueRef.current.push(text.trim());
      if (speakingRef.current) return; // already draining — this will follow.

      // Step 5: pause the mic for the entire speaking window so the reply audio
      // is not transcribed back in as caller input (prevents an echo loop).
      speakingRef.current = true;
      setIsSpeaking(true);
      stopListening();
      void drainSpeakQueue();
    },
    [drainSpeakQueue, stopListening],
  );

  // --- Push-to-talk (mic button hold, or Alt/Option + Space) ----------------
  const startPushToTalk = useCallback(() => {
    if (holdingRef.current) return; // ignore key auto-repeat / re-entry
    holdingRef.current = true;
    setIsHolding(true);
    holdModeRef.current = true;
    // Enable voice output so the reply is spoken, but in hold-mode the mic only
    // listens while held (onend won't auto-restart once released).
    voiceEnabledRef.current = true;
    setVoiceEnabled(true);
    if (speakingRef.current) stopSpeaking(); // barge-in: stop any current reply audio
    ignoreResultsUntilRef.current = 0; // don't suppress the words about to be said
    startListening();
  }, [startListening, stopSpeaking]);

  const stopPushToTalk = useCallback(() => {
    if (!holdingRef.current) return;
    holdingRef.current = false;
    setIsHolding(false);
    // Stop the mic: audio captured while held finalizes into onresult and is sent.
    // onend won't restart (hold-mode + released), so the mic goes idle.
    stopListening();
  }, [stopListening]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const isChord = (e: KeyboardEvent) => e.code === 'Space' && e.altKey;

    const onKeyDown = (e: KeyboardEvent) => {
      if (!isChord(e)) return;
      // Prevent the OS/browser default (Option+Space inserting a non-breaking
      // space in the text input) and start listening.
      e.preventDefault();
      if (e.repeat) return;
      startPushToTalk();
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (holdingRef.current && (e.code === 'Space' || e.key === 'Alt')) {
        e.preventDefault();
        stopPushToTalk();
      }
    };
    // Safety: if focus leaves the window mid-hold, keyup may never arrive.
    const onBlur = () => {
      if (holdingRef.current) stopPushToTalk();
    };

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    };
  }, [startPushToTalk, stopPushToTalk]);

  return {
    supported,
    voiceEnabled,
    isListening,
    isSpeaking,
    isHolding,
    interim,
    toggleVoice,
    startHold: startPushToTalk,
    stopHold: stopPushToTalk,
    speak,
    stopSpeaking,
  };
}
