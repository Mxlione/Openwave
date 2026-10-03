/**
 * The live spectrum and waterfall.
 *
 * Two views of the same frames. The spectrum shows what is on the air right now, which is what
 * you watch while turning the dial; the waterfall shows the last few seconds, which is what
 * shows up a signal that comes and goes.
 *
 * Both are drawn on a canvas rather than with elements. A thousand bins twenty times a second is
 * twenty thousand DOM updates a second, which no amount of change detection makes acceptable;
 * one canvas draw is a few hundred microseconds.
 *
 * **The scale is fixed, not automatic.** A frame can tell you its own loudest bin, and following
 * it makes the display use its full range -- but it also makes an unchanging signal appear to
 * change colour as something else comes and goes elsewhere in the span. For a waterfall that is
 * worse than wasting range, so the reference is pinned.
 */
import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';

import { OpenWaveService, formatFrequency } from '../api/openwave.service';
import { binFrequencyHz, decodeFrame, levelDbfs } from './frame';
import type { SpectrumFrame } from './frame';

/** Rows of history the waterfall keeps. */
const WATERFALL_ROWS = 160;

/** The level the top of the scale means, in dBFS. */
const SCALE_TOP_DBFS = -10;

/** Decibels the scale covers below its top. */
const SCALE_RANGE_DB = 70;

@Component({
  selector: 'ow-spectrum',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './spectrum.html',
  styleUrl: './spectrum.scss',
})
export class Spectrum implements AfterViewInit {
  private readonly api = inject(OpenWaveService);
  private readonly destroyRef = inject(DestroyRef);

  private readonly spectrumCanvas =
    viewChild.required<ElementRef<HTMLCanvasElement>>('spectrumCanvas');
  private readonly waterfallCanvas =
    viewChild.required<ElementRef<HTMLCanvasElement>>('waterfallCanvas');

  protected readonly frequencyMhz = signal(98.0);
  protected readonly running = signal(false);
  protected readonly problem = signal<string | null>(null);
  protected readonly framesSeen = signal(0);
  protected readonly latest = signal<SpectrumFrame | null>(null);

  /** What the pointer is over, so a viewer can read a frequency off the display. */
  protected readonly hover = signal<{ hz: number; dbfs: number } | null>(null);

  protected readonly span = computed(() => {
    const frame = this.latest();
    return frame ? formatFrequency(frame.spanHz) : '—';
  });

  private socket: WebSocket | null = null;
  private waterfallRow = 0;

  constructor() {
    this.destroyRef.onDestroy(() => this.stop());
  }

  ngAfterViewInit(): void {
    this.prepareCanvases();
  }

  /** Start or stop the feed. */
  protected toggle(): void {
    if (this.running()) {
      this.stop();
    } else {
      this.start();
    }
  }

  protected start(): void {
    this.stop();
    this.problem.set(null);
    this.framesSeen.set(0);
    this.waterfallRow = 0;
    this.clearWaterfall();

    const socket = new WebSocket(this.api.spectrumSocketUrl(this.frequencyMhz() * 1e6));
    socket.binaryType = 'arraybuffer';
    this.socket = socket;
    this.running.set(true);

    socket.onmessage = (event) => {
      try {
        const frame = decodeFrame(event.data as ArrayBuffer);
        this.latest.set(frame);
        this.framesSeen.update((count) => count + 1);
        this.draw(frame);
      } catch (error) {
        // A frame this build cannot read is worth saying rather than drawing wrongly.
        this.problem.set(error instanceof Error ? error.message : String(error));
        this.stop();
      }
    };

    socket.onerror = () => {
      this.problem.set('The spectrum feed stopped. Is the receiver still available?');
      this.running.set(false);
    };

    socket.onclose = () => this.running.set(false);
  }

  protected stop(): void {
    if (this.socket) {
      this.socket.onmessage = null;
      this.socket.onerror = null;
      this.socket.onclose = null;
      this.socket.close();
      this.socket = null;
    }
    this.running.set(false);
  }

  /** Report what the pointer is over, in frequency and level. */
  protected onPointerMove(event: PointerEvent): void {
    const frame = this.latest();
    const canvas = this.spectrumCanvas().nativeElement;
    if (!frame) {
      return;
    }
    const bounds = canvas.getBoundingClientRect();
    const fraction = (event.clientX - bounds.left) / bounds.width;
    const index = Math.max(
      0,
      Math.min(frame.magnitudes.length - 1, Math.floor(fraction * frame.magnitudes.length)),
    );
    this.hover.set({ hz: binFrequencyHz(frame, index), dbfs: levelDbfs(frame, index) });
  }

  protected onPointerLeave(): void {
    this.hover.set(null);
  }

  protected formatHz(hz: number): string {
    return formatFrequency(hz);
  }

  // -- Drawing ------------------------------------------------------------------------------

