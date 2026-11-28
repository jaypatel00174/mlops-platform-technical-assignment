import { ChangeDetectionStrategy, Component } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { RouterLink } from '@angular/router';

import { EmptyState } from '../../shared/states';

@Component({
  selector: 'app-not-found',
  imports: [EmptyState, MatButtonModule, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <app-empty
        icon="search"
        title="We could not find that page"
        message="The address may be mistyped, or the page may have moved."
      >
        <a mat-flat-button routerLink="/">Back to overview</a>
      </app-empty>
    </div>
  `,
})
export class NotFound {}
