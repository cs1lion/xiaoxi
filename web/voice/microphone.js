// Invalidates unresolved browser permission requests without reusing a released stream.
export class MicrophoneLease {
  constructor(acquire) { this.acquire = acquire; this.epoch = 0; this.stream = null; this.pending = null; }
  async open() {
    if (this.stream) return this.stream;
    if (this.pending) return this.pending;
    const epoch = this.epoch;
    const request = (async () => {
      const stream = await this.acquire();
      if (epoch !== this.epoch) { stream.getTracks().forEach(track => track.stop()); return null; }
      this.stream = stream;
      return stream;
    })();
    this.pending = request;
    try { return await request; }
    finally { if (this.pending === request) this.pending = null; }
  }
  close() {
    this.epoch++;
    this.pending = null;
    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
  }
}
