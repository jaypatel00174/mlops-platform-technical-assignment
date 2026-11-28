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

import { METRICS, MetricKey, ModelStatus, Resolution, Series, Version } from '../../api/types';
import { ApiService } from '../../core/api.service';
import { Loader } from '../../core/loader';
import { ChartPoint } from '../../shared/chart-math';
import { AgoPipe, formatMetric } from '../../shared/format';
import { ChartLimit, LineChart } from '../../shared/line-chart';
import { EmptyState, ErrorPanel, Skeleton } from '../../shared/states';
import { StatusChip } from '../../shared/status-chip';
import { compareSemver } from './versions-tab';

type Preset = 'latest' | '24h' | '7d' | '30d' | 'all';

const DAY_MS = 86_400_000;

const PRESETS: { key: Preset; label: string; hint: string }[] = [
  { key: 'latest', label: 'Latest data', hint: 'The 30 days ending at the newest sample' },
  { key: '24h', label: '24 hours', hint: 'Relative to now' },
  { key: '7d', label: '7 days', hint: 'Relative to now' },
  { key: '30d', label: '30 days', hint: 'Relative to now' },
  { key: 'all', label: 'All time', hint: 'Everything stored, by day' },
];

const DEFAULT_RESOLUTION: Record<Preset, Resolution> = {
  latest: 'day',
  '24h': 'hour',
  '7d': 'hour',
  '30d': 'day',
  all: 'day',
};

const COLORS: Record<MetricKey, string> = {
  latency_ms: 'var(--accent-blue)',
  error_rate: 'var(--accent-rose)',
  throughput_rpm: 'var(--accent-teal)',
  quality_score: 'var(--accent-violet)',
  drift_score: 'var(--accent-amber)',
  availability: 'var(--ok)',
};

/**
 * The default health limits, in the units the charts show. They mirror docs/monitoring.md so a
 * line on a chart sits where the platform changes its verdict. The server decides the status;
 * these only draw the lines.
 */
const LIMITS: Partial<Record<MetricKey, ChartLimit[]>> = {
  latency_ms: [
    { value: 200, label: 'degraded', tone: 'warn' },
    { value: 500, label: 'critical', tone: 'crit' },
  ],
  error_rate: [
    { value: 2, label: 'degraded', tone: 'warn' },
    { value: 5, label: 'critical', tone: 'crit' },
  ],
  quality_score: [
    { value: 0.8, label: 'degraded', tone: 'warn' },
    { value: 0.7, label: 'critical', tone: 'crit' },
  ],
  drift_score: [
    { value: 0.3, label: 'degraded', tone: 'warn' },
    { value: 0.5, label: 'critical', tone: 'crit' },
  ],
  availability: [
    { value: 99, label: 'degraded', tone: 'warn' },
    { value: 95, label: 'critical', tone: 'crit' },
  ],
};

const ENVIRONMENTS = ['production', 'staging', 'dev'];