  /** Size the canvases to their boxes, allowing for the display's pixel ratio. */
  private prepareCanvases(): void {
    for (const reference of [this.spectrumCanvas(), this.waterfallCanvas()]) {
      const canvas = reference.nativeElement;
      const ratio = window.devicePixelRatio || 1;
      const bounds = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(bounds.width * ratio));
      canvas.height = Math.max(1, Math.round(bounds.height * ratio));
    }
    this.clearWaterfall();
  }

  private draw(frame: SpectrumFrame): void {
    this.drawSpectrum(frame);
    this.drawWaterfallRow(frame);
  }

  /** Draw the spectrum as a filled trace. */
  private drawSpectrum(frame: SpectrumFrame): void {
    const canvas = this.spectrumCanvas().nativeElement;
    const context = canvas.getContext('2d');
    if (!context) {
      return;
    }

    const { width, height } = canvas;
    context.clearRect(0, 0, width, height);

    // Horizontal lines every ten decibels, so a viewer can judge a level rather than only
    // compare shapes.
    context.strokeStyle = 'rgba(255, 255, 255, 0.07)';
    context.lineWidth = 1;
    for (let db = 0; db <= SCALE_RANGE_DB; db += 10) {
      const y = Math.round((db / SCALE_RANGE_DB) * height) + 0.5;
      context.beginPath();
      context.moveTo(0, y);
      context.lineTo(width, y);
      context.stroke();
    }

    const bins = frame.magnitudes.length;
    context.beginPath();
    context.moveTo(0, height);
    for (let index = 0; index < bins; index += 1) {
      const x = (index / (bins - 1)) * width;
      const y = this.levelToY(levelDbfs(frame, index), height);
      context.lineTo(x, y);
    }
    context.lineTo(width, height);
    context.closePath();

    const fill = context.createLinearGradient(0, 0, 0, height);
    fill.addColorStop(0, 'rgba(90, 200, 250, 0.55)');
    fill.addColorStop(1, 'rgba(90, 200, 250, 0.05)');
    context.fillStyle = fill;
    context.fill();

    context.strokeStyle = 'rgb(120, 215, 255)';
    context.lineWidth = Math.max(1, Math.round(window.devicePixelRatio || 1));
    context.stroke();
  }

  /**
   * Add one row to the waterfall.
   *
   * Written one row at a time into a canvas that scrolls by moving its own contents up, rather
   * than redrawing the whole history every frame: the history does not change, and redrawing it
   * twenty times a second would be a hundred times the work for the same picture.
   */
  private drawWaterfallRow(frame: SpectrumFrame): void {
    const canvas = this.waterfallCanvas().nativeElement;
    const context = canvas.getContext('2d');
    if (!context) {
      return;
    }

    const { width, height } = canvas;
    const rowHeight = Math.max(1, Math.floor(height / WATERFALL_ROWS));

    // Shift what is there up by one row, then draw the new row at the bottom.
    context.drawImage(canvas, 0, -rowHeight);

    const bins = frame.magnitudes.length;
    const row = context.createImageData(width, rowHeight);
    for (let x = 0; x < width; x += 1) {
      const index = Math.min(bins - 1, Math.floor((x / width) * bins));
      const [red, green, blue] = this.colour(levelDbfs(frame, index));
      for (let y = 0; y < rowHeight; y += 1) {
        const offset = (y * width + x) * 4;
        row.data[offset] = red;
        row.data[offset + 1] = green;
        row.data[offset + 2] = blue;
        row.data[offset + 3] = 255;
      }
    }
    context.putImageData(row, 0, height - rowHeight);
    this.waterfallRow += 1;
  }

  private clearWaterfall(): void {
    const canvas = this.waterfallCanvas().nativeElement;
    const context = canvas.getContext('2d');
    if (!context) {
      return;
    }
    context.fillStyle = '#05070c';
    context.fillRect(0, 0, canvas.width, canvas.height);
  }

  /** Where a level sits on the vertical scale. */
  private levelToY(dbfs: number, height: number): number {
    const fraction = (SCALE_TOP_DBFS - dbfs) / SCALE_RANGE_DB;
    return Math.max(0, Math.min(height, fraction * height));
  }

  /**
   * The colour for a level.
   *
   * Dark blue through cyan to yellow and white. The ramp rises in lightness the whole way, so it
   * reads correctly as intensity even to somebody who cannot distinguish the hues -- a ramp that
   * only changes hue leaves them a flat picture.
   */
  private colour(dbfs: number): [number, number, number] {
    const fraction = Math.max(
      0,
      Math.min(1, 1 - (SCALE_TOP_DBFS - dbfs) / SCALE_RANGE_DB),
    );

    if (fraction < 0.35) {
      const t = fraction / 0.35;
      return [Math.round(5 + t * 10), Math.round(10 + t * 80), Math.round(25 + t * 110)];
    }
    if (fraction < 0.7) {
      const t = (fraction - 0.35) / 0.35;
      return [Math.round(15 + t * 90), Math.round(90 + t * 130), Math.round(135 + t * 60)];
    }
    const t = (fraction - 0.7) / 0.3;
    return [Math.round(105 + t * 150), Math.round(220 + t * 35), Math.round(195 - t * 60)];
  }
}
