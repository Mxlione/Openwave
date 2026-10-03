import { Routes } from '@angular/router';

/**
 * Where each page lives.
 *
 * Every view is loaded on demand. The spectrum view in particular pulls in canvas drawing that
 * somebody who only wants a station list never needs, and a first load that waits for it is a
 * first load that feels slow for no reason.
 */
export const routes: Routes = [
  { path: '', redirectTo: 'stations', pathMatch: 'full' },
  {
    path: 'stations',
    title: 'FM stations · OpenWave',
    loadComponent: () => import('./stations/stations').then((m) => m.Stations),
  },
  {
    path: 'channels',
    title: 'Television channels · OpenWave',
    loadComponent: () => import('./channels/channels').then((m) => m.Channels),
  },
  {
    path: 'spectrum',
    title: 'Spectrum · OpenWave',
    loadComponent: () => import('./spectrum/spectrum').then((m) => m.Spectrum),
  },
  { path: '**', redirectTo: 'stations' },
];
