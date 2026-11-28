import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatSelectModule } from '@angular/material/select';
import { Router, RouterLink } from '@angular/router';
import {
  EMPTY,
  catchError,
  combineLatest,
  debounceTime,
  distinctUntilChanged,
  switchMap,
  tap,
} from 'rxjs';

import { HealthStatus, Model } from '../../api/types';
import { ApiError } from '../../core/api-error';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { HealthStore } from '../../core/health.store';
import { Notifications } from '../../core/notifications.service';
import { AgoPipe, initials } from '../../shared/format';
import { Icon } from '../../shared/icon';
import { EmptyState, ErrorPanel, Skeleton } from '../../shared/states';
import { StatusChip } from '../../shared/status-chip';
import { RegisterModelDialog } from './register-model-dialog';

const PAGE_SIZE = 24;
const HEALTH_FILTERS: { key: HealthStatus | ''; label: string }[] = [
  { key: '', label: 'All' },
  { key: 'HEALTHY', label: 'Healthy' },
  { key: 'DEGRADED', label: 'Degraded' },
  { key: 'CRITICAL', label: 'Critical' },
  { key: 'STALE', label: 'Stale' },
  { key: 'NO_DATA', label: 'No data' },
];

/** A stable colour pair per model, so each card is recognisable at a glance. */
function gradientFor(id: string): string {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const hue = hash % 360;
  return `linear-gradient(135deg, hsl(${hue} 70% 52%), hsl(${(hue + 48) % 360} 72% 48%))`;
}

@Component({
  selector: 'app-models',
  imports: [
    RouterLink,
    MatButtonModule,
    MatSelectModule,
    Icon,
    StatusChip,
    AgoPipe,
    ErrorPanel,
    EmptyState,
    Skeleton,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './models.scss',
  templateUrl: './models.html',
})
export class Models {
  /** Optional `?health=CRITICAL` link from the overview. */
  readonly health = input<string>();

  private readonly api = inject(ApiService);
  private readonly dialog = inject(MatDialog);
  private readonly notify = inject(Notifications);
  private readonly router = inject(Router);
  protected readonly auth = inject(AuthService);
  protected readonly store = inject(HealthStore);

  protected readonly healthFilters = HEALTH_FILTERS;
  protected readonly skeletons = [1, 2, 3, 4, 5, 6];

  protected readonly search = signal('');
  protected readonly framework = signal('');
  protected readonly healthFilter = signal<HealthStatus | ''>('');

  protected readonly items = signal<Model[]>([]);
  protected readonly nextCursor = signal<string | null>(null);
  protected readonly loading = signal(true);
  protected readonly loadingMore = signal(false);
  protected readonly error = signal<ApiError | null>(null);
  private readonly knownFrameworks = signal<string[]>([]);

  protected readonly frameworks = this.knownFrameworks.asReadonly();
  protected readonly filtering = computed(
    () => this.search() !== '' || this.framework() !== '' || this.healthFilter() !== '',
  );

  /** Health filtering is done here because status is per model and comes from a separate call. */
  protected readonly visible = computed(() => {
    const wanted = this.healthFilter();
    if (!wanted) return this.items();
    return this.items().filter((model) => this.store.statuses()[model.id]?.status === wanted);
  });

  constructor() {
    combineLatest([toObservable(this.search), toObservable(this.framework)])
      .pipe(
        debounceTime(250),
        distinctUntilChanged(([a, b], [c, d]) => a === c && b === d),
        tap(() => {
          this.loading.set(true);
          this.error.set(null);
        }),
        // A failed search must not end the stream, or typing again would do nothing.
        switchMap(([q, framework]) =>
          this.api.models({ q, framework, limit: PAGE_SIZE }).pipe(
            catchError((error: unknown) => {
              this.fail(error);
              return EMPTY;
            }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((page) => this.accept(page.items, page.next_cursor, false));

    effect(() => {
      const wanted = this.health();
      if (wanted && HEALTH_FILTERS.some((f) => f.key === wanted)) {
        untracked(() => this.healthFilter.set(wanted as HealthStatus));
      }
    });

    effect(() => {
      const ids = this.items().map((model) => model.id);
      untracked(() => this.store.ensure(ids));
    });
  }

  protected loadMore(): void {
    const cursor = this.nextCursor();
    if (!cursor || this.loadingMore()) return;
    this.loadingMore.set(true);
    this.api
      .models({ q: this.search(), framework: this.framework(), limit: PAGE_SIZE, cursor })
      .subscribe({
        next: (page) => this.accept(page.items, page.next_cursor, true),
        error: (error: unknown) => {
          this.loadingMore.set(false);
          this.notify.error(error, 'Could not load more models');
        },
      });
  }

  protected reload(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.models({ q: this.search(), framework: this.framework(), limit: PAGE_SIZE }).subscribe({
      next: (page) => this.accept(page.items, page.next_cursor, false),
      error: (error: unknown) => this.fail(error),
    });
  }

  protected register(): void {
    this.dialog
      .open<RegisterModelDialog, void, Model>(RegisterModelDialog, {
        width: '560px',
        maxWidth: '95vw',
      })
      .afterClosed()
      .subscribe((model) => {
        if (!model) return;
        this.notify.success(`Registered ${model.name}`);
        void this.router.navigate(['/models', model.id]);
      });
  }

  protected clearFilters(): void {
    this.search.set('');
    this.framework.set('');
    this.healthFilter.set('');
  }

  protected readonly initials = initials;
  protected readonly gradient = gradientFor;

  private accept(page: Model[], cursor: string | null, append: boolean): void {
    this.items.update((current) => (append ? [...current, ...page] : page));
    this.nextCursor.set(cursor);
    this.knownFrameworks.update((known) =>
      [...new Set([...known, ...page.map((m) => m.framework)])].sort(),
    );
    this.loading.set(false);
    this.loadingMore.set(false);
  }

  private fail(error: unknown): void {
    this.error.set(ApiError.from(error));
    this.loading.set(false);
  }
}
