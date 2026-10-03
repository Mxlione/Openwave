/**
 * Tests for the spectrum frame decoder.
 *
 * This is a wire format shared with the backend, so the layout is pinned down here: a frame
 * built by hand to the documented offsets must decode to the values that went in. If the server
 * ever changes the layout, this is where it shows.
 */
import { FRAME_HEADER_SIZE, FRAME_VERSION, binFrequencyHz, decodeFrame, levelDbfs } from './frame';

/** Build a frame to the documented layout, so the decoder is tested against the spec. */
function buildFrame(options: {
  bins: number[];
  centerFreqHz?: number;
  spanHz?: number;
  referenceDbfs?: number;
  rangeDb?: number;
  timestampMs?: number;
  version?: number;
  magic?: string;
}): ArrayBuffer {
  const {
    bins,
    centerFreqHz = 98e6,
    spanHz = 2.4e6,
    referenceDbfs = -20,
    rangeDb = 90,
    timestampMs = 1_700_000_000_000,
    version = FRAME_VERSION,
    magic = 'OWSP',
  } = options;

  const buffer = new ArrayBuffer(FRAME_HEADER_SIZE + bins.length);
  const view = new DataView(buffer);
  for (let index = 0; index < 4; index += 1) {
    view.setUint8(index, magic.charCodeAt(index));
  }
  view.setUint8(4, version);
  view.setUint8(5, 0);
  view.setUint16(6, bins.length, true);
  view.setFloat64(8, centerFreqHz, true);
  view.setFloat64(16, spanHz, true);
  view.setFloat32(24, referenceDbfs, true);
  view.setFloat32(28, rangeDb, true);
  view.setBigUint64(32, BigInt(timestampMs), true);
  new Uint8Array(buffer, FRAME_HEADER_SIZE).set(bins);
  return buffer;
}

describe('decodeFrame', () => {
  it('reads every header field from its documented offset', () => {
    const frame = decodeFrame(
      buildFrame({
        bins: [0, 128, 255],
        centerFreqHz: 101.7e6,
        spanHz: 1.024e6,
        referenceDbfs: -33.5,
        rangeDb: 60,
        timestampMs: 1_234_567_890,
      }),
    );

    expect(frame.centerFreqHz).toBe(101.7e6);
    expect(frame.spanHz).toBe(1.024e6);
    expect(frame.referenceDbfs).toBeCloseTo(-33.5, 3);
    expect(frame.rangeDb).toBeCloseTo(60, 3);
    expect(frame.timestampMs).toBe(1_234_567_890);
    expect(frame.magnitudes.length).toBe(3);
  });

  it('turns the highest byte into the reference level', () => {
    const frame = decodeFrame(buildFrame({ bins: [255], referenceDbfs: -20 }));
    expect(levelDbfs(frame, 0)).toBeCloseTo(-20, 3);
  });

  it('turns the lowest byte into the bottom of the range', () => {
    const frame = decodeFrame(buildFrame({ bins: [0], referenceDbfs: -20, rangeDb: 90 }));
    expect(levelDbfs(frame, 0)).toBeCloseTo(-110, 3);
  });

  it('spaces the bins across the span', () => {
    const frame = decodeFrame(buildFrame({ bins: [0, 0, 0, 0], centerFreqHz: 100e6, spanHz: 4e6 }));
    expect(binFrequencyHz(frame, 0)).toBeCloseTo(98.5e6, 0);
    expect(binFrequencyHz(frame, 3)).toBeCloseTo(101.5e6, 0);
  });

  it('refuses something that is not a frame', () => {
    expect(() => decodeFrame(buildFrame({ bins: [0], magic: 'JUNK' }))).toThrowError(
      /not a spectrum frame/,
    );
  });

  it('refuses a version it does not understand', () => {
    // An old interface misreading a newer frame could draw a loud signal that is not there.
    expect(() =>
      decodeFrame(buildFrame({ bins: [0], version: FRAME_VERSION + 1 })),
    ).toThrowError(/is not version/);
  });

  it('refuses a frame shorter than its header', () => {
    expect(() => decodeFrame(new ArrayBuffer(8))).toThrowError(/at least 40 bytes/);
  });

  it('refuses a frame whose length disagrees with its bin count', () => {
    const full = buildFrame({ bins: [1, 2, 3, 4] });
    expect(() => decodeFrame(full.slice(0, full.byteLength - 2))).toThrowError(/should be/);
  });
});
