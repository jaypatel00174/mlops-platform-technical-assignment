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

import { Comparison, METRICS, MetricKey, Version } from '../../api/types';
import { ApiService } from '../../core/api.service';
import { Loader } from '../../core/loader';
import { DateTimePipe, formatMetric } from '../../shared/format';
import { Icon } from '../../shared/icon';
import { EmptyState, ErrorPanel, Skeleton } from '../../shared/states';
import { compareSemver } from './versions-tab';

/** For each metric, which direction is an improvement. Throughput is treated as "more is better". */
const BETTER: Record<MetricKey, 'lower' | 'higher'> = {
  latency_ms: 'lower',
  error_rate: 'lower',
  drift_score: 'lower',
  throughput_rpm: 'higher',
  quality_score: 'higher',
  availability: 'higher',
};

type Verdict = 'better' | 'worse' | 'same';

@Component({
  selector: 'app-compare-tab',
  imports: [MatSelectModule, Icon, DateTimePipe, ErrorPanel, EmptyState, Skeleton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './compare-tab.scss',
  templateUrl: './compare-tab.html',
})
export class CompareTab implements OnDestroy {
  readonly modelId = input.required<string>();
  readonly versions = input.required<Version[]>();

  private readonly api = inject(ApiService);

  protected readonly a = signal<string | null>(null);
  protected readonly b = signal<string | null>(null);
  protected readonly environment = signal('production');
  protected readonly environments = ['production', 'staging', 'dev'];

  protected readonly names = computed(() =>
    [...this.versions()].sort((x, y) => compareSemver(x.version, y.version)).map((v) => v.version),
  );

  protected readonly result = new Loader<Comparison>(() =>
    this.api.compare(this.modelId(), {
      a: this.a()!,
      b: this.b()!,
      environment: this.environment(),
    }),
  );

  protected readonly canCompare = computed(
    () => this.a() !== null && this.b() !== null && this.a() !== this.b(),
  );

  protected readonly rows = computed(() => {
    const data = this.result.data();
    if (!data?.deltas) return [];
    return METRICS.map((metric) => {
      const delta = data.deltas![metric.key];
      if (!delta) return null;
      const lowerIsBetter = BETTER[metric.key] === 'lower';
      const tiny = Math.abs(delta.change) < Math.max(Math.abs(delta.a), 1e-9) * 0.005;
      const verdict: Verdict = tiny
        ? 'same'
        : delta.change < 0 === lowerIsBetter
          ? 'better'
          : 'worse';
      const pct = delta.change_pct;
      return {
        key: metric.key,
        label: metric.label,
        a: formatMetric(metric.key, delta.a),
        b: formatMetric(metric.key, delta.b),
        change: pct === null ? 'n/a' : `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`,
        verdict,
        // The bar shows the size of the change, capped so one wild metric does not flatten the rest.
        width: pct === null ? 0 : Math.min(Math.abs(pct), 50) * 2,
        up: delta.change > 0,
      };
    }).filter((row) => row !== null);
  });

  protected readonly summary = computed(() => {
    const rows = this.rows();
    return {
      better: rows.filter((r) => r.verdict === 'better').length,
      worse: rows.filter((r) => r.verdict === 'worse').length,
    };
  });

  constructor() {
    // Start with the two newest versions: the usual question is "did the new one improve?".
    effect(() => {
      const names = this.names();
      untracked(() => {
        if (this.a() === null && names.length >= 2) {
          this.a.set(names[names.length - 2]!);
          this.b.set(names[names.length - 1]!);
        }
      });
    });
    effect(() => {
      this.a();
      this.b();
      this.environment();
      untracked(() => {
        if (this.canCompare()) this.result.load();
      });
    });
  }

  ngOnDestroy(): void {
    this.result.destroy();
  }

  protected metadataRows(data: Comparison): { label: string; a: string; b: string }[] {
    const pairs: [string, (v: Comparison['a']) => string][] = [
      ['Stage', (v) => v.stage],
      ['Approval', (v) => v.approval_status],
      ['Algorithm', (v) => v.algorithm],
      ['Runtime', (v) => v.runtime_type],
      ['Registered by', (v) => v.registered_by],
      ['Training data', (v) => v.training_data_ref],
    ];
    return pairs.map(([label, read]) => ({ label, a: read(data.a), b: read(data.b) }));
  }
}
