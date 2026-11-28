import { Injectable, inject, signal } from '@angular/core';
import { EMPTY, catchError, from, mergeMap } from 'rxjs';

import { ModelStatus } from '../api/types';
import { ApiService } from './api.service';

const FRESH_FOR_MS = 20_000;
const PARALLEL = 6;

/**
 * Health status per model, shared by the screens that show it.
 *
 * Status is one request per model, so screens ask for the models they display and the store
 * fetches only what is missing or older than 20 seconds, a few at a time. A model whose status
 * cannot be read is simply absent: the screen shows "unknown" rather than failing as a whole.
 */
@Injectable({ providedIn: 'root' })
export class HealthStore {
  private readonly api = inject(ApiService);
  private readonly fetchedAt = new Map<string, number>();

  readonly statuses = signal<Record<string, ModelStatus>>({});
  readonly pending = signal(0);

  /** Fetch statuses for these models, skipping fresh ones. Pass `force` to re-read all. */
  ensure(modelIds: string[], force = false): void {
    const now = Date.now();
    const wanted = modelIds.filter(
      (id) => force || now - (this.fetchedAt.get(id) ?? 0) > FRESH_FOR_MS,
    );
    if (wanted.length === 0) return;
    // Mark first so overlapping calls do not fetch the same model twice.
    for (const id of wanted) this.fetchedAt.set(id, now);
    this.pending.update((count) => count + wanted.length);
    from(wanted)
      .pipe(
        mergeMap(
          (id) =>
            this.api.status(id).pipe(
              catchError(() => {
                this.fetchedAt.delete(id);
                this.pending.update((count) => count - 1);
                return EMPTY;
              }),
            ),
          PARALLEL,
        ),
      )
      .subscribe((status) => {
        this.statuses.update((current) => ({ ...current, [status.model_id]: status }));
        this.pending.update((count) => count - 1);
      });
  }

  get(modelId: string): ModelStatus | undefined {
    return this.statuses()[modelId];
  }
}
