/**
 * A bar showing how strong a signal is.
 *
 * The scale is the part worth thinking about. OpenWave reports a signal-to-noise ratio in
 * decibels, and a consumer receiver has no calibrated power reference, so an absolute figure
 * would be invented precision. What a viewer can act on is comparative: is this station strong
 * enough to listen to, and is it stronger than that one? So the bar runs from the ratio at which
 * reception becomes usable to the ratio beyond which more makes no difference.
 */
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Signal-to-noise ratio below which FM reception is unusable, in decibels. */
const FLOOR_DB = 10;

/** Ratio beyond which more signal makes no audible difference, in decibels. */
const CEILING_DB = 60;

@Component({
  selector: 'ow-signal-meter',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      class="meter"
      role="meter"
      [attr.aria-valuenow]="snrDb()"
      [attr.aria-valuemin]="0"
      [attr.aria-valuemax]="ceiling"
      [attr.aria-label]="'Signal strength ' + snrDb().toFixed(0) + ' decibels'"
      [title]="snrDb().toFixed(1) + ' dB above the noise floor'"
    >
      <div class="fill" [class]="quality()" [style.width.%]="percent()"></div>
    </div>
    @if (showValue()) {
      <span class="value">{{ snrDb().toFixed(0) }} dB</span>
    }
  `,
  styles: `
    :host {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
    }

    .meter {
      width: 6rem;
      height: 0.5rem;
      background: var(--ow-surface-sunken);
      border-radius: 0.25rem;
      overflow: hidden;
    }

    .fill {
      height: 100%;
      border-radius: 0.25rem;
      transition: width 150ms ease-out;
    }

    /* Three bands rather than a continuous gradient: a viewer reads "strong, usable, marginal"
       far faster than a colour on a scale, and the boundaries mean something. */
    .fill.strong {
      background: var(--ow-good);
    }
    .fill.usable {
      background: var(--ow-fair);
    }
    .fill.marginal {
      background: var(--ow-poor);
    }

    .value {
      font-variant-numeric: tabular-nums;
      font-size: 0.8125rem;
      color: var(--ow-text-dim);
      min-width: 3.5rem;
    }
  `,
})
export class SignalMeter {
  /** Signal-to-noise ratio, in decibels above the noise floor. */
  readonly snrDb = input.required<number>();

  /** Whether to print the figure beside the bar. */
  readonly showValue = input(true);

  protected readonly ceiling = CEILING_DB;

  /** How much of the bar to fill. */
  protected readonly percent = computed(() => {
    const span = CEILING_DB - FLOOR_DB;
    const above = this.snrDb() - FLOOR_DB;
    return Math.max(2, Math.min(100, (above / span) * 100));
  });

  /** Which band this signal falls in. */
  protected readonly quality = computed(() => {
    const snr = this.snrDb();
    if (snr >= 35) {
      return 'strong';
    }
    if (snr >= 20) {
      return 'usable';
    }
    return 'marginal';
  });
}
