import { Observable, filter, timer } from 'rxjs';

/** Ticks every `ms`, but stays silent while the tab is hidden so a forgotten tab costs nothing. */
export function visibleTicker(ms: number): Observable<number> {
  return timer(ms, ms).pipe(filter(() => !document.hidden));
}
