import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  inject,
  input,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { Loader } from '../../core/loader';
import { visibleTicker } from '../../core/ticker';
import { DeploymentTable } from '../deployments/deployment-table';
import { EmptyState, ErrorPanel, Skeleton } from '../../shared/states';

/** The deployments of one model, following any that are still moving. */
@Component({
  selector: 'app-model-deployments-tab',
  imports: [DeploymentTable, EmptyState, ErrorPanel, Skeleton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="card flush">
      @if (list.error(); as problem) {
        <div style="padding: 16px"><app-error-panel [error]="problem" (retry)="list.load()" /></div>
      } @else if (list.loading() && !list.data()) {
        <div style="padding: 16px; display: grid; gap: 10px">
          <app-skeleton [height]="40" /><app-skeleton [height]="40" /><app-skeleton [height]="40" />
        </div>
      } @else if (items().length === 0) {
        <app-empty
          icon="rocket"
          title="Not deployed yet"
          message="Deploy a version from the Versions tab. It will show up here and update live."
        />
      } @else {
        <app-deployment-table [deployments]="items()" [showModel]="false" (select)="open($event)" />
      }
    </div>
  `,
})
export class ModelDeploymentsTab implements OnInit, OnDestroy {
  readonly modelId = input.required<string>();

  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  protected readonly list = new Loader(() =>
    this.api.deployments({ model_id: this.modelId(), limit: 100 }),
  );
  protected readonly items = computed(() => this.list.data()?.items ?? []);

  constructor() {
    visibleTicker(3000)
      .pipe(takeUntilDestroyed())
      .subscribe(() => {
        if (this.items().some((d) => ['REQUESTED', 'VALIDATING', 'DEPLOYING'].includes(d.status))) {
          this.list.refresh();
        }
      });
  }

  ngOnInit(): void {
    this.list.load();
  }

  ngOnDestroy(): void {
    this.list.destroy();
  }

  protected open(id: string): void {
    void this.router.navigate(['/deployments', id]);
  }
}
