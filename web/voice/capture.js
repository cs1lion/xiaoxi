const modules = new WeakMap();

function loadModule(context) {
  let pending = modules.get(context);
  if (!pending) {
    pending = Promise.resolve().then(() => context.audioWorklet.addModule(
      new URL('./capture-worklet.js', import.meta.url)));
    modules.set(context, pending);
    pending.catch(() => { if (modules.get(context) === pending) modules.delete(context); });
  }
  return pending;
}

/**
 * Local duplex capture. audioTime uses AudioContext seconds for a chunk start:
 * worklet_input_clock comes from the input render quantum's processing timeline;
 * script_callback_estimate subtracts input-buffer duration from callback currentTime.
 * Neither is a physical microphone sampling timestamp. ScriptProcessor playbackTime
 * belongs to its output buffer and is deliberately never used for input timing.
 * Chunks are round(sampleRate * .02) samples; reference is zero when unavailable.
 * kind: 'worklet' | 'script'. timingSource identifies the two proxies above.
 * hasReference describes a connected reference lane.
 * stop() is synchronous cancellation: discard the pending <20ms tail and queued
 * messages, with no final callback. While running, every sample is carried across
 * render/callback boundaries, without padding. Stream tracks belong to the caller.
 * Worklet setup failure transparently falls back; total setup failure rejects.
 * Runtime/callback errors stop capture and call onError (if supplied).
 */
export async function createCapture({ context, stream, referenceNode = null, onChunk, onError }) {
  if (!context || !stream || typeof onChunk !== 'function') throw new TypeError('Capture requires context, stream and onChunk');

  function setup(kind) {
    const owned = [];
    let processor, referenceTarget, stopped = false, used = 0;
    const own = node => { owned.push(node); return node; };
    const stop = () => {
      if (stopped) return;
      stopped = true;
      used = 0;
      if (processor) {
        processor.onaudioprocess = null;
        processor.onprocessorerror = null;
        if (processor.port) {
          processor.port.onmessage = null;
          processor.port.onmessageerror = null;
          try { processor.port.postMessage({ type: 'stop' }); } catch (_) {}
          try { processor.port.close(); } catch (_) {}
        }
      }
      // Never disconnect all outputs on the caller's playback GainNode.
      if (referenceTarget) {
        try { referenceNode.disconnect(referenceTarget, 0, 1); } catch (_) {}
      }
      for (const node of owned) { try { node.disconnect(); } catch (_) {} }
    };
    const fail = error => {
      if (stopped) return;
      stop();
      if (typeof onError === 'function') { try { onError(error); } catch (_) {} }
    };
    const emit = chunk => {
      if (stopped) return;
      try { onChunk(chunk); } catch (error) { fail(error); }
    };
    try {
      const mic = own(context.createMediaStreamSource(stream));
      let hasReference = false;
      if (kind === 'worklet') {
        processor = own(new AudioWorkletNode(context, 'neurosama-duplex-capture', {
          numberOfInputs: 2, numberOfOutputs: 1, outputChannelCount: [1],
          channelCountMode: 'max', channelInterpretation: 'discrete',
        }));
        processor.port.onmessage = event => emit(event.data);
        processor.port.onmessageerror = () => fail(new Error('Capture worklet message could not be decoded'));
        processor.onprocessorerror = () => fail(new Error('Capture worklet processor failed'));
        mic.connect(processor, 0, 0);
        if (referenceNode) {
          referenceTarget = processor;
          referenceNode.connect(processor, 0, 1);
          hasReference = true;
        }
        // Worklet writes only zeroes; keep it on the render graph.
        processor.connect(context.destination);
      } else {
        const merger = typeof context.createChannelMerger === 'function' ? own(context.createChannelMerger(2)) : null;
        processor = own(context.createScriptProcessor(1024, merger ? 2 : 1, 1));
        const mute = own(context.createGain());
        mute.gain.value = 0;
        if (merger) {
          mic.connect(merger, 0, 0);
          if (referenceNode) {
            referenceTarget = merger;
            referenceNode.connect(merger, 0, 1);
            hasReference = true;
          }
          merger.connect(processor);
        } else mic.connect(processor);
        const size = Math.max(1, Math.round(context.sampleRate * .02));
        let microphone = new Float32Array(size), reference = new Float32Array(size), audioTime = 0;
        processor.onaudioprocess = event => {
          // Silence even a queued callback after stop; never copy input to output.
          for (let c = 0; c < event.outputBuffer.numberOfChannels; c++) event.outputBuffer.getChannelData(c).fill(0);
          if (stopped) return;
          try {
            const input = event.inputBuffer;
            const m = input.getChannelData(0);
            const r = hasReference && input.numberOfChannels > 1 ? input.getChannelData(1) : null;
            const inputLength = Number.isInteger(input.length) && input.length > 0 ? input.length : m.length;
            const start = context.currentTime - inputLength / context.sampleRate;
            for (let i = 0; i < m.length && !stopped; i++) {
              if (!used) audioTime = start + i / context.sampleRate;
              microphone[used] = m[i];
              reference[used] = r ? r[i] : 0;
              if (++used === size) {
                const chunk = { microphone, reference, sampleRate: context.sampleRate, audioTime };
                used = 0;
                microphone = new Float32Array(size); reference = new Float32Array(size);
                emit(chunk);
              }
            }
          } catch (error) { fail(error); }
        };
        processor.connect(mute); mute.connect(context.destination);
      }
      return { kind, timingSource: kind === 'worklet' ? 'worklet_input_clock' : 'script_callback_estimate', hasReference, stop };
    } catch (error) { stop(); throw error; }
  }

  if (context.audioWorklet?.addModule && typeof AudioWorkletNode === 'function') {
    try { await loadModule(context); return setup('worklet'); } catch (_) {
      // Includes partial graph failures; setup has already disconnected its nodes.
    }
  }
  return setup('script');
}
