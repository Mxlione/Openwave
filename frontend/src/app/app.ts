/**
 * The application shell: navigation, and what this installation can do.
 *
 * The capabilities are fetched once and shown in the footer, because the honest answer to "why
 * is there no sound?" is usually that libVLC is not installed, and the honest answer to "why
 * does the scan find nothing?" is usually that there is no receiver. Saying so where somebody
 * will see it is better than leaving them to guess.
 */
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { OpenWaveService } from './api/openwave.service';
import type { Capabilities } from './api/types';

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  private readonly api = inject(OpenWaveService);

  protected readonly capabilities = signal<Capabilities | null>(null);
  protected readonly unreachable = signal(false);

  constructor() {
    this.api.capabilities().subscribe({
      next: (capabilities) => this.capabilities.set(capabilities),
      error: () => this.unreachable.set(true),
    });
  }
}
