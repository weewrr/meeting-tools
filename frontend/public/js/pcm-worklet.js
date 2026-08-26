/**
 * PCM 采集处理器：混合音频每累积 CHUNK 秒向主线程抛出一段 PCM
 * 用于实时转写切片（可独立解码的 WAV）
 */
const CHUNK_SECONDS = 20;

class PcmCollector extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = [];
    this._len = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      this._buffer.push(new Float32Array(input[0]));
      this._len += input[0].length;
      if (this._len >= CHUNK_SECONDS * sampleRate) {
        const merged = new Float32Array(this._len);
        let off = 0;
        for (const b of this._buffer) {
          merged.set(b, off);
          off += b.length;
        }
        this.port.postMessage(
          { pcm: merged, sampleRate, duration: this._len / sampleRate },
          [merged.buffer]
        );
        this._buffer = [];
        this._len = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-collector', PcmCollector);
