'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import SettingsModal, { useSettings } from '@/components/SettingsModal';
import { ChatHeader } from '@/components/chat/ChatHeader';
import { ChatInput } from '@/components/chat/ChatInput';
import { ChatMessages } from '@/components/chat/ChatMessages';
import { ErrorBanner } from '@/components/chat/ErrorBanner';
import { useAutoScroll } from '@/components/chat/useAutoScroll';
import { useVoiceInterface } from '@/components/chat/useVoiceInterface';
import type { ChatBubble } from '@/components/chat/types';

const POLL_INTERVAL_MS = 1500;

// Proactive opening greeting, shown before the caller says anything. It doubles
// as the voice onboarding (mic controls live in the browser, so this stays on
// the client) and front-loads the intake so the caller can answer in one go.
const GREETING = [
  "Hi there, thanks for calling Riverside Family Clinic — my name is Ava, and I'll help you get scheduled today.",
  '',
  "Quick heads-up on how this works: press and hold the mic button while you speak, then release when you're done — that's your push-to-talk. I'll stay quiet while you're holding it, and I'll speak back once you let go. (You can also just type in the box below if you'd rather.)",
  '',
  'To get you booked as fast as possible, go ahead and give me everything at once in one go:',
  '• Your full name and date of birth',
  "• Whether you're a new or existing patient",
  '• A good callback number',
  '• Your insurance provider',
  "• And the reason for your visit — what's going on and how you're feeling",
  '',
  "Take your time, and don't worry about the order. Whenever you're ready, hold the mic and tell me all of that — I'll sort out the details and confirm anything I need.",
  '',
  'And if this is a medical emergency, please hang up and call 911 right away.',
].join('\n');

const formatEscapeCharacters = (text: string): string => {
  return text
    .replace(/\\n/g, '\n')
    .replace(/\\t/g, '\t')
    .replace(/\\r/g, '\r')
    .replace(/\\\\/g, '\\');
};

const isRenderableMessage = (entry: any) =>
  typeof entry?.role === 'string' &&
  typeof entry?.content === 'string' &&
  entry.content.trim().length > 0;

const toBubbles = (payload: any): ChatBubble[] => {
  if (!Array.isArray(payload?.messages)) return [];

  return payload.messages
    .filter(isRenderableMessage)
    .map((message: any, index: number) => ({
      id: `history-${index}`,
      role: message.role,
      text: formatEscapeCharacters(message.content),
    }));
};

