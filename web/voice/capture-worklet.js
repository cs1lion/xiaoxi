// The processor never routes captured sound to its output. Each input is averaged
// to mono, and both lanes share one sample cursor and one audio-clock timestamp.
class DuplexCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frameSize = Math.max(1, Math.round(sampleRate * .02));
    this.microphone = new Float32Array(this.frameSize);
    this.reference = new Float32Array(this.frameSize);
    this.used = 0;
    this.running = true;
    this.port.onmessage = event => {
      if (event.data?.type === 'stop') {
        // Cancellation discards the unposted (<20ms) tail, never pads or emits it.
        this.running = false;
        this.used = 0;
        this.port.onmessage = null;
      }
    };
  }
  process(inputs, outputs) {
    for (const output of outputs) for (const channel of output) channel.fill(0);
    if (!this.running) return false;
    const count = outputs[0]?.[0]?.length || inputs[0]?.[0]?.length || inputs[1]?.[0]?.length || 128;
    const mic = inputs[0] || [], ref = inputs[1] || [];
    for (let i = 0; i < count; i++) {
      if (!this.used) this.audioTime = currentTime + i / sampleRate;
      let m = 0, r = 0;
      for (const channel of mic) m += channel[i] || 0;
      for (const channel of ref) r += channel[i] || 0;
      this.microphone[this.used] = mic.length ? m / mic.length : 0;
      this.reference[this.used] = ref.length ? r / ref.length : 0;
      if (++this.used === this.frameSize) {
        this.port.postMessage({ microphone: this.microphone, reference: this.reference,
          sampleRate, audioTime: this.audioTime }, [this.microphone.buffer, this.reference.buffer]);
        this.microphone = new Float32Array(this.frameSize);
        this.reference = new Float32Array(this.frameSize);
        this.used = 0;
      }
    }
    return true;
  }
}
registerProcessor('neurosama-duplex-capture', DuplexCaptureProcessor);
