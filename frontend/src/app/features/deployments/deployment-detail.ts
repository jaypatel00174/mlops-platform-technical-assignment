import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  computed,
  effect,
  inject,
  input,
  output,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { RouterLink } from '@angular/router';

import { Deployment } from '../../api/types';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Loader } from '../../core/loader';
import { Notifications } from '../../core/notifications.service';
import { visibleTicker } from '../../core/ticker';
import { ConfirmDialog, ConfirmData, ConfirmResult } from '../../shared/confirm-dialog';
import { AgoPipe, DateTimePipe } from '../../shared/format';
import { Icon } from '../../shared/icon';
import { ErrorPanel, Skeleton } from '../../shared/states';
import { StatusChip } from '../../shared/status-chip';

const IN_FLIGHT = new Set(['REQUESTED', 'VALIDATING', 'DEPLOYING']);

type StepState = 'done' | 'active' | 'todo' | 'failed';

const STEPS = [
  { key: 'REQUESTED', label: 'Requested', hint: 'Recorded and queued' },
  { key: 'VALIDATING', label: 'Validating', hint: 'Rules and artifact checked' },
  { key: 'DEPLOYING', label: 'Deploying', hint: 'Runtime is placing the model' },
  { key: 'DONE', label: 'Finished', hint: '' },
] as const;

const FAILURE_HELP: Record<string, { title: string; help: string }> = {
  POLICY: {
    title: 'A promotion rule refused it',
    help: 'Check that the version is approved and has succeeded in the earlier environment. Retrying will not help until that changes.',
  },
  VALIDATION: {
    title: 'The request or artifact was invalid',
    help: 'The artifact could not be verified against its recorded digest. Fix the artifact or register a corrected version.',
  },
  TRANSIENT: {
    title: 'A temporary problem',
    help: 'Timeouts and unavailable runtimes are retried automatically while the attempt budget lasts. Retry by hand once the runtime is healthy.',
  },
  PERMANENT_RUNTIME: {
    title: 'The runtime refused this model',
    help: 'Retrying will not change the answer. The model or the runtime configuration has to change first.',
  },
  INTERNAL: {
    title: 'An unexpected platform error',
    help: 'Give the reference below to an operator; it finds this request in the logs.',
  },
};

