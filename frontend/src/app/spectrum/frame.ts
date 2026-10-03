/**
 * Reading the binary spectrum frames the server sends.
 *
 * The layout is defined once, in `openwave/api/spectrum.py`, and this is the other half of it.
 * A frame carries its own scale, so this decoder needs no prior agreement about what the bytes
 * mean -- and refuses a version it does not know rather than drawing a loud signal that is not
 * there.
 */

/** Marks a spectrum frame: the ASCII bytes of `OWSP`. */
const MAGIC = 0x4f_57_53_50;

/** The frame layout this decoder understands. */
export const FRAME_VERSION = 1;

/** Bytes before the bins. */
export const FRAME_HEADER_SIZE = 40;

/** One decoded frame. */
export interface SpectrumFrame {
  /** Middle of the span, in hertz. */
  readonly centerFreqHz: number;
  /** Width the bins cover, in hertz. */
  readonly spanHz: number;
  /** The level the highest byte value means, in dBFS. */
  readonly referenceDbfs: number;
  /** Decibels the byte range covers, below the reference. */
  readonly rangeDb: number;
  /** When the frame was made, in milliseconds since the epoch. */
  readonly timestampMs: number;
  /** One byte per bin, lowest frequency first. */
  readonly magnitudes: Uint8Array;
}

/**
 * Decode a frame.
 *
 * @throws if the data is not a frame, or is a version this build does not understand.
 */
export function decodeFrame(buffer: ArrayBuffer): SpectrumFrame {
  if (buffer.byteLength < FRAME_HEADER_SIZE) {
    throw new Error(
      `a spectrum frame is at least ${FRAME_HEADER_SIZE} bytes, got ${buffer.byteLength}`,
    );
  }

  const view = new DataView(buffer);
  // Big-endian here only because the magic is four characters read in order; every number
  // after it is little-endian, which is what the server packs.
  if (view.getUint32(0, false) !== MAGIC) {
    throw new Error('not a spectrum frame: the magic bytes do not match');
  }

  const version = view.getUint8(4);
  if (version !== FRAME_VERSION) {
    throw new Error(
      `spectrum frame version ${version} is not version ${FRAME_VERSION}, which is what this ` +
        'build understands. Reload the page to pick up a newer one.',
    );
  }

  const bins = view.getUint16(6, true);
  const expected = FRAME_HEADER_SIZE + bins;
  if (buffer.byteLength !== expected) {
    throw new Error(
      `frame claims ${bins} bins, so should be ${expected} bytes, got ${buffer.byteLength}`,
    );
  }

  return {
    centerFreqHz: view.getFloat64(8, true),
    spanHz: view.getFloat64(16, true),
    referenceDbfs: view.getFloat32(24, true),
    rangeDb: view.getFloat32(28, true),
    // Milliseconds since the epoch exceeds 2^53 only in the year 287396, so a Number is fine
    // and avoids a BigInt that nothing else here wants.
    timestampMs: Number(view.getBigUint64(32, true)),
    magnitudes: new Uint8Array(buffer, FRAME_HEADER_SIZE, bins),
  };
}

/** The level of one bin, in dBFS. */
export function levelDbfs(frame: SpectrumFrame, index: number): number {
  const step = frame.rangeDb / 255;
  return frame.referenceDbfs - (255 - frame.magnitudes[index]) * step;
}

/** The centre frequency of one bin, in hertz. */
export function binFrequencyHz(frame: SpectrumFrame, index: number): number {
  const start = frame.centerFreqHz - frame.spanHz / 2;
  return start + (frame.spanHz / frame.magnitudes.length) * (index + 0.5);
}
