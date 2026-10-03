/**
 * A star that remembers a station or channel.
 *
 * Favourites are kept by the server rather than this browser, so the same list appears on a
 * phone and a television as well. That means every change is a request, and the button shows
 * what the server has rather than what was clicked -- a click that fails must not leave a star
 * filled in for something that was never remembered.
 */
import { ChangeDetectionStrategy, Component, computed, inject, input, output, signal } from '@angular/core';
import { Observable } from 'rxjs';

import { OpenWaveService } from '../api/openwave.service';
import type { ApiFailure } from '../api/openwave.service';
import type { Favourite } from '../api/types';

@Component({
  selector: 'ow-favourite-button',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button
      type="button"
      class="star"
      [class.on]="marked()"
      [disabled]="busy()"
      [attr.aria-pressed]="marked()"
      [attr.aria-label]="marked() ? 'Remove from favourites' : 'Add to favourites'"
      [title]="title()"
      (click)="toggle()"
    >
      {{ marked() ? '★' : '☆' }}
    </button>
  `,
  styles: `
    .star {
      background: none;
      border: none;
      cursor: pointer;
      font-size: 1.125rem;
      line-height: 1;
      padding: 0.125rem 0.25rem;
      color: var(--ow-text-dim);
      border-radius: 0.25rem;
    }

    .star:hover:not(:disabled) {
      color: var(--ow-fair);
      background: var(--ow-surface-sunken);
    }

    .star.on {
      color: var(--ow-fair);
    }

    .star:disabled {
      cursor: progress;
      opacity: 0.6;
    }
  `,
})
export class FavouriteButton {
  /** What this button remembers. */
  readonly favourite = input.required<Favourite>();

  /** Whether it is remembered at the moment. */
  readonly isFavourite = input(false);

  /** Fired after a successful change, so a list can refresh. */
  readonly changed = output<boolean>();

  /** Fired when the server refused, so the page can say why. */
  readonly failed = output<ApiFailure>();

  private readonly api = inject(OpenWaveService);
  private readonly local = signal<boolean | null>(null);

  protected readonly busy = signal(false);

  /** Whether to draw the star filled. Follows the server once a change has been made. */
  protected readonly marked = computed(() => this.local() ?? this.isFavourite());

  protected readonly title = computed(() =>
    this.marked() ? 'Remembered. Click to forget.' : 'Click to remember this one.',
  );

  protected toggle(): void {
    const wanted = !this.marked();
    this.busy.set(true);

    // Typed as the common shape: adding returns the favourite and removing returns nothing,
    // and TypeScript cannot unify the two subscribe signatures otherwise.
    const request: Observable<unknown> = wanted
      ? this.api.addFavourite(this.favourite())
      : this.api.removeFavourite(this.favourite());

    request.subscribe({
      next: () => {
        this.local.set(wanted);
        this.busy.set(false);
        this.changed.emit(wanted);
      },
      error: (problem: ApiFailure) => {
        // The star stays as it was: showing it filled for something the server refused to
        // remember would be a lie the next page load would contradict.
        this.busy.set(false);
        this.failed.emit(problem);
      },
    });
  }
}
