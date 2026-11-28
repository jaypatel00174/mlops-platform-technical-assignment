import { signal } from '@angular/core';
import { Observable, Subscription } from 'rxjs';

import { ApiError } from './api-error';

/**
 * The three states every screen needs from a request (loading, failed, loaded), as signals.
 *
 * `load()` shows the loading state; `refresh()` re-reads quietly, keeping the old data on screen,
 * which is what polling and "reload after an action" want. A quiet failure is recorded in
 * `refreshFailed` instead of replacing good data with an error.
 */
export class Loader<T> {
  readonly data = signal<T | null>(null);
  readonly loading = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly refreshFailed = signal(false);

  private subscription?: Subscription;

  constructor(private readonly source: () => Observable<T>) {}

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.run();
  }

  refresh(): void {
    this.run();
  }

  destroy(): void {
    this.subscription?.unsubscribe();
  }

  private run(): void {
    this.subscription?.unsubscribe();
    this.subscription = this.source().subscribe({
      next: (value) => {
        this.data.set(value);
        this.loading.set(false);
        this.error.set(null);
        this.refreshFailed.set(false);
      },
      error: (error: unknown) => {
        const apiError = ApiError.from(error);
        this.loading.set(false);
        if (this.data() === null) this.error.set(apiError);
        else this.refreshFailed.set(true);
      },
    });
  }
}
