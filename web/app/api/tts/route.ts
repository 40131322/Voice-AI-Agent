export const runtime = 'nodejs';

// Proxy the browser's TTS request to the Python backend, returning MP3 audio.
export async function POST(req: Request) {
  let body: any;
  try {
    body = await req.json();
  } catch (e) {
    return new Response('Invalid JSON', { status: 400 });
  }

  const text = body?.text;
  if (typeof text !== 'string' || !text.trim()) {
    return new Response('Missing text', { status: 400 });
  }

  const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
  const url = `${serverBase.replace(/\/$/, '')}/api/v1/tts`;

  try {
    const upstream = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice: body?.voice }),
    });
    if (!upstream.ok) {
      const detail = await upstream.text();
      return new Response(detail || 'TTS upstream error', { status: upstream.status });
    }
    const audio = await upstream.arrayBuffer();
    return new Response(audio, { status: 200, headers: { 'Content-Type': 'audio/mpeg' } });
  } catch (e: any) {
    console.error('[tts-proxy] upstream error', e);
    return new Response(e?.message || 'Upstream error', { status: 502 });
  }
}
