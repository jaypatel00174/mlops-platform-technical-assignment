import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';

import { Accepted } from '../../api/types';
import { ApiError } from '../../core/api-error';
import { ApiService, newIdempotencyKey } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Icon } from '../../shared/icon';
import { ErrorPanel } from '../../shared/states';

export interface DeployDialogData {
  modelId: string;
  version: string;
  approval: string;
  stage: string;
}

interface Choice {
  key: 'dev' | 'staging' | 'production';
  title: string;
  rule: string;
}

const CHOICES: Choice[] = [
  { key: 'dev', title: 'Dev', rule: 'No approval needed. Good for a first try.' },
  { key: 'staging', title: 'Staging', rule: 'The version must be approved.' },
  {
    key: 'production',
    title: 'Production',
    rule: 'Approved, and already succeeded in staging. Release managers only.',
  },
];

@Component({
  selector: 'app-deploy-dialog',
  imports: [MatDialogModule, MatButtonModule, Icon, ErrorPanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .choices {
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }
    .choice {
      all: unset;
      box-sizing: border-box;
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1.5px solid var(--border);
      cursor: pointer;
      transition:
        border-color 140ms ease,
        background 140ms ease;
    }
    .choice:hover:not([aria-disabled='true']) {
      border-color: color-mix(in srgb, var(--accent-blue) 50%, var(--border));
    }
    .choice:focus-visible {
      outline: 2px solid var(--focus);
      outline-offset: 2px;
    }
    .choice[aria-checked='true'] {
      border-color: var(--accent-blue);
      background: color-mix(in srgb, var(--accent-blue) 8%, transparent);
    }
    .choice[aria-disabled='true'] {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .radio {
      width: 20px;
      height: 20px;
      border-radius: 50%;
      border: 2px solid var(--border);
      display: grid;
      place-items: center;
      flex: none;
    }
    .choice[aria-checked='true'] .radio {
      border-color: var(--accent-blue);
    }
    .choice[aria-checked='true'] .radio::after {
      content: '';
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent-blue);
    }
    .text {
      display: grid;
    }
    .text small {
      color: var(--text-soft);
    }
    .note {
      margin-top: 14px;
      display: flex;
      gap: 8px;
      align-items: flex-start;
      padding: 10px 12px;
      border-radius: 12px;
      background: var(--warn-soft);
      color: var(--warn);
      font-size: 13px;
    }
  `,
  template: `
    <h2 mat-dialog-title>Deploy v{{ data.version }}</h2>
    <mat-dialog-content>
      <p class="help-text">
        Choose where to deploy <strong>{{ data.modelId }}</strong>. The request is recorded straight
        away and a worker does the rest; you can follow it live.
      </p>

      @if (data.approval !== 'APPROVED') {
        <div class="note" role="note">
          <app-icon name="info" [size]="16" />
          This version is not approved yet. Only dev will accept it until an approver signs off.
        </div>
      }

      <div class="choices" role="radiogroup" aria-label="Environment">
        @for (choice of choices; track choice.key) {
          <button
            type="button"
            class="choice"
            role="radio"
            [attr.aria-checked]="selected() === choice.key"
            [attr.aria-disabled]="!allowed(choice.key)"
            (click)="allowed(choice.key) && selected.set(choice.key)"
          >
            <span class="radio"></span>
            <span class="text">
              <strong>{{ choice.title }}</strong>
              <small>{{
                allowed(choice.key) ? choice.rule : 'Your role cannot deploy here.'
              }}</small>
            </span>
          </button>
        }
      </div>

      @if (problem(); as error) {
        <div style="margin-top: 14px"><app-error-panel [error]="error" (retry)="submit()" /></div>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close [disabled]="busy()">Cancel</button>
      <button mat-flat-button type="button" [disabled]="busy() || !selected()" (click)="submit()">
        {{ busy() ? 'Requesting…' : 'Deploy to ' + (selected() ?? '…') }}
      </button>
    </mat-dialog-actions>
  `,
})
export class DeployDialog {
  protected readonly data = inject<DeployDialogData>(MAT_DIALOG_DATA);
  private readonly ref = inject<MatDialogRef<DeployDialog, Accepted>>(MatDialogRef);
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly choices = CHOICES;
  protected readonly busy = signal(false);
  protected readonly problem = signal<ApiError | null>(null);

  /** One key for this dialog: pressing Deploy twice (or retrying after a timeout) cannot deploy twice. */
  private readonly idempotencyKey = newIdempotencyKey();

  protected readonly selected = signal<Choice['key'] | null>(
    this.allowed('dev') ? (this.data.approval === 'APPROVED' ? 'staging' : 'dev') : null,
  );

  protected readonly selectedChoice = computed(() =>
    CHOICES.find((c) => c.key === this.selected()),
  );

  protected allowed(key: Choice['key']): boolean {
    return this.auth.can(key === 'production' ? 'deployProduction' : 'deployNonProduction');
  }

  protected submit(): void {
    const environment = this.selected();
    if (!environment || this.busy()) return;
    this.busy.set(true);
    this.problem.set(null);
    this.api
      .requestDeployment(
        { model_id: this.data.modelId, version: this.data.version, environment },
        this.idempotencyKey,
      )
      .subscribe({
        next: (accepted) => this.ref.close(accepted),
        error: (error: unknown) => {
          this.busy.set(false);
          this.problem.set(ApiError.from(error));
        },
      });
  }
}