/** One deployment in depth: where it is, what happened, and what you can do about it. */
@Component({
  selector: 'app-deployment-detail',
  imports: [
    MatButtonModule,
    RouterLink,
    Icon,
    StatusChip,
    AgoPipe,
    DateTimePipe,
    ErrorPanel,
    Skeleton,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './deployment-detail.scss',
  templateUrl: './deployment-detail.html',
})
export class DeploymentDetail implements OnDestroy {
  readonly id = input.required<string>();
  /** Emitted after an action changed things, so the list can refresh. */
  readonly changed = output<void>();
  readonly closed = output<void>();
  /** A rollback creates a new deployment; the parent navigates to it. */
  readonly opened = output<string>();

  private readonly api = inject(ApiService);
  private readonly dialog = inject(MatDialog);
  private readonly notify = inject(Notifications);
  protected readonly auth = inject(AuthService);

  protected readonly steps = STEPS;

  protected readonly timeline = new Loader(() => this.api.timeline(this.id()));

  constructor() {
    effect(() => {
      this.id();
      untracked(() => this.timeline.load());
    });
    // Follow a deployment that is still moving, then stop.
    visibleTicker(2000)
      .pipe(takeUntilDestroyed())
      .subscribe(() => {
        if (this.isMoving()) {
          this.timeline.refresh();
          this.changed.emit();
        }
      });
  }

  ngOnDestroy(): void {
    this.timeline.destroy();
  }

  protected readonly deployment = computed<Deployment | null>(
    () => this.timeline.data()?.deployment ?? null,
  );
  protected readonly isMoving = computed(() => {
    const d = this.deployment();
    return d !== null && IN_FLIGHT.has(d.status);
  });

  /** Which step is current, and whether the run ended well. */
  protected readonly progress = computed<StepState[]>(() => {
    const d = this.deployment();
    if (!d) return STEPS.map(() => 'todo');
    const at = ['REQUESTED', 'VALIDATING', 'DEPLOYING'].indexOf(d.status);
    if (at >= 0) return STEPS.map((_, i) => (i < at ? 'done' : i === at ? 'active' : 'todo'));
    // Finished: every step is behind us, and the last one shows how it ended.
    return STEPS.map((_, i) => (i < 3 ? 'done' : d.status === 'FAILED' ? 'failed' : 'done'));
  });

  protected readonly failure = computed(() => {
    const d = this.deployment();
    return d?.failure_class ? (FAILURE_HELP[d.failure_class] ?? null) : null;
  });

  protected readonly canRetry = computed(() => {
    const d = this.deployment();
    if (!d || d.status !== 'FAILED') return false;
    return this.auth.can(
      d.environment === 'production' ? 'deployProduction' : 'deployNonProduction',
    );
  });

  protected readonly canRollback = computed(() => {
    const d = this.deployment();
    if (!d || d.status !== 'SUCCEEDED') return false;
    return this.auth.can(
      d.environment === 'production' ? 'deployProduction' : 'deployNonProduction',
    );
  });

  protected stepLabel(index: number): string {
    const d = this.deployment();
    if (index === 3 && d) {
      if (d.status === 'SUCCEEDED') return 'Succeeded';
      if (d.status === 'FAILED') return 'Failed';
      if (d.status === 'ROLLED_BACK') return 'Rolled back';
    }
    return STEPS[index]?.label ?? '';
  }

  protected nodeTone(action: string): 'ok' | 'crit' | 'info' | '' {
    if (action.includes('succeeded') || action.includes('requested'))
      return action.includes('succeeded') ? 'ok' : 'info';
    if (action.includes('failed')) return 'crit';
    if (action.includes('retry') || action.includes('reconciled') || action.includes('rolled'))
      return 'info';
    return '';
  }

  protected actionLabel(action: string): string {
    const labels: Record<string, string> = {
      'deployment.requested': 'Deployment requested',
      'deployment.succeeded': 'Deployed successfully',
      'deployment.failed': 'Attempt failed',
      'deployment.retry_scheduled': 'Retry scheduled automatically',
      'deployment.retried': 'Retried by a person',
      'deployment.reconciled': 'Recovered after a worker stopped',
      'deployment.rollback_requested': 'Rollback requested',
      'deployment.rolled_back': 'Rolled back',
    };
    return labels[action] ?? action.replace(/[._]/g, ' ');
  }

  protected copy(text: string): void {
    void navigator.clipboard?.writeText(text);
    this.notify.success('Copied');
  }

  protected retry(): void {
    const d = this.deployment();
    if (!d) return;
    this.api.retry(d.deployment_id).subscribe({
      next: () => {
        this.notify.success('Retry queued');
        this.timeline.refresh();
        this.changed.emit();
      },
      error: (error: unknown) => this.notify.error(error, 'Could not retry'),
    });
  }

  protected rollback(): void {
    const d = this.deployment();
    if (!d) return;
    const data: ConfirmData = {
      title: 'Roll back this deployment?',
      message:
        `This redeploys the version that was live before v${d.version} in ${d.environment}. ` +
        'The platform checks that the earlier version is approved and its artifact still verifies, and refuses if anything is unsafe.',
      confirmLabel: 'Roll back',
      danger: true,
    };
    this.dialog
      .open<ConfirmDialog, ConfirmData, ConfirmResult>(ConfirmDialog, {
        data,
        width: '460px',
        maxWidth: '95vw',
      })
      .afterClosed()
      .subscribe((result) => {
        if (!result) return;
        this.api.rollback(d.deployment_id).subscribe({
          next: (rollback) => {
            this.notify.success(`Rolling back to v${rollback.version}`);
            this.changed.emit();
            this.opened.emit(rollback.deployment_id);
          },
          error: (error: unknown) => this.notify.error(error, 'Rollback refused'),
        });
      });
  }
}
