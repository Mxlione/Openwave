/**
 * The television channel list.
 *
 * Grouped by multiplex, because that is the physical reality a viewer occasionally needs to see:
 * every service in a group shares one transmission, so they all have the same signal strength
 * and they all disappear together when reception fails. Within a group they are listed by the
 * number a viewer would type.
 *
 * Services that cannot be watched are listed anyway, and marked. A channel list that silently
 * omits an encrypted service leaves somebody wondering where a channel went.
 */
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { OpenWaveService, formatFrequency } from '../api/openwave.service';
import type { ApiFailure } from '../api/openwave.service';
import type { Channel, Favourite } from '../api/types';
import { FavouriteButton } from '../shared/favourite-button';
import { ScanPanel } from '../shared/scan-panel';
import { SignalMeter } from '../shared/signal-meter';

/** One multiplex and the services it carries. */
interface Multiplex {
  readonly freqHz: number;
  readonly label: string;
  readonly snrDb: number | null;
  readonly channels: readonly Channel[];
}

@Component({
  selector: 'ow-channels',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FavouriteButton, ScanPanel, SignalMeter],
  templateUrl: './channels.html',
  styleUrl: './channels.scss',
})
export class Channels {
  private readonly api = inject(OpenWaveService);

  protected readonly channels = signal<Channel[]>([]);
  protected readonly favourites = signal<Favourite[]>([]);
  protected readonly loading = signal(true);
  protected readonly failure = signal<ApiFailure | null>(null);
  protected readonly scanned = signal(false);
  protected readonly band = signal<'uhf' | 'vhf'>('uhf');

  protected readonly scanOptions = computed(() => ({ plan: this.band() }));

  protected readonly televisionCount = computed(
    () => this.channels().filter((channel) => channel.service_type !== 2).length,
  );

  /** The channels grouped by the transmission that carries them. */
  protected readonly multiplexes = computed<Multiplex[]>(() => {
    const groups = new Map<number, Channel[]>();
    for (const channel of this.channels()) {
      const existing = groups.get(channel.mux_freq_hz);
      if (existing) {
        existing.push(channel);
      } else {
        groups.set(channel.mux_freq_hz, [channel]);
      }
    }

    return [...groups.entries()]
      .sort(([left], [right]) => left - right)
      .map(([freqHz, channels]) => ({
        freqHz,
        label: formatFrequency(freqHz),
        snrDb: channels[0]?.snr_db ?? null,
        channels: [...channels].sort(
          (left, right) =>
            (left.logical_channel ?? Number.MAX_SAFE_INTEGER) -
            (right.logical_channel ?? Number.MAX_SAFE_INTEGER),
        ),
      }));
  });

  constructor() {
    this.reload();
  }

  protected reload(): void {
    this.loading.set(true);
    this.api.channels().subscribe({
      next: (channels) => {
        this.channels.set(channels);
        this.loading.set(false);
        if (channels.length) {
          this.scanned.set(true);
        }
      },
      error: (problem: ApiFailure) => {
        this.failure.set(problem);
        this.loading.set(false);
      },
    });
    this.api.favourites('channel').subscribe({
      next: (favourites) => this.favourites.set(favourites),
      error: () => {
        // Empty stars are a smaller loss than no channel list.
      },
    });
  }

  protected onScanned(): void {
    this.scanned.set(true);
    this.reload();
  }

  /** What kind of service this is, in a word. */
  protected kind(channel: Channel): string {
    if (channel.service_type === 2 || channel.service_type === 10) {
      return 'radio';
    }
    if (channel.video_pid !== null && channel.video_pid !== undefined) {
      return 'television';
    }
    return 'other';
  }

  protected asFavourite(channel: Channel): Favourite {
    return {
      kind: 'channel',
      name: channel.name,
      freq_hz: channel.mux_freq_hz,
      service_id: channel.service_id,
      added_at: new Date().toISOString(),
    } as Favourite;
  }

  protected isFavourite(channel: Channel): boolean {
    const wantedFrequency = Math.round(channel.mux_freq_hz / 1000);
    return this.favourites().some(
      (item) =>
        Math.round(item.freq_hz / 1000) === wantedFrequency &&
        item.service_id === channel.service_id,
    );
  }

  protected onFavouriteChanged(): void {
    this.api.favourites('channel').subscribe({
      next: (favourites) => this.favourites.set(favourites),
    });
  }

  protected onFavouriteFailed(problem: ApiFailure): void {
    this.failure.set(problem);
  }
}
