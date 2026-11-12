// Captures mic audio, downsamples to 16 kHz mono int16, posts ~64 ms chunks.
// Each output sample is the AVERAGE of the input samples it covers (a simple low-pass).
// Picking every 3rd sample instead folds high frequencies into the speech band and hurts recognition.
class MicWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.acc = 0; this.count = 0; this.phase = 0;
    this.out = new Int16Array(1024);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += ch[i]; this.count++; this.phase += 1;
      if (this.phase >= this.ratio) {
        this.phase -= this.ratio;
        const s = Math.max(-1, Math.min(1, this.acc / this.count));
        this.acc = 0; this.count = 0;
        this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        if (this.n === this.out.length) { this.port.postMessage(this.out.buffer.slice(0)); this.n = 0; }
      }
    }
    return true;
  }
}
registerProcessor("mic-worklet", MicWorklet);
