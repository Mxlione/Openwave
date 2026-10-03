/**
 * Tests for the application shell.
 *
 * The shell's job is navigation and an honest account of what the installation can do, so that
 * is what is checked. The HTTP calls go through Angular's own testing controller rather than a
 * hand-made stub: `HttpClient.get` has a dozen overloads, and a stub that satisfies them all is
 * more fragile than the thing it is testing.
 */
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { App } from './app';
import { API_BASE } from './api/openwave.service';
import type { Capabilities } from './api/types';

/** Capabilities as a working installation reports them. */
const WORKING: Capabilities = {
  version: '0.1.0.dev0',
  playback: true,
  libvlc_version: '3.0.20',
  rtlsdr: false,
  receivers: 1,
  tuners: 1,
};

describe('App', () => {
  let fixture: ComponentFixture<App>;
  let http: HttpTestingController;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    await TestBed.compileComponents();
    fixture = TestBed.createComponent(App);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  /** Answer the capabilities request the shell makes when it is created. */
  function answer(capabilities: Capabilities = WORKING): void {
    http.expectOne(`${API_BASE}/health`).flush(capabilities);
    fixture.detectChanges();
  }

  function text(selector: string): string {
    return (fixture.nativeElement as HTMLElement).querySelector(selector)?.textContent ?? '';
  }

  it('is created', () => {
    answer();
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('offers a link to each view', () => {
    answer();
    const links = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('nav a'),
    ).map((anchor) => anchor.textContent?.trim());
    expect(links).toEqual(['Stations', 'Channels', 'Spectrum']);
  });

  it('reports the version and what works', () => {
    answer();
    expect(text('footer')).toContain('0.1.0.dev0');
    expect(text('footer')).toContain('playback ready');
  });

  it('says plainly when a driver is not installed', () => {
    // Without the extra, the RTL-SDR driver cannot be opened at all. Saying so where somebody
    // will see it beats leaving them to guess why a scan finds nothing.
    answer();
    expect(text('footer')).toContain('RTL-SDR driver not installed');
  });

  it('says when playback is unavailable', () => {
    answer({ ...WORKING, playback: false, libvlc_version: null });
    expect(text('footer')).toContain('no playback');
  });

  it('says when the API cannot be reached', () => {
    // The interface is served by the same process as the API, so this means the backend has
    // stopped — worth saying rather than showing an empty list.
    http
      .expectOne(`${API_BASE}/health`)
      .error(new ProgressEvent('error'), { status: 0, statusText: 'unreachable' });
    fixture.detectChanges();
    expect(text('[role="alert"]')).toContain('Cannot reach OpenWave');
  });
});
