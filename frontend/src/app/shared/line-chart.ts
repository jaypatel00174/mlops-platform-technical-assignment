import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';

import { ChartPoint, domainWithLimits, nearestIndex, niceScale, timeFormatter } from './chart-math';

export interface ChartLimit {
  value: number;
  label: string;
  tone: 'warn' | 'crit';
}

let nextId = 0;

/**
 * A responsive line chart in plain SVG: no charting library to ship, update or audit.
 *
 * Hover (or touch, or the arrow keys) moves a crosshair and shows the exact value and time.
 * Limit lines, such as the health thresholds, are drawn when they are close to the data.
 */
@Component({
  selector: 'app-line-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[style.--series]': 'color()',
    tabindex: '0',
    role: 'img',
    '[attr.aria-label]': 'summary()',
    '(keydown)': 'onKey($event)',
    '(blur)': 'active.set(null)',
  },
  styles: `
    :host {
      display: block;
      position: relative;
      width: 100%;
      outline-offset: 4px;
    }
    svg {
      display: block;
      overflow: visible;
      touch-action: pan-y;
    }
    .grid {
      stroke: var(--border);
      stroke-width: 1;
    }
    .axis {
      fill: var(--text-soft);
      font-size: 11px;
      font-variant-numeric: tabular-nums;
    }
    .line {
      fill: none;
      stroke: var(--series);
      stroke-width: 2.2;
      stroke-linejoin: round;
      stroke-linecap: round;
    }
    .area {
      fill: url(#fill);
    }
    .dot {
      fill: var(--card);
      stroke: var(--series);
      stroke-width: 2;
    }
    .cross {
      stroke: var(--text-soft);
      stroke-dasharray: 3 3;
    }
    .limit {
      stroke-width: 1.2;
      stroke-dasharray: 5 4;
    }
    .limit.warn {
      stroke: var(--warn);
    }
    .limit.crit {
      stroke: var(--crit);
    }
    .limit-label {
      font-size: 10.5px;
      font-weight: 600;
    }
    .limit-label.warn {
      fill: var(--warn);
    }
    .limit-label.crit {
      fill: var(--crit);
    }
    .tip {
      position: absolute;
      top: 0;
      pointer-events: none;
      transform: translateX(-50%);
      background: var(--card-raised);
      border: 1px solid var(--border);
      box-shadow: var(--shadow-md);
      border-radius: 10px;
      padding: 6px 10px;
      font-size: 12px;
      white-space: nowrap;
      z-index: 2;
    }
    .tip strong {
      display: block;
      font-size: 14px;
      font-variant-numeric: tabular-nums;
    }
    .tip span {
      color: var(--text-soft);
    }
    .none {
      display: grid;
      place-items: center;
      color: var(--text-soft);
    }
  `,
  template: `
    @if (points().length === 0) {
      <div class="none" [style.height.px]="height()">No data in this range</div>
    } @else {
      <svg
        [attr.width]="width()"
        [attr.height]="height()"
        [attr.viewBox]="'0 0 ' + width() + ' ' + height()"
        (pointermove)="onPointer($event)"
        (pointerleave)="active.set(null)"
        (pointerdown)="onPointer($event)"
      >
        <defs>
          <linearGradient [attr.id]="'fill' + uid" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--series)" stop-opacity="0.28" />
            <stop offset="100%" stop-color="var(--series)" stop-opacity="0" />
          </linearGradient>
        </defs>

        @for (tick of yTicks(); track tick.value) {
          <line
            class="grid"
            [attr.x1]="pad.l"
            [attr.x2]="width() - pad.r"
            [attr.y1]="tick.y"
            [attr.y2]="tick.y"
          />
          <text class="axis" [attr.x]="pad.l - 8" [attr.y]="tick.y + 4" text-anchor="end">
            {{ tick.label }}
          </text>
        }
        @for (tick of xTicks(); track tick.t) {
          <text class="axis" [attr.x]="tick.x" [attr.y]="height() - 6" text-anchor="middle">
            {{ tick.label }}
          </text>
        }

        @for (limit of visibleLimits(); track limit.label) {
          <line
            class="limit"
            [class]="'limit ' + limit.tone"
            [attr.x1]="pad.l"
            [attr.x2]="width() - pad.r"
            [attr.y1]="limit.y"
            [attr.y2]="limit.y"
          />
          <text
            class="limit-label"
            [class]="'limit-label ' + limit.tone"
            [attr.x]="width() - pad.r - 4"
            [attr.y]="limit.y - 4"
            text-anchor="end"
          >
            {{ limit.label }}
          </text>
        }

        @if (coords().length > 1) {
          <path class="area" [attr.d]="areaPath()" [attr.fill]="'url(#fill' + uid + ')'" />
          <path class="line" [attr.d]="linePath()" />
        }
        @if (coords().length <= 40) {
          @for (c of coords(); track c.t) {
            <circle class="dot" [attr.cx]="c.x" [attr.cy]="c.y" r="3" />
          }
        }

        @if (activePoint(); as a) {
          <line
            class="cross"
            [attr.x1]="a.x"
            [attr.x2]="a.x"
            [attr.y1]="pad.t"
            [attr.y2]="height() - pad.b"
          />
          <circle class="dot" [attr.cx]="a.x" [attr.cy]="a.y" r="5" />
        }
      </svg>

      @if (activePoint(); as a) {
        <div class="tip" [style.left.px]="tipLeft()" aria-live="polite">
          <strong>{{ format()(a.v) }}</strong>
          <span
            >{{ a.when }}
            @if (a.n && a.n > 1) {
              · {{ a.n }} samples
            }
          </span>
        </div>
      }
    }
  `,
})
export class LineChart {
  readonly points = input.required<ChartPoint[]>();
  readonly format = input<(value: number) => string>((value) => String(value));
  readonly color = input('var(--accent-blue)');
  readonly limits = input<ChartLimit[]>([]);
  readonly height = input(190);
  readonly label = input('Chart');

