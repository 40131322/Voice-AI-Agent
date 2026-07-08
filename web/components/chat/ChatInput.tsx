import { FormEvent } from 'react';

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
  interim?: string;
  onToggleVoice?: () => void;
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
  interim = '',
  onToggleVoice,
}: ChatInputProps) {
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;
    void onSubmit();
  };

  const voiceStatus = voiceEnabled
    ? isSpeaking
      ? 'Speaking…'
      : interim
        ? `“${interim}”`
        : isListening
          ? 'Listening…'
          : 'Voice on'
    : '';

  return (
    <div className="flex flex-col gap-1">
      <form className="flex items-center gap-2" onSubmit={handleSubmit}>
        {voiceSupported && (
          <button
            type="button"
            onClick={onToggleVoice}
            aria-pressed={voiceEnabled}
            aria-label={voiceEnabled ? 'Turn voice off' : 'Turn voice on'}
            title={voiceEnabled ? 'Turn voice off' : 'Turn voice on'}
            className={`rounded-md border px-3 py-2 text-sm transition-colors ${
              voiceEnabled
                ? isListening
                  ? 'border-red-300 bg-red-50 text-red-600 animate-pulse'
                  : 'border-blue-300 bg-blue-50 text-blue-600'
                : 'border-gray-200 hover:bg-gray-50'
            }`}
          >
            {voiceEnabled ? '🎙️' : '🎤'}
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
      {voiceSupported && voiceEnabled && (
        <span className="px-1 text-xs text-gray-500">{voiceStatus}</span>
      )}
    </div>
  );
}
