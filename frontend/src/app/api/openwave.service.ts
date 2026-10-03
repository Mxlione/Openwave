/**
 * The one place that talks to OpenWave.
 *
 * Every component goes through this service rather than calling `fetch` itself, so that the
 * base URL, the polling of a running scan and the handling of a failed request are decided
 * once. The types come from the backend's own OpenAPI document, so a change to a response in
 * Python becomes a compile error here.
 */
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';

import type {
  Capabilities,
  Channel,
  DeviceSummary,
  Favourite,
  FavouriteKind,
  ScanDetail,
  ScanRequest,
  ScanSummary,
  Station,
} from './types';

/** Where the API lives. Same origin, because the backend serves this application. */
export const API_BASE = '/api/v1';

/** What went wrong, in words meant to be shown to somebody. */
export interface ApiFailure {
  /** The kind of problem, such as `DeviceNotFoundError`. */
  readonly error: string;
  /** What happened, and what to do about it. */
  readonly detail: string;
  /** The HTTP status, for a client that wants to distinguish a conflict from a failure. */
  readonly status: number;
}

@Injectable({ providedIn: 'root' })
export class OpenWaveService {
  private readonly http = inject(HttpClient);

  /** What this installation can do. */
  capabilities(): Observable<Capabilities> {
    return this.get<Capabilities>('/health');
  }

  /** Every receiver and tuner that can currently be opened. */
  devices(): Observable<DeviceSummary[]> {
    return this.get<DeviceSummary[]>('/devices');
  }

  /** Start a scan. Returns at once; the scan runs in the background. */
  startScan(request: ScanRequest): Observable<ScanSummary> {
    return this.post<ScanSummary>('/scans', request);
  }

  /** One scan, with its results once it has finished. */
  scan(id: string): Observable<ScanDetail> {
    return this.get<ScanDetail>(`/scans/${encodeURIComponent(id)}`);
  }

  /** Every remembered scan, newest first. */
  scans(): Observable<ScanSummary[]> {
    return this.get<ScanSummary[]>('/scans');
  }

  /** Ask a scan to stop. */
  cancelScan(id: string): Observable<ScanSummary> {
    return this.delete<ScanSummary>(`/scans/${encodeURIComponent(id)}`);
  }

  /** The stations the most recent completed FM scan found. */
  stations(): Observable<Station[]> {
    return this.get<Station[]>('/stations');
  }

  /** The services the most recent completed television scan found. */
  channels(): Observable<Channel[]> {
    return this.get<Channel[]>('/channels');
  }

  /** Every remembered station and channel. */
  favourites(kind?: FavouriteKind): Observable<Favourite[]> {
    const query = kind ? `?kind=${encodeURIComponent(kind)}` : '';
    return this.get<Favourite[]>(`/favourites${query}`);
  }

  /** Remember a station or channel. */
  addFavourite(favourite: Favourite): Observable<Favourite> {
    return this.post<Favourite>('/favourites', favourite);
  }

  /** Forget a station or channel. */
  removeFavourite(favourite: Favourite): Observable<void> {
    return this.http
      .request<void>('delete', `${API_BASE}/favourites`, { body: favourite })
      .pipe(catchError((error) => this.fail(error)));
  }

  /**
   * Where to point a player to hear a station.
   *
   * A URL rather than audio data: the browser's own audio element, or VLC on another machine,
   * can pull it without this application decoding anything.
   */
  streamUrl(freqHz: number, seconds?: number): string {
    const query = seconds === undefined ? '' : `?seconds=${seconds}`;
    return `${API_BASE}/streams/fm/${freqHz}${query}`;
  }

  /** Where to connect for a scan's progress. */
  scanSocketUrl(id: string): string {
    return `${this.websocketBase()}/ws/scans/${encodeURIComponent(id)}`;
  }

  /** Where to connect for a live spectrum. */
  spectrumSocketUrl(freqHz: number, sampleRateHz = 2_400_000): string {
    return (
      `${this.websocketBase()}/ws/spectrum` +
      `?freq_hz=${freqHz}&sample_rate_hz=${sampleRateHz}`
    );
  }

  /**
   * The WebSocket origin, derived from the page's own.
   *
   * Derived rather than configured: the backend serves this application, so hard-coding a host
   * would break every deployment that is not the developer's own.
   */
  private websocketBase(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}${API_BASE}`;
  }

  private get<T>(path: string): Observable<T> {
    return this.http.get<T>(`${API_BASE}${path}`).pipe(catchError((error) => this.fail(error)));
  }

  private post<T>(path: string, body: unknown): Observable<T> {
    return this.http
      .post<T>(`${API_BASE}${path}`, body)
      .pipe(catchError((error) => this.fail(error)));
  }

  private delete<T>(path: string): Observable<T> {
    return this.http
      .delete<T>(`${API_BASE}${path}`)
      .pipe(catchError((error) => this.fail(error)));
  }

  /**
   * Turn a failed request into something worth showing.
   *
   * The backend's own errors carry a message meant for a person -- a dongle not plugged in, a
   * driver not installed -- so it is passed through rather than replaced with "request failed".
   */
  private fail(error: HttpErrorResponse): Observable<never> {
    const body = error.error as { error?: string; detail?: string } | null;
    const failure: ApiFailure = {
      error: body?.error ?? error.name ?? 'RequestFailed',
      detail:
        body?.detail ??
        (error.status === 0
          ? 'Could not reach OpenWave. Is the server still running?'
          : error.message),
      status: error.status,
    };
    return throwError(() => failure);
  }
}

/** Format a frequency the way a radio dial does. */
export function formatFrequency(hz: number): string {
  const magnitude = Math.abs(hz);
  if (magnitude >= 1e9) {
    return `${(hz / 1e9).toFixed(3)} GHz`;
  }
  if (magnitude >= 1e6) {
    return `${(hz / 1e6).toFixed(3)} MHz`;
  }
  if (magnitude >= 1e3) {
    return `${(hz / 1e3).toFixed(1)} kHz`;
  }
  return `${hz.toFixed(0)} Hz`;
}

/** Format a frequency in megahertz, as an FM station is spoken. */
export function formatMegahertz(hz: number): string {
  return `${(hz / 1e6).toFixed(1)}`;
}