  protected readonly uid = nextId++;
  protected readonly pad = { l: 52, r: 14, t: 14, b: 26 };
  protected readonly width = signal(480);
  protected readonly active = signal<number | null>(null);

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  constructor() {
    const element = this.host.nativeElement;
    this.width.set(Math.max(element.clientWidth, 240));
    const observer = new ResizeObserver(() => this.width.set(Math.max(element.clientWidth, 240)));
    observer.observe(element);
    inject(DestroyRef).onDestroy(() => observer.disconnect());
    // A new series should not keep pointing at an index that no longer exists.
    effect(() => {
      this.points();
      this.active.set(null);
    });
  }

  private readonly yScale = computed(() => {
    const values = this.points().map((p) => p.v);
    const { min, max } = domainWithLimits(
      values,
      this.limits().map((l) => l.value),
    );
    return niceScale(min, max, 4);
  });

  private readonly xDomain = computed(() => {
    const times = this.points().map((p) => p.t);
    const min = Math.min(...times);
    const max = Math.max(...times);
    return { min, max: max === min ? min + 1 : max };
  });

  private toX(t: number): number {
    const { min, max } = this.xDomain();
    const inner = this.width() - this.pad.l - this.pad.r;
    // One point sits in the middle; several span the full width.
    return this.points().length === 1
      ? this.pad.l + inner / 2
      : this.pad.l + ((t - min) / (max - min)) * inner;
  }

  private toY(v: number): number {
    const { domain } = this.yScale();
    const inner = this.height() - this.pad.t - this.pad.b;
    return this.pad.t + (1 - (v - domain.min) / (domain.max - domain.min)) * inner;
  }

  protected readonly coords = computed(() => {
    const full = new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
    return this.points().map((p) => ({
      ...p,
      x: this.toX(p.t),
      y: this.toY(p.v),
      when: full.format(p.t),
    }));
  });

  protected readonly linePath = computed(() =>
    this.coords()
      .map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(1)},${c.y.toFixed(1)}`)
      .join(' '),
  );

  protected readonly areaPath = computed(() => {
    const cs = this.coords();
    if (cs.length === 0) return '';
    const base = this.height() - this.pad.b;
    return `${this.linePath()} L${cs[cs.length - 1]!.x.toFixed(1)},${base} L${cs[0]!.x.toFixed(1)},${base} Z`;
  });

  protected readonly yTicks = computed(() =>
    this.yScale().ticks.map((value) => ({
      value,
      y: this.toY(value),
      label: this.format()(value),
    })),
  );

  protected readonly xTicks = computed(() => {
    const { min, max } = this.xDomain();
    if (this.points().length === 1) {
      const only = this.points()[0]!;
      return [{ t: only.t, x: this.toX(only.t), label: timeFormatter(0)(only.t) }];
    }
    const fmt = timeFormatter(max - min);
    const count = Math.max(2, Math.min(5, Math.floor((this.width() - this.pad.l) / 110)));
    return Array.from({ length: count }, (_, i) => {
      const t = min + ((max - min) * i) / (count - 1);
      return { t, x: this.toX(t), label: fmt(t) };
    });
  });

  protected readonly visibleLimits = computed(() => {
    const { domain } = this.yScale();
    return this.limits()
      .filter((l) => l.value >= domain.min && l.value <= domain.max)
      .map((l) => ({ ...l, y: this.toY(l.value) }));
  });

  protected readonly activePoint = computed(() => {
    const index = this.active();
    return index === null ? null : (this.coords()[index] ?? null);
  });

  protected readonly tipLeft = computed(() => {
    const point = this.activePoint();
    if (!point) return 0;
    return Math.min(Math.max(point.x, 70), this.width() - 70);
  });

  protected readonly summary = computed(() => {
    const points = this.points();
    if (points.length === 0) return `${this.label()}: no data`;
    const values = points.map((p) => p.v);
    const f = this.format();
    return `${this.label()}: ${points.length} points, from ${f(Math.min(...values))} to ${f(Math.max(...values))}, latest ${f(points[points.length - 1]!.v)}`;
  });

  protected onPointer(event: PointerEvent): void {
    const svg = event.currentTarget as SVGSVGElement;
    const x = event.clientX - svg.getBoundingClientRect().left;
    this.active.set(
      nearestIndex(
        this.coords().map((c) => c.x),
        x,
      ),
    );
  }

  protected onKey(event: KeyboardEvent): void {
    const count = this.points().length;
    if (count === 0) return;
    const current = this.active();
    if (event.key === 'ArrowRight') {
      this.active.set(current === null ? 0 : Math.min(current + 1, count - 1));
    } else if (event.key === 'ArrowLeft') {
      this.active.set(current === null ? count - 1 : Math.max(current - 1, 0));
    } else if (event.key === 'Home') {
      this.active.set(0);
    } else if (event.key === 'End') {
      this.active.set(count - 1);
    } else if (event.key === 'Escape') {
      this.active.set(null);
      return;
    } else {
      return;
    }
    event.preventDefault();
  }
}
