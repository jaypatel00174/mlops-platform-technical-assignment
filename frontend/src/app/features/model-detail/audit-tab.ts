import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { MatSelectModule } from '@angular/material/select';

import { AuditEvent, Version } from '../../api/types';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Loader } from '../../core/loader';
import { AgoPipe } from '../../shared/format';
import { EmptyState, ErrorPanel, Skeleton } from '../../shared/states';
import { compareSemver } from './versions-tab';

const LABELS: Record<string, string> = {
  'model.registered': 'Model registered',
  'version.registered': 'Version registered',
  'version.validated': 'Version validated',
  'version.approved': 'Version approved',
  'version.rejected': 'Version rejected',
  'version.archived': 'Version archived',
  'version.promoted': 'Stage promoted by a deployment',
};

@Component({
  selector: 'app-audit-tab',
  imports: [MatSelectModule, AgoPipe, ErrorPanel, EmptyState, Skeleton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (!auth.can('readAudit')) {
      <div class="card">
        <app-empty
          icon="shield"
          title="The audit trail is for approvers and admins"
          message="Every change is recorded, and people with the approver or admin role can read the history."
        />
      </div>
    } @else {
      <section class="controls">
        <label class="pick">
          <span class="soft">Show history of</span>
          <mat-select
            [value]="target()"
            (selectionChange)="target.set($event.value)"
            aria-label="Audit subject"
          >
            <mat-option value="model">The model itself</mat-option>
            @for (name of names(); track name) {
              <mat-option [value]="name">Version {{ name }}</mat-option>
            }
          </mat-select>
        </label>
      </section>

      <div class="card">
        @if (events.error(); as problem) {
          <app-error-panel [error]="problem" (retry)="events.load()" />
        } @else if (events.loading() && !events.data()) {
          <app-skeleton [height]="18" width="50%" /><app-skeleton
            [height]="18"
            width="70%"
          /><app-skeleton [height]="18" width="40%" />
        } @else if (items().length === 0) {
          <app-empty
            icon="file"
            title="No recorded changes"
            message="Nothing has been recorded for this yet."
          />
        } @else {
          <ol class="timeline">
            @for (e of items(); track e.id) {
              <li>
                <span class="node info"></span>
                <div class="spread">
                  <strong>{{ label(e.action) }}</strong>
                  <span class="soft" [title]="e.occurred_at">{{ e.occurred_at | ago }}</span>
                </div>
                <div class="soft">
                  by {{ e.actor }}
                  @if (changes(e); as text) {
                    · {{ text }}
                  }
                </div>
              </li>
            }
          </ol>
        }
      </div>
    }
  `,
  styles: `
    :host {
      display: grid;
      gap: 16px;
    }
    .pick {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      height: 40px;
      padding: 0 14px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--card);
      min-width: 260px;
    }
  `,
})
export class AuditTab implements OnDestroy {
  readonly modelId = input.required<string>();
  readonly versions = input.required<Version[]>();

  private readonly api = inject(ApiService);
  protected readonly auth = inject(AuthService);

  protected readonly target = signal<string>('model');
  protected readonly names = computed(() =>
    [...this.versions()].sort((a, b) => compareSemver(b.version, a.version)).map((v) => v.version),
  );

  protected readonly events = new Loader(() => {
    const target = this.target();
    return target === 'model'
      ? this.api.audit('model', this.modelId())
      : this.api.audit('model_version', `${this.modelId()}@${target}`);
  });

  /** Newest first reads better than the API's oldest-first order. */
  protected readonly items = computed<AuditEvent[]>(() =>
    [...(this.events.data()?.items ?? [])].reverse(),
  );

  constructor() {
    effect(() => {
      this.target();
      untracked(() => {
        if (this.auth.can('readAudit')) this.events.load();
      });
    });
  }

  ngOnDestroy(): void {
    this.events.destroy();
  }

  protected label(action: string): string {
    return LABELS[action] ?? action.replace(/[._]/g, ' ');
  }

  /** A short "stage DRAFT → VALIDATED" style summary of what changed. */
  protected changes(event: AuditEvent): string {
    const before = (event.before ?? {}) as Record<string, unknown>;
    const after = (event.after ?? {}) as Record<string, unknown>;
    const parts: string[] = [];
    for (const key of Object.keys(after)) {
      if (before[key] !== undefined && before[key] !== after[key]) {
        parts.push(`${key.replace(/_/g, ' ')} ${String(before[key])} → ${String(after[key])}`);
      }
    }
    return parts.join(', ');
  }
}
