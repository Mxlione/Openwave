/**
 * The FM station list.
 *
 * What a viewer wants from this page is to find something to listen to, so the list leads with
 * the station's name where RDS gave one and its frequency otherwise, and the Listen button
 * points the browser's own audio element at the stream rather than decoding anything here.
 */
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { OpenWaveService, formatMegahertz } from '../api/openwave.service';
import type { ApiFailure } from '../api/openwave.service';
import type { Favourite, Station } from '../api/types';
import { FavouriteButton } from '../shared/favourite-button';
import { ScanPanel } from '../shared/scan-panel';
import { SignalMeter } from '../shared/signal-meter';

@Component({
  selector: 'ow-stations',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FavouriteButton, ScanPanel, SignalMeter],
  templateUrl: './stations.html',
  styleUrl: './stations.scss',
})
export class Stations {
  private readonly api = inject(OpenWaveService);

  protected readonly stations = signal<Station[]>([]);
  protected readonly favourites = signal<Favourite[]>([]);
  protected readonly loading = signal(true);
  protected readonly failure = signal<ApiFailure | null>(null);
  protected readonly playing = signal<Station | null>(null);
  protected readonly readRds = signal(true);

  /** Whether a scan has ever been run, which is different from one that found nothing. */
  protected readonly scanned = signal(false);

  protected readonly stereoCount = computed(
    () => this.stations().filter((station) => station.stereo === true).length,
  );

  protected readonly scanOptions = computed(() => ({ rds: this.readRds() }));

  constructor() {
    this.reload();
  }

  /** Fetch the station list and the favourites together. */
  protected reload(): void {
    this.loading.set(true);
    this.api.stations().subscribe({
      next: (stations) => {
        this.stations.set(stations);
        this.loading.set(false);
        if (stations.length) {
          this.scanned.set(true);
        }
      },
      error: (problem: ApiFailure) => {
        this.failure.set(problem);
        this.loading.set(false);
      },
    });
    this.api.favourites('station').subscribe({
      next: (favourites) => this.favourites.set(favourites),
      error: () => {
        // Not worth a banner: a missing favourites list leaves empty stars, and the station
        // list is still useful.
      },
    });
  }

  /** Called when a scan finishes. */
  protected onScanned(): void {
    this.scanned.set(true);
    this.reload();
  }

  /** How to show a station: its RDS name, or its frequency. */
  protected label(station: Station): string {
    return station.name ?? `${formatMegahertz(station.freq_hz)} MHz`;
  }

  protected megahertz(station: Station): string {
    return formatMegahertz(station.freq_hz);
  }

  /** How to describe a station's mode, including when it was not checked. */
  protected mode(station: Station): string {
    if (station.stereo === null || station.stereo === undefined) {
      return 'unchecked';
    }
    return station.stereo ? 'stereo' : 'mono';
  }

  /** What a favourite for this station looks like. */
  protected asFavourite(station: Station): Favourite {
    return {
      kind: 'station',
      name: this.label(station),
      freq_hz: station.freq_hz,
      service_id: null,
      added_at: new Date().toISOString(),
    } as Favourite;
  }

  protected isFavourite(station: Station): boolean {
    // Matched to the nearest kilohertz, the same way the server does: a receiver reports where
    // it was tuned, so the same station can differ by a few hertz between scans.
    const wanted = Math.round(station.freq_hz / 1000);
    return this.favourites().some((item) => Math.round(item.freq_hz / 1000) === wanted);
  }

  /** Start playing a station, or stop if it is already playing. */
  protected listen(station: Station): void {
    this.playing.set(this.playing()?.freq_hz === station.freq_hz ? null : station);
  }

  protected streamUrl(station: Station): string {
    return this.api.streamUrl(station.freq_hz);
  }

  protected onFavouriteChanged(): void {
    this.api.favourites('station').subscribe({
      next: (favourites) => this.favourites.set(favourites),
    });
  }

  protected onFavouriteFailed(problem: ApiFailure): void {
    this.failure.set(problem);
  }
}
