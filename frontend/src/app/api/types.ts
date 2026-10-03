/**
 * Convenient names for the types the API defines.
 *
 * The shapes themselves come from `schema.d.ts`, which is generated from the backend's OpenAPI
 * document by `npm run generate:api`. Nothing here is written by hand, which is the point: a
 * change to a response in Python becomes a compile error here rather than a field that is
 * quietly `undefined` at run time.
 */
import type { components } from './schema';

type Schemas = components['schemas'];

export type Station = Schemas['Station'];
export type Channel = Schemas['Channel'];
export type Capabilities = Schemas['Capabilities'];
export type DeviceSummary = Schemas['DeviceSummary'];
export type Favourite = Schemas['Favourite'];
export type FavouriteKind = Schemas['FavouriteKind'];
export type ScanRequest = Schemas['ScanRequest'];
export type ScanSummary = Schemas['ScanSummary'];
export type ScanDetail = Schemas['ScanDetail'];
export type ScanProgress = Schemas['ScanProgress'];
export type ScanState = Schemas['ScanState'];
export type Band = Schemas['Band'];
export type FmScanResults = Schemas['FmScanResults'];
export type TvScanResults = Schemas['TvScanResults'];
export type MultiplexSummary = Schemas['MultiplexSummary'];

/** Whether a scan will not change again. */
export function isFinished(state: ScanState): boolean {
  return state === 'complete' || state === 'failed' || state === 'cancelled';
}

/**
 * How far through a scan is, from 0 to 1.
 *
 * Zero while the total is still unknown, which is the case for the moment between a scan
 * starting and its first segment being planned.
 */
export function scanFraction(progress: ScanProgress | null | undefined): number {
  if (!progress || !progress.total) {
    return 0;
  }
  return progress.done / progress.total;
}
