import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { Router } from '@angular/router';

import { Deployment } from '../../api/types';
import { ApiService } from '../../core/api.service';
import { Loader } from '../../core/loader';
import { visibleTicker } from '../../core/ticker';
import { Icon } from '../../shared/icon';
import { EmptyState, ErrorPanel, Skeleton } from '../../shared/states';
import { DeploymentDetail } from './deployment-detail';
import { DeploymentTable } from './deployment-table';

type StatusGroup = 'all' | 'moving' | 'succeeded' | 'failed' | 'rolled_back';

const GROUPS: { key: StatusGroup; label: string; match: (d: Deployment) => boolean }[] = [
  { key: 'all', label: 'All', match: () => true },
  {
    key: 'moving',
    label: 'In flight',
    match: (d) => ['REQUESTED', 'VALIDATING', 'DEPLOYING'].includes(d.status),
  },
  { key: 'succeeded', label: 'Succeeded', match: (d) => d.status === 'SUCCEEDED' },
  { key: 'failed', label: 'Failed', match: (d) => d.status === 'FAILED' },
  { key: 'rolled_back', label: 'Rolled back', match: (d) => d.status === 'ROLLED_BACK' },
];

const ENVIRONMENTS = ['all', 'dev', 'staging', 'production'] as const;

@Component({
  selector: 'app-deployments',
  imports: [
    MatButtonModule,
    Icon,
    DeploymentTable,
    DeploymentDetail,
    ErrorPanel,
    EmptyState,
    Skeleton,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './deployments.scss',
  templateUrl: './deployments.html',
})
export class Deployments implements OnInit, OnDestroy {
  /** From the route /deployments/:id. */
  readonly id = input<string>();

  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  protected readonly groups = GROUPS;
  protected readonly environments = ENVIRONMENTS;
  protected readonly group = signal<StatusGroup>('all');
  protected readonly environment = signal<(typeof ENVIRONMENTS)[number]>('all');
  protected readonly text = signal('');
  protected readonly skeletons = [1, 2, 3, 4, 5];

  protected readonly list = new Loader(() => this.api.deployments({ limit: 200 }));

  protected readonly all = computed<Deployment[]>(() => this.list.data()?.items ?? []);
  protected readonly anyMoving = computed(() => this.all().some(GROUPS[1]!.match));

  protected readonly counts = computed(() => {
    const items = this.all();
    return Object.fromEntries(GROUPS.map((g) => [g.key, items.filter(g.match).length]));
  });

  protected readonly rows = computed(() => {
    const matcher = GROUPS.find((g) => g.key === this.group())!.match;
    const environment = this.environment();
    const needle = this.text().trim().toLowerCase();
    return this.all().filter(
      (d) =>
        matcher(d) &&
        (environment === 'all' || d.environment === environment) &&
        (!needle || d.model_id.toLowerCase().includes(needle) || d.version.includes(needle)),
    );
  });

  constructor() {
    // Refresh quickly while something is moving, slowly otherwise. Hidden tabs do not poll.
    visibleTicker(3000)
      .pipe(takeUntilDestroyed())
      .subscribe(() => {
        if (this.anyMoving()) this.list.refresh();
      });
    visibleTicker(20000)
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.list.refresh());
  }

  ngOnInit(): void {
    this.list.load();
  }

  ngOnDestroy(): void {
    this.list.destroy();
  }

  protected select(id: string): void {
    void this.router.navigate(['/deployments', id]);
  }

  protected close(): void {
    void this.router.navigate(['/deployments']);
  }

  protected setText(event: Event): void {
    this.text.set((event.target as HTMLInputElement).value);
  }

  protected clear(): void {
    this.group.set('all');
    this.environment.set('all');
    this.text.set('');
  }

  protected refresh(): void {
    this.list.load();
  }
}
