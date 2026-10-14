// Streaming area resampler: every input sample contributes, including across callbacks.
export class StreamingResampler {
  constructor(inputRate, outputRate = 16000) {
    if (!(inputRate > 0 && outputRate > 0)) throw new Error('Invalid sample rate');
    this.ratio = inputRate / outputRate;
    this.weight = 0;
    this.sum = 0;
  }
  push(input) {
    const output = [];
    for (const sample of input) {
      let remaining = 1;
      while (remaining > 1e-10) {
        const take = Math.min(remaining, this.ratio - this.weight);
        this.sum += sample * take;
        this.weight += take;
        remaining -= take;
        if (this.weight >= this.ratio - 1e-10) {
          output.push(this.sum / this.ratio);
          this.weight = 0;
          this.sum = 0;
        }
      }
    }
    return Float32Array.from(output);
  }
  flush() {
    const out = this.weight > 1e-10 ? Float32Array.of(this.sum / this.weight) : new Float32Array();
    this.weight = this.sum = 0;
    return out;
  }
}

export function pcm16(samples) {
  const result = new ArrayBuffer(samples.length * 2);
  const view = new DataView(result);
  samples.forEach((value, i) => {
    const s = Math.max(-1, Math.min(1, value));
    view.setInt16(i * 2, s < 0 ? s * 32768 : s * 32767, true);
  });
  return result;
}

export class SampleFramer {
  constructor(size = 320) { this.size = size; this.pending = new Float32Array(); }
  push(samples) {
    const joined = new Float32Array(this.pending.length + samples.length);
    joined.set(this.pending); joined.set(samples, this.pending.length);
    const frames = [];
    let offset = 0;
    for (; offset + this.size <= joined.length; offset += this.size) frames.push(joined.slice(offset, offset + this.size));
    this.pending = joined.slice(offset);
    return frames;
  }
  flush() { const tail = this.pending; this.pending = new Float32Array(); return tail; }
}
