/**
 * Starting a scan and watching it run.
 *
 * A scan takes seconds for radio and up to a minute for television, so the progress is pushed
 * over a WebSocket rather than polled: a client asking every few hundred milliseconds whether a
 * sweep has moved on by one channel is a great many requests to learn very little.
 *
 * The socket is the only source of progress, but the finished result is fetched over HTTP. That
 * split is deliberate: the socket carries a summary, which is small and frequent, and the result
 * can be a hundred stations, which is neither.
 */
import { ChangeDetectionStrategy, Component, DestroyRef, inject, input, output, signal } from '@angular/core';

import { OpenWaveService, formatFrequency } from '../api/openwave.service';
import type { ApiFailure } from '../api/openwave.service';
import type { Band, ScanRequest, ScanSummary } from '../api/types';
import { isFinished, scanFraction } from '../api/types';

@Component({
  selector: 'ow-scan-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="panel">
      <button type="button" class="primary" (click)="start()" [disabled]="running()">
        @if (running()) {
          Scanning…
        } @else {
          {{ label() }}
        }
      </button>

      @if (running()) {
        <button type="button" class="secondary" (click)="cancel()">Stop</button>
      }

      @if (progress(); as current) {
        <div class="progress" role="status">
          <div class="bar">
            <div class="fill" [style.width.%]="fraction() * 100"></div>
          </div>
          <span class="stage">
            {{ current.stage }}
            @if (current.frequency_hz) {
              <span class="where">· {{ frequency(current.frequency_hz) }}</span>
            }
            @if (current.total) {
              <span class="count">{{ current.done }} / {{ current.total }}</span>
            }
          </span>
        </div>
      }

      @if (failure(); as problem) {
        <p class="failure" role="alert">
          <strong>{{ problem.error }}</strong>
          {{ problem.detail }}
        </p>
      }
    </div>
  `,
  styles: `
    .panel {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.75rem;
    }

    .progress {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      flex: 1 1 14rem;
      min-width: 0;
    }

    .bar {
      height: 0.25rem;
      background: var(--ow-surface-sunken);
      border-radius: 0.125rem;
      overflow: hidden;
    }

    .fill {
      height: 100%;
      background: var(--ow-accent);
      transition: width 200ms ease-out;
    }

    .stage {
      font-size: 0.8125rem;
      color: var(--ow-text-dim);
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }

    .count {
      font-variant-numeric: tabular-nums;
    }

    .failure {
      flex: 1 1 100%;
      margin: 0;
      padding: 0.625rem 0.75rem;
      border-radius: 0.375rem;
      background: var(--ow-surface-sunken);
      border-left: 3px solid var(--ow-poor);
      font-size: 0.875rem;
    }

    .failure strong {
      display: block;
      color: var(--ow-poor);
      font-weight: 600;
    }
  `,
})
export class ScanPanel {
  /** Which band to sweep. */
  readonly band = input.required<Band>();

  /** What to put on the button. */
  readonly label = input('Scan');

  /** Anything else to ask of the scan, such as whether to read RDS. */
  readonly options = input<Partial<ScanRequest>>({});

  /** Fired when a scan finishes, so a list can reload itself. */
  readonly finished = output<ScanSummary>();

  private readonly api = inject(OpenWaveService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly running = signal(false);
  protected readonly progress = signal<ScanSummary['progress']>(null);
  protected readonly failure = signal<ApiFailure | null>(null);

  private scanId: string | null = null;
  private socket: WebSocket | null = null;

  constructor() {
    // Closing the socket when the component goes away: a socket left open keeps a scan's
    // progress flowing to nobody, and keeps the connection against the server.
    this.destroyRef.onDestroy(() => this.closeSocket());
  }

  protected readonly fraction = () => scanFraction(this.progress());

  protected frequency(hz: number): string {
    return formatFrequency(hz);
  }

  /** Start a scan and begin watching it. */
  protected start(): void {
    this.failure.set(null);
    this.progress.set(null);
    this.running.set(true);

    this.api.startScan({ band: this.band(), ...this.options() } as ScanRequest).subscribe({
      next: (summary) => {
        this.scanId = summary.id;
        this.watch(summary.id);
      },
      error: (problem: ApiFailure) => {
        this.failure.set(problem);
        this.running.set(false);
      },
    });
  }

  /** Ask the scan to stop. */
  protected cancel(): void {
    if (!this.scanId) {
      return;
    }
    this.api.cancelScan(this.scanId).subscribe({
      error: (problem: ApiFailure) => this.failure.set(problem),
    });
  }

  /** Follow a scan's progress over a WebSocket. */
  private watch(id: string): void {
    this.closeSocket();
    const socket = new WebSocket(this.api.scanSocketUrl(id));
    this.socket = socket;

    socket.onmessage = (event) => {
      const summary = JSON.parse(event.data as string) as ScanSummary;
      this.progress.set(summary.progress ?? null);
      if (isFinished(summary.state)) {
        this.settle(summary);
      }
    };

    // A socket that fails is not a reason to leave the button disabled for ever: the scan may
    // well have finished anyway, so the result is fetched over HTTP instead.
    socket.onerror = () => this.recover(id);
    socket.onclose = () => {
      if (this.running()) {
        this.recover(id);
      }
    };
  }

  /** Finish up after a scan has stopped. */
  private settle(summary: ScanSummary): void {
    this.running.set(false);
    this.progress.set(null);
    this.closeSocket();
    if (summary.error) {
      this.failure.set({ error: 'ScanFailed', detail: summary.error, status: 0 });
    } else {
      this.finished.emit(summary);
    }
  }

  /** Fall back to asking over HTTP when the socket lets go. */
  private recover(id: string): void {
    this.closeSocket();
    this.api.scan(id).subscribe({
      next: (detail) => {
        if (isFinished(detail.state)) {
          this.settle(detail);
        } else {
          this.running.set(false);
          this.progress.set(null);
        }
      },
      error: (problem: ApiFailure) => {
        this.failure.set(problem);
        this.running.set(false);
      },
    });
  }

  private closeSocket(): void {
    if (this.socket) {
      this.socket.onmessage = null;
      this.socket.onerror = null;
      this.socket.onclose = null;
      this.socket.close();
      this.socket = null;
    }
  }
}
