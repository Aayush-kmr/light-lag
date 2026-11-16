import { roomUrl } from './ll.js';
// Voice notes: record in the browser, upload, get a radio-filtered clip and a
// local whisper.cpp transcript back. Playback uses the radio version.
// TTS for TERN: Piper on the server if installed, else the browser's
// speechSynthesis (local voices; no cloud).
export class Recorder {
  constructor() { this.rec = null; this.chunks = []; }
  get supported() { return !!(navigator.mediaDevices && window.MediaRecorder); }
  async start() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.rec = new MediaRecorder(stream); this.chunks = [];
    this.rec.ondataavailable = (e) => this.chunks.push(e.data);
    this.rec.start(); this.t0 = performance.now();
  }
  async stop() {
    if (!this.rec) return null;
    const done = new Promise((r) => (this.rec.onstop = r));
    this.rec.stop(); await done;
    this.rec.stream.getTracks().forEach((t) => t.stop());
    const blob = new Blob(this.chunks, { type: this.rec.mimeType || 'audio/webm' });
    this.rec = null;
    const fd = new FormData(); fd.append('file', blob, 'note.webm');
    const r = await fetch(roomUrl('/api/voice'), { method: 'POST', body: fd });
    return await r.json(); // {id, transcript, seconds, bytes, stt}
  }
}

export function playAudio(id) {
  if (!id) return;
  new Audio(roomUrl(`/api/audio/${id}`)).play().catch(() => {});
}

let voices = null;
export function speak(text, { rate = 1.02, pitch = 0.9, who = 'tern' } = {}) {
  if (!('speechSynthesis' in window) || !text) return;
  voices = voices || speechSynthesis.getVoices();
  const u = new SpeechSynthesisUtterance(text.replace(/\bOver\.?$/, 'Over.'));
  const local = voices.filter((v) => v.localService && v.lang.startsWith('en'));
  if (local.length) u.voice = local[who === 'tern' ? 0 : Math.min(1, local.length - 1)];
  u.rate = rate; u.pitch = pitch;
  speechSynthesis.speak(u);
}
