import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  computed,
  effect,
  inject,
  input,
  untracked,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { Version } from '../../api/types';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { HealthStore } from '../../core/health.store';
import { Loader } from '../../core/loader';
import { Notifications } from '../../core/notifications.service';
import { AgoPipe, initials } from '../../shared/format';
import { Icon } from '../../shared/icon';
import { ErrorPanel, Skeleton } from '../../shared/states';
import { StatusChip } from '../../shared/status-chip';
import { AuditTab } from './audit-tab';
import { CompareTab } from './compare-tab';
import { ModelDeploymentsTab } from './model-deployments-tab';
import { MonitoringTab } from './monitoring-tab';
import { RegisterVersionData, RegisterVersionDialog } from './register-version-dialog';
import { VersionsTab, compareSemver } from './versions-tab';

type TabKey = 'versions' | 'monitoring' | 'compare' | 'deployments' | 'audit';

const TABS: {
  key: TabKey;
  label: string;
  icon: 'layers' | 'pulse' | 'compare' | 'rocket' | 'file';
}[] = [
  { key: 'versions', label: 'Versions', icon: 'layers' },
  { key: 'monitoring', label: 'Monitoring', icon: 'pulse' },
  { key: 'compare', label: 'Compare', icon: 'compare' },
  { key: 'deployments', label: 'Deployments', icon: 'rocket' },
  { key: 'audit', label: 'Audit', icon: 'file' },
];

@Component({
  selector: 'app-model-detail',
  imports: [
    MatButtonModule,
    RouterLink,
    Icon,
    StatusChip,
    AgoPipe,
    ErrorPanel,
    Skeleton,
    VersionsTab,
    MonitoringTab,
    CompareTab,
    ModelDeploymentsTab,
    AuditTab,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './model-detail.scss',
  templateUrl: './model-detail.html',
})
export class ModelDetail implements OnDestroy {
  /** From the route /models/:id. */
  readonly id = input.required<string>();
  /** From the query string, so a tab can be linked to: /models/x?tab=monitoring */
  readonly tab = input<string>();

  private readonly api = inject(ApiService);
  private readonly dialog = inject(MatDialog);
  private readonly notify = inject(Notifications);
  private readonly router = inject(Router);
  protected readonly auth = inject(AuthService);
  protected readonly store = inject(HealthStore);

  protected readonly tabs = TABS;

  protected readonly page = new Loader(() =>
    forkJoin({ model: this.api.model(this.id()), versions: this.api.versions(this.id()) }),
  );

  protected readonly model = computed(() => this.page.data()?.model ?? null);
  protected readonly versions = computed<Version[]>(() => this.page.data()?.versions.items ?? []);
  protected readonly status = computed(() => this.store.statuses()[this.id()] ?? null);
  protected readonly active = computed<TabKey>(() => {
    const wanted = this.tab();
    return TABS.some((t) => t.key === wanted) ? (wanted as TabKey) : 'versions';
  });

  protected readonly nextVersion = computed(() => {
    const newest = [...this.versions()].sort((a, b) => compareSemver(b.version, a.version))[0];
    if (!newest) return '1.0.0';
    const parts = newest.version.split('.').map((n) => Number.parseInt(n, 10) || 0);
    return `${parts[0] ?? 1}.${parts[1] ?? 0}.${(parts[2] ?? 0) + 1}`;
  });

  constructor() {
    effect(() => {
      const id = this.id();
      untracked(() => {
        this.page.load();
        this.store.ensure([id], true);
      });
    });
  }

  ngOnDestroy(): void {
    this.page.destroy();
  }

  protected initials = initials;

  protected select(tab: TabKey): void {
    void this.router.navigate([], {
      queryParams: { tab },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /** Something changed (an approval, a deployment): re-read everything this page shows. */
  protected reload(): void {
    this.page.refresh();
    this.store.ensure([this.id()], true);
  }

  protected registerVersion(): void {
    const data: RegisterVersionData = { modelId: this.id(), suggested: this.nextVersion() };
    this.dialog
      .open<RegisterVersionDialog, RegisterVersionData, Version>(RegisterVersionDialog, {
        data,
        width: '640px',
        maxWidth: '95vw',
      })
      .afterClosed()
      .subscribe((version) => {
        if (!version) return;
        this.notify.success(`Registered v${version.version}`);
        this.reload();
        this.select('versions');
      });
  }
}