export default function Page() {
  const { settings, setSettings } = useSettings();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatBubble[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isWaitingForResponse, setIsWaitingForResponse] = useState(false);
  const { scrollContainerRef, handleScroll } = useAutoScroll({
    items: messages,
    isWaiting: isWaitingForResponse,
  });
  const openSettings = useCallback(() => setOpen(true), [setOpen]);
  const closeSettings = useCallback(() => setOpen(false), [setOpen]);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/chat/history', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      setMessages(toBubbles(data));
    } catch (err: any) {
      if (err?.name === 'AbortError') return;
      console.error('Failed to load chat history', err);
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  // Detect and store browser timezone on first load
  useEffect(() => {
    const detectAndStoreTimezone = async () => {
      // Only run if timezone not already stored
      if (settings.timezone) return;
      
      try {
        const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        
        // Send to server
        const response = await fetch('/api/timezone', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ timezone: browserTimezone }),
        });
        
        if (response.ok) {
          // Update local settings
          setSettings({ ...settings, timezone: browserTimezone });
        }
      } catch (error) {
        // Fail silently - timezone detection is not critical
        console.debug('Timezone detection failed:', error);
      }
    };

    void detectAndStoreTimezone();
  }, [settings, setSettings]);


  useEffect(() => {
    const intervalId = window.setInterval(() => {
      void loadHistory();
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [loadHistory]);

  const canSubmit = input.trim().length > 0;
  const inputPlaceholder = 'Type a message…';

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      setError(null);
      setIsWaitingForResponse(true);

      // Optimistically add the user message immediately
      const userMessage: ChatBubble = {
        id: `user-${Date.now()}`,
        role: 'user',
        text: formatEscapeCharacters(trimmed),
      };
      setMessages(prev => {
        const newMessages = [...prev, userMessage];
        return newMessages;
      });

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            messages: [{ role: 'user', content: trimmed }],
          }),
        });

        if (!(res.ok || res.status === 202)) {
          const detail = await res.text();
          throw new Error(detail || `Request failed (${res.status})`);
        }
      } catch (err: any) {
        console.error('Failed to send message', err);
        setError(err?.message || 'Failed to send message');
        // Remove the optimistic message on error
        setMessages(prev => prev.filter(msg => msg.id !== userMessage.id));
        setIsWaitingForResponse(false);
        throw err instanceof Error ? err : new Error('Failed to send message');
      } finally {
        // Poll until we get the assistant's response
        let pollAttempts = 0;
        const maxPollAttempts = 30; // Max 30 attempts (30 seconds)
        
        const pollForAssistantResponse = async () => {
          pollAttempts++;
          
          try {
            const res = await fetch('/api/chat/history', { cache: 'no-store' });
            if (res.ok) {
              const data = await res.json();
              const currentMessages = toBubbles(data);
              
              // Check if the last message is from assistant and contains our user message
              const lastMessage = currentMessages[currentMessages.length - 1];
              const hasUserMessage = currentMessages.some(msg => msg.text === trimmed && msg.role === 'user');
              const hasAssistantResponse = lastMessage?.role === 'assistant' && hasUserMessage;
              
              if (hasAssistantResponse) {
                // We got the assistant response, update messages and stop loading
                setMessages(currentMessages);
                setIsWaitingForResponse(false);
                return;
              }
            }
          } catch (err) {
            console.error('Error polling for response:', err);
          }
          
          // Continue polling if we haven't exceeded max attempts
          if (pollAttempts < maxPollAttempts) {
            setTimeout(pollForAssistantResponse, 1000); // Poll every second
          } else {
            // Timeout - stop loading and update messages anyway
            setIsWaitingForResponse(false);
            await loadHistory();
          }
        };
        
        // Start polling after a brief delay
        setTimeout(pollForAssistantResponse, 1000);
      }
    },
    [loadHistory],
  );

  // Voice interface: a thin client shim over the SAME text send path. It has no
  // tools and creates no agent — it just speaks/recognizes around sendMessage.
  const voice = useVoiceInterface({ onFinalTranscript: sendMessage });

  // Speak newly-arrived assistant replies (TTS). TTS is DECOUPLED from the mic:
  // replies are spoken when the mic is OFF (typed conversation) — with the mic
  // off there is no echo-loop risk, so the previous `voiceEnabled` gate is gone.
  // Voice mode (mic on) still speaks through the same path. `spokenRef` is seeded
  // once on mount so pre-existing history is NOT replayed; only replies that
  // arrive afterward are spoken.
  const spokenRef = useRef<string | null>(null);
  const spokenSeededRef = useRef(false);

  useEffect(() => {
    const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant');
    if (!spokenSeededRef.current) {
      // First run: treat whatever is already on screen as already-spoken.
      spokenSeededRef.current = true;
      spokenRef.current = lastAssistant?.text ?? null;
      return;
    }
    if (lastAssistant && lastAssistant.text !== spokenRef.current) {
      spokenRef.current = lastAssistant.text;
      voice.speak(lastAssistant.text);
    }
  }, [messages, voice]);

  // Latest voice handle, so the mount-only effect below always calls the current
  // speak/stopSpeaking without re-subscribing on every render.
  const voiceRef = useRef(voice);
  voiceRef.current = voice;

  // The greeting is spoken exactly ONCE at the start of the call, and never again.
  // `greetingDoneRef` also gates the autoplay fallback below AND is flipped the
  // instant the caller types or holds the mic (see stopTts) so it can't sneak in
  // after they've started interacting.
  const greetingDoneRef = useRef(false);

  // Immediately stop ALL TTS (greeting or a reply) and make sure the greeting
  // fallback can never fire afterward. Called the moment the caller types or
  // holds the mic — TTS stops no matter what.
  const stopTts = useCallback(() => {
    greetingDoneRef.current = true;
    voiceRef.current.stopSpeaking();
  }, []);

  // Speak the opening greeting once at the very start. Browsers block
  // speechSynthesis before the first user gesture (autoplay policy), so if the
  // immediate attempt is dropped we retry on the first interaction — UNLESS the
  // caller has already typed/held the mic (greetingDoneRef), in which case we
  // stay silent. Mount-only: it must not re-run and re-speak on every render.
  useEffect(() => {
    const synth = typeof window !== 'undefined' ? window.speechSynthesis : null;
    if (!synth) return;

    // Fallback retries on pointerdown ONLY, never keydown: a keystroke means the
    // caller is typing (or Alt+Space push-to-talk), and typing must keep TTS
    // silent — so we never let a key event start the greeting. handleStartHold
    // sets greetingDoneRef before this fires for a mic-button hold, so that's
    // suppressed too; a neutral click still recovers a blocked greeting.
    const onFirstGesture = () => {
      if (!greetingDoneRef.current && !synth.speaking && !synth.pending) {
        voiceRef.current.speak(GREETING);
      }
      greetingDoneRef.current = true;
      detach();
    };
    const detach = () => {
      window.removeEventListener('pointerdown', onFirstGesture);
    };

    voiceRef.current.speak(GREETING); // immediate attempt (may be deferred by autoplay)
    window.addEventListener('pointerdown', onFirstGesture);
    return detach;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The greeting is a pinned, client-only first bubble (it explains browser mic
  // controls, so it never enters the agent's conversation/history). It is shown
  // as text and spoken once on start (see the effect above); typed/voice replies
  // afterward are spoken by the reply-TTS effect.
  const displayMessages = useMemo<ChatBubble[]>(
    () => [{ id: 'greeting-intro', role: 'assistant', text: GREETING }, ...messages],
    [messages],
  );

  const handleClearHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/chat/history', { method: 'DELETE' });
      if (!res.ok) {
        console.error('Failed to clear chat history', res.statusText);
        return;
      }
      setMessages([]);
    } catch (err) {
      console.error('Failed to clear chat history', err);
    }
  }, [setMessages]);

  const triggerClearHistory = useCallback(() => {
    void handleClearHistory();
  }, [handleClearHistory]);

  const handleSubmit = useCallback(async () => {
    if (!canSubmit) return;
    const value = input;
    setInput('');
    try {
      await sendMessage(value);
    } catch {
      setInput(value);
    }
  }, [canSubmit, input, sendMessage, setInput]);

  const handleInputChange = useCallback((value: string) => {
    stopTts(); // the moment the caller types, any TTS (greeting or reply) stops.
    setInput(value);
  }, [setInput, stopTts]);

  // Holding the mic interrupts TTS too (barge-in), then starts push-to-talk.
  const handleStartHold = useCallback(() => {
    stopTts();
    voice.startHold();
  }, [stopTts, voice]);

  const clearError = useCallback(() => setError(null), [setError]);

  return (
    <main className="chat-bg min-h-screen p-4 sm:p-6">
      <div className="chat-wrap flex flex-col">
        <ChatHeader onOpenSettings={openSettings} onClearHistory={triggerClearHistory} />

        <div className="card flex-1 overflow-hidden">
          <ChatMessages
            messages={displayMessages}
            isWaitingForResponse={isWaitingForResponse}
            scrollContainerRef={scrollContainerRef}
            onScroll={handleScroll}
          />

          <div className="border-t border-gray-200 p-3">
            {error && <ErrorBanner message={error} onDismiss={clearError} />}

            <ChatInput
              value={input}
              canSubmit={canSubmit}
              placeholder={inputPlaceholder}
              onChange={handleInputChange}
              onSubmit={handleSubmit}
              voiceSupported={voice.supported}
              voiceEnabled={voice.voiceEnabled}
              isListening={voice.isListening}
              isSpeaking={voice.isSpeaking}
              isHolding={voice.isHolding}
              interim={voice.interim}
              onStartHold={handleStartHold}
              onStopHold={voice.stopHold}
            />
          </div>
        </div>

        <SettingsModal open={open} onClose={closeSettings} settings={settings} onSave={setSettings} />
      </div>
    </main>
  );
}
