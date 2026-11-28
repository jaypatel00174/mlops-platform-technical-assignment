import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  effect,
  inject,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { Deployment, HealthStatus, ModelStatus } from '../../api/types';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { HealthStore } from '../../core/health.store';
import { Loader } from '../../core/loader';
import { visibleTicker } from '../../core/ticker';
import { Donut, DonutSegment } from '../../shared/donut';
import { AgoPipe } from '../../shared/format';
import { Icon } from '../../shared/icon';
import { EmptyState, ErrorPanel, Skeleton } from '../../shared/states';
import { StatusChip } from '../../shared/status-chip';

const IN_FLIGHT = new Set(['REQUESTED', 'VALIDATING', 'DEPLOYING']);
const ENVIRONMENTS = ['dev', 'staging', 'production'] as const;

const HEALTH_COLORS: Record<HealthStatus, string> = {
  HEALTHY: 'var(--ok)',
  DEGRADED: 'var(--warn)',
  CRITICAL: 'var(--crit)',
  STALE: 'var(--stale)',
  NO_DATA: 'var(--muted)',
};
const HEALTH_LABELS: Record<HealthStatus, string> = {
  HEALTHY: 'Healthy',
  DEGRADED: 'Degraded',
  CRITICAL: 'Critical',
  STALE: 'Stale',
  NO_DATA: 'No data',
};
const URGENCY: HealthStatus[] = ['CRITICAL', 'DEGRADED', 'STALE', 'NO_DATA', 'HEALTHY'];

@Component({
  selector: 'app-overview',
  imports: [
    RouterLink,
    MatButtonModule,
    Icon,
    StatusChip,
    Donut,
    AgoPipe,
    ErrorPanel,
    EmptyState,
    Skeleton,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './overview.scss',
  templateUrl: './overview.html',
})
export class Overview implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  protected readonly router = inject(Router);
  protected readonly auth = inject(AuthService);
  protected readonly health = inject(HealthStore);

  protected readonly environments = ENVIRONMENTS;

  protected readonly data = new Loader(() =>
    forkJoin({
      models: this.api.models({ limit: 200 }),
      deployments: this.api.deployments({ limit: 50 }),
      dev: this.api.bindings('dev'),
      staging: this.api.bindings('staging'),
      production: this.api.bindings('production'),
    }),
  );

  constructor() {
    // Keep the deployment feed live: refresh every 5 seconds while anything is in flight.
    visibleTicker(5000)
      .pipe(takeUntilDestroyed())
      .subscribe(() => {
        if (this.inFlight().length > 0) this.data.refresh();
      });
    // Whenever the list of models changes, make sure we have a health status for each.
    effect(() => {
      const ids = this.models().map((m) => m.id);
      untracked(() => this.health.ensure(ids));
    });
  }

  ngOnInit(): void {
    this.data.load();
  }

  ngOnDestroy(): void {
    this.data.destroy();
  }

  protected readonly greeting = computed(() => {
    const hour = new Date().getHours();
    const part = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
    return `${part}, ${this.auth.subject()}`;
  });

  protected readonly models = computed(() => this.data.data()?.models.items ?? []);
  protected readonly deployments = computed<Deployment[]>(
    () => this.data.data()?.deployments.items ?? [],
  );
  protected readonly inFlight = computed(() =>
    this.deployments().filter((d) => IN_FLIGHT.has(d.status)),
  );
  protected readonly failed = computed(() =>
    this.deployments().filter((d) => d.status === 'FAILED'),
  );
  protected readonly recent = computed(() => this.deployments().slice(0, 7));

  protected readonly liveByEnvironment = computed(() => {
    const data = this.data.data();
    return ENVIRONMENTS.map((environment) => ({
      environment,
      live: data ? data[environment].items.filter((b) => b.current_deployment_id).length : 0,
    }));
  });

  /** Statuses for the models loaded here, in the order the donut and the list want them. */
  private readonly statuses = computed<ModelStatus[]>(() =>
    this.models()
      .map((model) => this.health.statuses()[model.id])
      .filter((status): status is ModelStatus => status !== undefined),
  );

  protected readonly segments = computed<DonutSegment[]>(() => {
    const counts = new Map<string, number>();
    for (const status of this.statuses())
      counts.set(status.status, (counts.get(status.status) ?? 0) + 1);
    return URGENCY.map((key) => ({
      key,
      label: HEALTH_LABELS[key],
      value: counts.get(key) ?? 0,
      color: HEALTH_COLORS[key],
    }));
  });

  protected readonly needsAttention = computed(() => {
    const names = new Map(this.models().map((m) => [m.id, m.name]));
    return this.statuses()
      .filter((s) => s.status === 'CRITICAL' || s.status === 'DEGRADED' || s.status === 'STALE')
      .sort(
        (a, b) =>
          URGENCY.indexOf(a.status as HealthStatus) - URGENCY.indexOf(b.status as HealthStatus),
      )
      .slice(0, 6)
      .map((status) => {
        const live = status.items.find((item) => item.live) ?? status.items[0];
        return {
          id: status.model_id,
          name: names.get(status.model_id) ?? status.model_id,
          status: status.status,
          why: live?.reasons[0]?.message ?? 'No details available.',
          version: live?.version,
        };
      });
  });

  protected readonly loadingStatuses = computed(() => this.health.pending() > 0);

  protected openModels(health: string): void {
    void this.router.navigate(['/models'], { queryParams: { health } });
  }

  protected refresh(): void {
    this.data.refresh();
    this.health.ensure(
      this.models().map((m) => m.id),
      true,
    );
  }
}