@Component({
  selector: 'app-monitoring-tab',
  imports: [MatSelectModule, StatusChip, AgoPipe, LineChart, ErrorPanel, EmptyState, Skeleton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './monitoring-tab.scss',
  templateUrl: './monitoring-tab.html',
})
export class MonitoringTab implements OnDestroy {
  readonly modelId = input.required<string>();
  readonly versions = input.required<Version[]>();
  readonly status = input<ModelStatus | null>(null);

  private readonly api = inject(ApiService);

  protected readonly presets = PRESETS;
  protected readonly metrics = METRICS;
  protected readonly resolutions: { key: Resolution; label: string }[] = [
    { key: 'raw', label: 'Every sample' },
    { key: 'hour', label: 'Hourly' },
    { key: 'day', label: 'Daily' },
  ];

  protected readonly version = signal<string | null>(null);
  protected readonly environment = signal<string | null>(null);
  protected readonly preset = signal<Preset>('latest');
  private readonly resolutionChoice = signal<Resolution | null>(null);

  protected readonly resolution = computed(
    () => this.resolutionChoice() ?? DEFAULT_RESOLUTION[this.preset()],
  );

  protected readonly versionNames = computed(() =>
    [...this.versions()].sort((a, b) => compareSemver(b.version, a.version)).map((v) => v.version),
  );

  /** Environments worth offering for the chosen version: where it has data or is live. */
  protected readonly environmentNames = computed(() => {
    const known = (this.status()?.items ?? [])
      .filter((item) => item.version === this.version())
      .map((item) => item.environment);
    return [...new Set([...known, ...ENVIRONMENTS])];
  });

  /** The health entry for the selected version and environment, if the platform has one. */
  protected readonly health = computed(() =>
    (this.status()?.items ?? []).find(
      (item) => item.version === this.version() && item.environment === this.environment(),
    ),
  );

  /** The time window to ask for. Null when "latest data" is chosen but nothing has been received. */
  private readonly window = computed(() => {
    const preset = this.preset();
    const now = Date.now();
    if (preset === 'latest') {
      const last = this.health()?.last_sample_at;
      if (!last) return null;
      const end = Date.parse(last) + 1000;
      return { from: new Date(end - 30 * DAY_MS).toISOString(), to: new Date(end).toISOString() };
    }
    if (preset === 'all') return { from: '2000-01-01T00:00:00Z', to: new Date(now).toISOString() };
    const span = preset === '24h' ? DAY_MS : preset === '7d' ? 7 * DAY_MS : 30 * DAY_MS;
    return { from: new Date(now - span).toISOString(), to: new Date(now).toISOString() };
  });

  protected readonly series = new Loader<Series>(() => {
    const window = this.window();
    return this.api.series(this.modelId(), {
      version: this.version()!,
      environment: this.environment()!,
      resolution: this.resolution(),
      from: window?.from,
      to: window?.to,
    });
  });

  protected readonly hasWindow = computed(() => this.window() !== null);

  protected readonly cards = computed(() => {
    const data = this.series.data();
    if (!data) return [];
    return METRICS.map((metric) => {
      const scale = 'scale' in metric ? metric.scale : 1;
      const points: ChartPoint[] = data.points.map((p) => ({
        t: Date.parse(p.ts),
        v: p[metric.key] * scale,
        n: p.samples,
      }));
      const stat = data.summary?.metrics[metric.key];
      const last = points[points.length - 1]?.v;
      return {
        key: metric.key,
        label: metric.label,
        color: COLORS[metric.key],
        limits: LIMITS[metric.key] ?? [],
        points,
        latest: last === undefined ? null : last / scale,
        mean: stat ? stat.mean : null,
        min: stat ? stat.minimum : null,
        max: stat ? stat.maximum : null,
        format: (value: number) => formatChartValue(metric.key, value),
      };
    });
  });

  constructor() {
    // Choose a sensible starting version and environment once health and versions are known.
    effect(() => {
      const versions = this.versionNames();
      const status = this.status();
      untracked(() => {
        if (this.version() !== null || versions.length === 0) return;
        const live =
          status?.items.find((i) => i.live && i.environment === 'production') ??
          status?.items.find((i) => i.live);
        const withData = status?.items.find((i) => i.last_sample_at);
        const pick = live ?? withData;
        this.version.set(pick?.version ?? versions[0] ?? null);
        this.environment.set(pick?.environment ?? 'production');
      });
    });

    // Reload whenever the selection changes.
    effect(() => {
      this.version();
      this.environment();
      this.preset();
      this.resolution();
      this.window();
      untracked(() => {
        if (this.version() && this.environment() && this.hasWindow()) this.series.load();
      });
    });
  }

  ngOnDestroy(): void {
    this.series.destroy();
  }

  protected pickVersion(version: string): void {
    this.version.set(version);
    // Keep the environment if this version has data there, otherwise follow the version.
    const items = (this.status()?.items ?? []).filter((i) => i.version === version);
    if (items.length && !items.some((i) => i.environment === this.environment())) {
      this.environment.set((items.find((i) => i.live) ?? items[0])!.environment);
    }
  }

  protected pickPreset(preset: Preset): void {
    this.preset.set(preset);
    this.resolutionChoice.set(null);
  }

  protected setResolution(resolution: Resolution): void {
    this.resolutionChoice.set(resolution);
  }

  protected display(key: MetricKey, value: number | null): string {
    return formatMetric(key, value);
  }
}

/** Axis and tooltip text. Values are already in display units (error rate as a percentage). */
function formatChartValue(key: MetricKey, value: number): string {
  const metric = METRICS.find((m) => m.key === key)!;
  const text = value.toLocaleString(undefined, {
    maximumFractionDigits: metric.decimals,
    minimumFractionDigits: 0,
  });
  return metric.unit === '%' ? `${text}%` : text;
}
