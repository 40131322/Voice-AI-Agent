'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Voice INTERFACE — a thin browser client shim, NOT an agent.
 *
 * It has no tools, no roster entry, and no reasoning. Its entire job is the
 * six-step client loop from the design:
 *   1. speech-to-text (mic -> transcript)              [SpeechRecognition]
 *   2. send transcript to the Interaction Agent        [onFinalTranscript -> existing /chat path]
 *   3. receive the Interaction Agent's text response    [caller speaks() the reply]
 *   4. text-to-speech (response -> speaker)            [SpeechSynthesisUtterance]
 *   5. pause mic while speaking                         [speak() stops recognition]
 *   6. resume mic after speaking                        [utterance.onend restarts it]
 *
 * Two ways to activate the mic:
 *   - Click the mic button           -> CONTINUOUS mode (listens until toggled off)
 *   - Hold Alt/Option + Space        -> HOLD-TO-TALK (push-to-talk; listens only
 *                                       while held, sends on release)
 * Alt+Space is chosen because it never blocks typing a normal space and is easy
 * to hit in Chrome. Holding it also interrupts any in-progress TTS (barge-in).
 *
 * All reasoning stays in the Interaction Agent (Claude via OpenRouter, unchanged).
 * The transcript travels the SAME /api/chat path that typed text uses.
 */

/**
 * After TTS finishes, ignore recognition results for this long so the tail of the
 * assistant's own audio (speaker latency / room echo) is not transcribed back in.
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
  /** Immediately halt any TTS and clear the queue (barge-in / interrupt). */
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
  // Hold-to-talk: holdModeRef => mic listens only while the chord is held (no
  // continuous auto-restart when idle); holdingRef => the chord is down now.
  const holdModeRef = useRef(false);
  const holdingRef = useRef(false);
  // Queue of pending TTS chunks (the agent sends several messages per turn).
  const speakQueueRef = useRef<string[]>([]);
  // The utterance currently being spoken — kept so a barge-in can detach its
  // onend/onerror before cancelling (otherwise the cancel re-arms the cooldown).
  const currentUtteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  // Wall-clock time before which recognition results are dropped (echo cooldown).
  const ignoreResultsUntilRef = useRef(0);
  const onFinalRef = useRef(onFinalTranscript);
  onFinalRef.current = onFinalTranscript;

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

  // Immediately halt TTS and clear the queue. Detaches the current utterance's
  // handlers first so the synth.cancel() below can't fire drainSpeakQueue and
  // re-arm the post-speech cooldown (which would swallow the caller's next words).
  const stopSpeaking = useCallback(() => {
    const utterance = currentUtteranceRef.current;
    if (utterance) {
      utterance.onend = null;
      utterance.onerror = null;
    }
    currentUtteranceRef.current = null;
    speakQueueRef.current = [];
    if (typeof window !== 'undefined') window.speechSynthesis.cancel();
    speakingRef.current = false;
    setIsSpeaking(false);
  }, []);

  // Build the recognition object once (client only).
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    const synth = window.speechSynthesis;
    if (!SR || !synth) {
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
      // TTS output — or its tail flushed by rec.stop() — loops back in as a new
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
      // Send only when all four hold: voice on, not speaking, past the cooldown
      // (all checked above), and a non-empty transcript.
      const trimmed = finalText.trim();
      if (trimmed.length > 0) {
        setInterim('');
        onFinalRef.current(trimmed);
      }
    };

    rec.onend = () => {
      setIsListening(false);
      // Keep listening only when: voice is on, we're not speaking, and either
      // we're in continuous mode OR the push-to-talk chord is still held. In
      // hold-mode with the chord released, we intentionally stay idle.
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
      window.speechSynthesis.cancel();
    };
  }, []);

  const toggleVoice = useCallback(() => {
    // Barge-in by tap: while the assistant is speaking, a mic tap means "stop
    // talking and listen to me now" — cancel the TTS and open the mic — rather
    // than turning voice off. (The mic is paused during TTS, so this is how the
    // caller interrupts; no echo risk from a live mic.)
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

  // Speak the next queued chunk; when the queue drains, resume the mic.
  const drainSpeakQueue = useCallback(() => {
    if (typeof window === 'undefined') return;
    const synth = window.speechSynthesis;
    const next = speakQueueRef.current.shift();

    if (next === undefined) {
      // Nothing left to say — end the speaking window, arm the echo cooldown,
      // then resume the mic (Step 6). The cooldown swallows the TTS tail.
      currentUtteranceRef.current = null;
      speakingRef.current = false;
      setIsSpeaking(false);
      ignoreResultsUntilRef.current = Date.now() + POST_SPEECH_COOLDOWN_MS;
      // Resume only in continuous mode (or while the chord is still held); in
      // hold-mode released, stay idle until the next Alt+Space press.
      if (voiceEnabledRef.current && (!holdModeRef.current || holdingRef.current)) {
        startListening();
      }
      return;
    }

    const utterance = new SpeechSynthesisUtterance(next);
    utterance.lang = 'en-US';
    // Chain to the next chunk. We never call synth.cancel() between chunks, so
    // no stray onend from an aborted utterance can resume the mic mid-speech.
    utterance.onend = drainSpeakQueue;
    utterance.onerror = drainSpeakQueue;
    currentUtteranceRef.current = utterance;
    synth.speak(utterance);
  }, [startListening]);

  const speak = useCallback(
    (text: string) => {
      if (typeof window === 'undefined') return;
      const synth = window.speechSynthesis;
      if (!synth || !text.trim()) return;

      // Queue this chunk. The agent emits several messages per turn; queueing
      // (rather than cancelling each time) keeps the mic paused for the WHOLE
      // spoken sequence and speaks every message in order.
      speakQueueRef.current.push(text.trim());
      if (speakingRef.current) return; // already draining — the new chunk will follow.

      // Step 5: pause the mic for the entire speaking window so the TTS output
      // is not transcribed back in as caller input (prevents an echo loop).
      speakingRef.current = true;
      setIsSpeaking(true);
      stopListening();
      drainSpeakQueue();
    },
    [drainSpeakQueue, stopListening],
  );

  // --- Push-to-talk (hold Alt/Option + Space) -------------------------------
  const startPushToTalk = useCallback(() => {
    if (holdingRef.current) return; // ignore key auto-repeat
    holdingRef.current = true;
    setIsHolding(true);
    holdModeRef.current = true;
    // Enable voice output so the reply is spoken, but in hold-mode the mic only
    // listens while the chord is down (onend won't auto-restart once released).
    voiceEnabledRef.current = true;
    setVoiceEnabled(true);
    if (speakingRef.current) stopSpeaking(); // barge-in: stop any current TTS
    ignoreResultsUntilRef.current = 0; // don't suppress the words about to be said
    startListening();
  }, [startListening, stopSpeaking]);

  const stopPushToTalk = useCallback(() => {
    if (!holdingRef.current) return;
    holdingRef.current = false;
    setIsHolding(false);
    // Stop the mic: the audio captured while held finalizes into onresult and is
    // sent. onend won't restart (hold-mode + chord released), so the mic goes idle.
    stopListening();
  }, [stopListening]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const isChord = (e: KeyboardEvent) => e.code === 'Space' && e.altKey;

    const onKeyDown = (e: KeyboardEvent) => {
      if (!isChord(e)) return;
      // Prevent the OS/browser default (e.g. Option+Space inserting a
      // non-breaking space in the text input) and start listening.
      e.preventDefault();
      if (e.repeat) return;
      startPushToTalk();
    };
    const onKeyUp = (e: KeyboardEvent) => {
      // End on release of either key in the chord.
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
