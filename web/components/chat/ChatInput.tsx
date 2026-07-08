import { FormEvent, PointerEvent as ReactPointerEvent } from 'react';

interface ChatInputProps {
  value: string;
  canSubmit: boolean;
  placeholder: string;
  onChange: (value: string) => void;
  onSubmit: () => Promise<void> | void;
  // Voice interface (optional — omitted when the browser has no Web Speech API).
  voiceSupported?: boolean;
  voiceEnabled?: boolean;
  isListening?: boolean;
  isSpeaking?: boolean;
  isHolding?: boolean;
  interim?: string;
  // Push-to-talk: hold the mic button to talk, release to send.
  onStartHold?: () => void;
  onStopHold?: () => void;
}

export function ChatInput({
  value,
  canSubmit,
  placeholder,
  onChange,
  onSubmit,
  voiceSupported = false,
  voiceEnabled = false,
  isListening = false,
  isSpeaking = false,
  isHolding = false,
  interim = '',
  onStartHold,
  onStopHold,
}: ChatInputProps) {
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;
    void onSubmit();
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    // Capture the pointer so release is detected even if the cursor/finger
    // drifts off the button while held.
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // ignore — capture is best-effort
    }
    onStartHold?.();
  };

  const handlePointerUp = () => {
    onStopHold?.();
  };

  const voiceStatus = isHolding
    ? 'Listening… release to send'
    : isSpeaking
      ? 'Speaking… (hold the mic to interrupt)'
      : interim
        ? `“${interim}”`
        : 'Hold the mic button to talk';

  return (
    <div className="flex flex-col gap-1">
      <form className="flex items-center gap-2" onSubmit={handleSubmit}>
        {voiceSupported && (
          <button
            type="button"
            onPointerDown={handlePointerDown}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
            onLostPointerCapture={handlePointerUp}
            aria-pressed={isHolding}
            aria-label="Hold to talk"
            title="Hold to talk (or hold ⌥Space / Alt+Space)"
            style={{ touchAction: 'none' }}
            className={`select-none rounded-md border px-3 py-2 text-sm transition-colors ${
              isHolding
                ? 'border-red-300 bg-red-50 text-red-600 animate-pulse'
                : isSpeaking
                  ? 'border-blue-300 bg-blue-50 text-blue-600'
                  : 'border-gray-200 hover:bg-gray-50'
            }`}
          >
            {isHolding ? '🎙️' : '🎤'}
          </button>
        )}
        <input
          className="input"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
        />
        <button type="submit" className="btn" disabled={!canSubmit}>
          Send
        </button>
      </form>
      {voiceSupported && (
        <span className="px-1 text-xs text-gray-500">{voiceStatus}</span>
      )}
    </div>
  );
}
