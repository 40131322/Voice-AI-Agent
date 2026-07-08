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
 * All reasoning stays in the Interaction Agent (Claude via OpenRouter, unchanged).
 * The transcript travels the SAME /api/chat path that typed text uses.
 */

interface Options {
  /** Called with a final recognized utterance; wire this to the chat send handler. */
  onFinalTranscript: (text: string) => void;
}

export interface VoiceInterface {
  supported: boolean;
  voiceEnabled: boolean;
  isListening: boolean;
  isSpeaking: boolean;
  interim: string;
  toggleVoice: () => void;
  /** Speak a piece of assistant text (pauses the mic while speaking). */
  speak: (text: string) => void;
}

export function useVoiceInterface({ onFinalTranscript }: Options): VoiceInterface {
  const [supported, setSupported] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [interim, setInterim] = useState('');

  const recognitionRef = useRef<any>(null);
  const voiceEnabledRef = useRef(false);
  const speakingRef = useRef(false);
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
      let finalText = '';
      let interimText = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) finalText += result[0].transcript;
        else interimText += result[0].transcript;
      }
      setInterim(interimText);
      const trimmed = finalText.trim();
      if (trimmed) {
        setInterim('');
        onFinalRef.current(trimmed);
      }
    };

    rec.onend = () => {
      setIsListening(false);
      // Auto-restart continuous recognition unless we're intentionally paused
      // (voice turned off, or the mic is paused while the assistant speaks).
      if (voiceEnabledRef.current && !speakingRef.current) {
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
    setVoiceEnabled((prev) => {
      const next = !prev;
      voiceEnabledRef.current = next;
      if (next) {
        startListening();
      } else {
        stopListening();
        if (typeof window !== 'undefined') window.speechSynthesis.cancel();
        speakingRef.current = false;
        setIsSpeaking(false);
      }
      return next;
    });
  }, [startListening, stopListening]);

  const speak = useCallback(
    (text: string) => {
      if (typeof window === 'undefined') return;
      const synth = window.speechSynthesis;
      if (!synth || !text.trim()) return;

      // Step 5: pause the mic while speaking so the TTS output is not
      // transcribed back in as caller input (prevents an echo loop).
      speakingRef.current = true;
      setIsSpeaking(true);
      stopListening();
      synth.cancel();

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = 'en-US';
      const finish = () => {
        speakingRef.current = false;
        setIsSpeaking(false);
        // Step 6: resume the mic once we're done speaking.
        if (voiceEnabledRef.current) startListening();
      };
      utterance.onend = finish;
      utterance.onerror = finish;
      synth.speak(utterance);
    },
    [startListening, stopListening],
  );

  return { supported, voiceEnabled, isListening, isSpeaking, interim, toggleVoice, speak };
}
