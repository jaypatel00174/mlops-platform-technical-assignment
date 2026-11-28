import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { Icon, IconName } from './icon';

export type ChipKind = 'health' | 'deployment' | 'stage' | 'approval';
type Tone = 'ok' | 'warn' | 'crit' | 'info' | 'stale' | 'muted';

interface Look {
  tone: Tone;
  label: string;
  live?: boolean;
  icon?: IconName;
}

const LOOKS: Record<ChipKind, Record<string, Look>> = {
  health: {
    HEALTHY: { tone: 'ok', label: 'Healthy', icon: 'check' },
    DEGRADED: { tone: 'warn', label: 'Degraded', icon: 'alert' },
    CRITICAL: { tone: 'crit', label: 'Critical', icon: 'alert' },
    STALE: { tone: 'stale', label: 'Stale', icon: 'clock' },
    NO_DATA: { tone: 'muted', label: 'No data', icon: 'info' },
  },
  deployment: {
    REQUESTED: { tone: 'info', label: 'Requested', live: true },
    VALIDATING: { tone: 'info', label: 'Validating', live: true },
    DEPLOYING: { tone: 'info', label: 'Deploying', live: true },
    SUCCEEDED: { tone: 'ok', label: 'Succeeded', icon: 'check' },
    FAILED: { tone: 'crit', label: 'Failed', icon: 'close' },
    ROLLED_BACK: { tone: 'warn', label: 'Rolled back', icon: 'undo' },
  },
  stage: {
    DRAFT: { tone: 'muted', label: 'Draft' },
    VALIDATED: { tone: 'info', label: 'Validated' },
    APPROVED: { tone: 'ok', label: 'Approved' },
    STAGING: { tone: 'info', label: 'Staging' },
    PRODUCTION: { tone: 'warn', label: 'Production' },
    ARCHIVED: { tone: 'muted', label: 'Archived' },
  },
  approval: {
    PENDING: { tone: 'muted', label: 'Pending review' },
    APPROVED: { tone: 'ok', label: 'Approved', icon: 'check' },
    REJECTED: { tone: 'crit', label: 'Rejected', icon: 'close' },
  },
};

/** One coloured pill for any status the platform reports, so every screen speaks the same way. */
@Component({
  selector: 'app-chip',
  imports: [Icon],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="chip" [class]="'chip ' + look().tone + (look().live ? ' live' : '')">
    @if (look().icon; as icon) {
      <app-icon [name]="icon" [size]="13" />
    } @else {
      <span class="dot"></span>
    }
    {{ look().label }}
  </span>`,
})
export class StatusChip {
  readonly kind = input.required<ChipKind>();
  readonly value = input.required<string>();

  protected readonly look = computed<Look>(
    () =>
      LOOKS[this.kind()][this.value()] ?? {
        tone: 'muted',
        label: this.value().toLowerCase().replace(/_/g, ' '),
      },
  );
}
