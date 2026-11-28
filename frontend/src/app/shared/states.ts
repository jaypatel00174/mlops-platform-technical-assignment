import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';

import { ApiError } from '../core/api-error';
import { Icon, IconName } from './icon';

/** What went wrong, in words, with a way to try again and a reference to quote to an operator. */
@Component({
  selector: 'app-error-panel',
  imports: [Icon, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="error-panel" role="alert">
      <div class="error-mark"><app-icon [name]="icon()" [size]="18" /></div>
      <div class="error-body">
        <h3>{{ headline() }}</h3>
        <p>{{ error().detail }}</p>
        @if (error().correlationId; as ref) {
          <p class="ref">
            Reference <code>{{ ref }}</code> · give this to an operator to find the request in the
            logs.
          </p>
        }
      </div>
      @if (retryable()) {
        <button mat-stroked-button type="button" (click)="retry.emit()">
          <app-icon name="refresh" [size]="16" /> Try again
        </button>
      }
    </div>
  `,
})
export class ErrorPanel {
  readonly error = input.required<ApiError>();
  readonly retry = output<void>();

  protected readonly icon = computed<IconName>(() => (this.error().isNetwork ? 'wifi' : 'alert'));
  protected readonly headline = computed(() => this.error().title);
  /** Retrying helps for network trouble and server faults, not for a refusal or a missing item. */
  protected readonly retryable = computed(() => {
    const { status } = this.error();
    return status === 0 || status >= 500 || status === 429;
  });
}

@Component({
  selector: 'app-empty',
  imports: [Icon],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="empty">
      <div class="empty-mark"><app-icon [name]="icon()" [size]="26" /></div>
      <h3>{{ title() }}</h3>
      <p>{{ message() }}</p>
      <ng-content />
    </div>
  `,
})
export class EmptyState {
  readonly icon = input<IconName>('sparkles');
  readonly title = input.required<string>();
  readonly message = input('');
}

/** A grey shimmering block shown while data loads, so the layout does not jump. */
@Component({
  selector: 'app-skeleton',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[style.height.px]': 'height()', '[style.width]': 'width()', class: 'skeleton' },
  styles: `
    :host {
      display: block;
    }
  `,
  template: '',
})
export class Skeleton {
  readonly height = input(16);
  readonly width = input('100%');
}
