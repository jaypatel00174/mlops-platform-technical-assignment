import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatMenuModule } from '@angular/material/menu';
import { Router } from '@angular/router';

import { Accepted, Model, ModelStatus, Version } from '../../api/types';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Notifications } from '../../core/notifications.service';
import { ConfirmData, ConfirmDialog, ConfirmResult } from '../../shared/confirm-dialog';
import { AgoPipe, DateTimePipe } from '../../shared/format';
import { Icon } from '../../shared/icon';
import { EmptyState } from '../../shared/states';
import { StatusChip } from '../../shared/status-chip';
import { DeployDialog, DeployDialogData } from './deploy-dialog';

type Action = 'validate' | 'approve' | 'reject' | 'archive';

@Component({
  selector: 'app-versions-tab',
  imports: [MatButtonModule, MatMenuModule, Icon, StatusChip, AgoPipe, DateTimePipe, EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './versions-tab.scss',
  templateUrl: './versions-tab.html',
})
export class VersionsTab {
  readonly model = input.required<Model>();
  readonly versions = input.required<Version[]>();
  readonly status = input<ModelStatus | null>(null);
  /** Tell the page to reload the model, its versions and its health. */
  readonly changed = output<void>();
  readonly register = output<void>();

  private readonly api = inject(ApiService);
  private readonly dialog = inject(MatDialog);
  private readonly notify = inject(Notifications);
  private readonly router = inject(Router);
  protected readonly auth = inject(AuthService);

  protected readonly expanded = signal<string | null>(null);
  protected readonly working = signal<string | null>(null);

  /** Newest first, using semantic version order. */
  protected readonly sorted = computed(() =>
    [...this.versions()].sort((a, b) => compareSemver(b.version, a.version)),
  );

  protected liveIn(version: string): string[] {
    return (this.status()?.items ?? [])
      .filter((item) => item.live && item.version === version)
      .map((item) => item.environment);
  }

  protected healthOf(version: string): string | null {
    const items = (this.status()?.items ?? []).filter(
      (item) => item.live && item.version === version,
    );
    return items.length ? items.map((i) => i.status).sort()[0]! : null;
  }

  // What each person may do with a version, mirroring the server's rules (which still decide).
  protected canValidate(v: Version): boolean {
    return v.stage === 'DRAFT' && this.auth.can('validate');
  }
  protected canDecide(v: Version): boolean {
    return v.stage === 'VALIDATED' && v.approval_status === 'PENDING' && this.auth.can('decide');
  }
  protected canDeploy(v: Version): boolean {
    return (
      v.stage !== 'ARCHIVED' &&
      v.approval_status !== 'REJECTED' &&
      (this.auth.can('deployNonProduction') || this.auth.can('deployProduction'))
    );
  }
  protected canArchive(v: Version): boolean {
    return (
      v.stage !== 'ARCHIVED' && this.auth.can('archive') && this.liveIn(v.version).length === 0
    );
  }
  protected hasActions(v: Version): boolean {
    return this.canValidate(v) || this.canDecide(v) || this.canDeploy(v) || this.canArchive(v);
  }

  protected toggle(version: string): void {
    this.expanded.update((current) => (current === version ? null : version));
  }

  protected copy(text: string): void {
    void navigator.clipboard?.writeText(text);
    this.notify.success('Copied');
  }

  protected run(version: Version, action: Action): void {
    const labels: Record<Action, string> = {
      validate: 'Validated',
      approve: 'Approved',
      reject: 'Rejected',
      archive: 'Archived',
    };
    this.working.set(version.version);
    this.api.versionAction(version.model_id, version.version, action).subscribe({
      next: () => {
        this.working.set(null);
        this.notify.success(`${labels[action]} v${version.version}`);
        this.changed.emit();
      },
      error: (error: unknown) => {
        this.working.set(null);
        this.notify.error(error, `Could not ${action} v${version.version}`);
      },
    });
  }

  protected decide(version: Version, action: 'approve' | 'reject'): void {
    const approve = action === 'approve';
    const data: ConfirmData = {
      title: `${approve ? 'Approve' : 'Reject'} v${version.version}?`,
      message: approve
        ? 'Approval lets this version go to staging and production. You cannot approve a version you registered yourself.'
        : 'A rejected version can never be deployed. Say why, so the author knows what to fix.',
      confirmLabel: approve ? 'Approve' : 'Reject',
      danger: !approve,
      commentLabel: approve ? 'Comment (optional)' : 'Reason',
    };
    this.dialog
      .open<ConfirmDialog, ConfirmData, ConfirmResult>(ConfirmDialog, {
        data,
        width: '480px',
        maxWidth: '95vw',
      })
      .afterClosed()
      .subscribe((result) => {
        if (!result) return;
        this.working.set(version.version);
        this.api
          .versionAction(version.model_id, version.version, action, result.comment || undefined)
          .subscribe({
            next: () => {
              this.working.set(null);
              this.notify.success(`${approve ? 'Approved' : 'Rejected'} v${version.version}`);
              this.changed.emit();
            },
            error: (error: unknown) => {
              this.working.set(null);
              this.notify.error(error, `Could not ${action} v${version.version}`);
            },
          });
      });
  }

  protected archive(version: Version): void {
    const data: ConfirmData = {
      title: `Archive v${version.version}?`,
      message: 'An archived version can no longer be deployed. This cannot be undone.',
      confirmLabel: 'Archive',
      danger: true,
    };
    this.dialog
      .open<ConfirmDialog, ConfirmData, ConfirmResult>(ConfirmDialog, {
        data,
        width: '460px',
        maxWidth: '95vw',
      })
      .afterClosed()
      .subscribe((result) => result && this.run(version, 'archive'));
  }

  protected deploy(version: Version): void {
    const data: DeployDialogData = {
      modelId: version.model_id,
      version: version.version,
      approval: version.approval_status,
      stage: version.stage,
    };
    this.dialog
      .open<DeployDialog, DeployDialogData, Accepted>(DeployDialog, {
        data,
        width: '520px',
        maxWidth: '95vw',
      })
      .afterClosed()
      .subscribe((accepted) => {
        if (!accepted) return;
        this.notify.success(`Deploying v${version.version} to ${accepted.environment}`);
        this.changed.emit();
        void this.router.navigate(['/deployments', accepted.deployment_id]);
      });
  }

  protected json(value: unknown): string {
    return JSON.stringify(value, null, 2);
  }

  protected hasMetadata(v: Version): boolean {
    return Object.keys(v.metadata ?? {}).length > 0;
  }
}

/** Order "1.10.0" after "1.2.0": compare the numbers, not the text. */
export function compareSemver(a: string, b: string): number {
  const parse = (value: string) =>
    value
      .split(/[-+]/)[0]!
      .split('.')
      .map((n) => Number.parseInt(n, 10) || 0);
  const left = parse(a);
  const right = parse(b);
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    const diff = (left[i] ?? 0) - (right[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}
