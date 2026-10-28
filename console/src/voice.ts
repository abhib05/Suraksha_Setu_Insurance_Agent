// Push-to-talk recording for the chat. The browser records in whatever format it likes
// (usually Opus); Muse Voice Transcribe wants mono 16-bit PCM WAV at 16 or 24 kHz, so the
// clip is decoded and re-encoded here before it is sent. Nothing is uploaded until the
// family stops recording, and the server does not keep the audio.

const RATE = 16000

export type Recorder = { stop: () => Promise<Blob> }

export async function startRecording(): Promise<Recorder> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  const rec = new MediaRecorder(stream)
  const chunks: Blob[] = []
  rec.ondataavailable = (e) => chunks.push(e.data)
  rec.start()
  return {
    stop: () =>
      new Promise((resolve, reject) => {
        rec.onstop = async () => {
          stream.getTracks().forEach((t) => t.stop())
          try {
            resolve(await toWav(new Blob(chunks, { type: rec.mimeType })))
          } catch (e) {
            reject(e)
          }
        }
        rec.stop()
      }),
  }
}

export async function toWav(clip: Blob): Promise<Blob> {
  const ctx = new AudioContext()
  const decoded = await ctx.decodeAudioData(await clip.arrayBuffer())
  await ctx.close()
  const frames = Math.ceil(decoded.duration * RATE)
  const offline = new OfflineAudioContext(1, Math.max(frames, 1), RATE)
  const src = offline.createBufferSource()
  src.buffer = decoded // mixed down to mono by the 1-channel context
  src.connect(offline.destination)
  src.start()
  const pcm = (await offline.startRendering()).getChannelData(0)

  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2))
  const text = (at: number, s: string) => [...s].forEach((c, i) => out.setUint8(at + i, c.charCodeAt(0)))
  text(0, 'RIFF')
  out.setUint32(4, 36 + pcm.length * 2, true)
  text(8, 'WAVE')
  text(12, 'fmt ')
  out.setUint32(16, 16, true) // PCM header size
  out.setUint16(20, 1, true) // PCM
  out.setUint16(22, 1, true) // mono
  out.setUint32(24, RATE, true)
  out.setUint32(28, RATE * 2, true) // bytes per second
  out.setUint16(32, 2, true) // bytes per frame
  out.setUint16(34, 16, true) // bits per sample
  text(36, 'data')
  out.setUint32(40, pcm.length * 2, true)
  pcm.forEach((v, i) => out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, v)) * 0x7fff, true))
  return new Blob([out], { type: 'audio/wav' })
}
