import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Deployment } from '../../api/types';
import { AgoPipe } from '../../shared/format';
import { Icon } from '../../shared/icon';
import { StatusChip } from '../../shared/status-chip';

/** Rows of deployments. Selecting a row emits its id; the parent decides what to show. */
@Component({
  selector: 'app-deployment-table',
  imports: [StatusChip, AgoPipe, Icon, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            @if (showModel()) {
              <th>Model</th>
            } @else {
              <th>Version</th>
            }
            <th>Environment</th>
            <th>Status</th>
            @if (!compact()) {
              <th>Attempt</th>
              <th>Requested by</th>
            }
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          @for (d of deployments(); track d.deployment_id) {
            <tr
              class="clickable"
              [class.selected]="d.deployment_id === selectedId()"
              tabindex="0"
              (click)="select.emit(d.deployment_id)"
              (keydown.enter)="select.emit(d.deployment_id)"
              [attr.aria-selected]="d.deployment_id === selectedId()"
            >
              <td>
                @if (showModel()) {
                  <a [routerLink]="['/models', d.model_id]" (click)="$event.stopPropagation()"
                    ><strong>{{ d.model_id }}</strong></a
                  >
                  <div class="soft num">v{{ d.version }}</div>
                } @else {
                  <strong class="num">v{{ d.version }}</strong>
                }
                @if (d.type === 'ROLLBACK') {
                  <div class="soft rb"><app-icon name="undo" [size]="13" /> rollback</div>
                }
              </td>
              <td>
                <span class="env-pill" [class]="'env-pill ' + d.environment">{{
                  d.environment
                }}</span>
              </td>
              <td><app-chip kind="deployment" [value]="d.status" /></td>
              @if (!compact()) {
                <td class="num">{{ d.attempt }}</td>
                <td class="soft">{{ d.requested_by }}</td>
              }
              <td class="soft" [title]="d.updated_at">{{ d.updated_at | ago }}</td>
            </tr>
          }
        </tbody>
      </table>
    </div>
  `,
  styles: `
    td:not(:first-child) {
      white-space: nowrap;
    }
    td:first-child {
      overflow-wrap: anywhere;
      min-width: 150px;
    }
    .rb {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 12px;
    }
  `,
})
export class DeploymentTable {
  readonly deployments = input.required<Deployment[]>();
  readonly selectedId = input<string | null>(null);
  readonly showModel = input(true);
  /** Drop the less important columns when the table shares the screen with a detail panel. */
  readonly compact = input(false);
  readonly select = output<string>();
}
