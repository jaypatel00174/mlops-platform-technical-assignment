import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

export interface DonutSegment {
  key: string;
  label: string;
  value: number;
  /** A CSS colour or variable. */
  color: string;
}

const RADIUS = 52;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/** A ring chart with a legend. Segments and legend rows are clickable to filter by that status. */
@Component({
  selector: 'app-donut',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host {
      display: flex;
      align-items: center;
      gap: 24px;
      flex-wrap: wrap;
    }
    svg {
      width: 150px;
      height: 150px;
      flex: none;
      transform: rotate(-90deg);
    }
    circle {
      fill: none;
      stroke-width: 15;
      transition:
        stroke-dasharray 500ms ease,
        stroke-dashoffset 500ms ease,
        opacity 160ms ease;
    }
    .track {
      stroke: var(--muted-soft);
    }
    .seg {
      cursor: pointer;
    }
    .dim {
      opacity: 0.25;
    }
    .centre {
      position: absolute;
      inset: 0;
      display: grid;
      place-content: center;
      text-align: center;
      pointer-events: none;
    }
    .centre strong {
      font-size: 30px;
      letter-spacing: -0.03em;
      line-height: 1;
    }
    .centre span {
      color: var(--text-soft);
      font-size: 12px;
    }
    .ring {
      position: relative;
    }
    ul {
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 4px;
      flex: 1;
      min-width: 150px;
    }
    li button {
      all: unset;
      box-sizing: border-box;
      width: 100%;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 5px 8px;
      border-radius: 8px;
      cursor: pointer;
    }
    li button:hover,
    li button:focus-visible {
      background: var(--muted-soft);
    }
    .swatch {
      width: 10px;
      height: 10px;
      border-radius: 3px;
      flex: none;
    }
    .count {
      margin-left: auto;
      font-weight: 650;
      font-variant-numeric: tabular-nums;
    }
  `,
  template: `
    <div class="ring">
      <svg viewBox="0 0 150 150" role="img" [attr.aria-label]="ariaLabel()">
        <circle class="track" cx="75" cy="75" [attr.r]="radius" />
        @for (arc of arcs(); track arc.key) {
          <circle
            class="seg"
            [class.dim]="hovered() !== null && hovered() !== arc.key"
            cx="75"
            cy="75"
            [attr.r]="radius"
            [attr.stroke]="arc.color"
            [attr.stroke-dasharray]="arc.dash"
            [attr.stroke-dashoffset]="arc.offset"
            (mouseenter)="hovered.set(arc.key)"
            (mouseleave)="hovered.set(null)"
            (click)="pick.emit(arc.key)"
          />
        }
      </svg>
      <div class="centre">
        <strong class="num">{{ total() }}</strong>
        <span>{{ unit() }}</span>
      </div>
    </div>
    <ul>
      @for (segment of segments(); track segment.key) {
        <li>
          <button
            type="button"
            (mouseenter)="hovered.set(segment.key)"
            (mouseleave)="hovered.set(null)"
            (focus)="hovered.set(segment.key)"
            (blur)="hovered.set(null)"
            (click)="pick.emit(segment.key)"
          >
            <span class="swatch" [style.background]="segment.color"></span>
            {{ segment.label }}
            <span class="count num">{{ segment.value }}</span>
          </button>
        </li>
      }
    </ul>
  `,
})
export class Donut {
  readonly segments = input.required<DonutSegment[]>();
  readonly unit = input('models');
  readonly pick = output<string>();

  protected readonly radius = RADIUS;
  protected readonly hovered = signal<string | null>(null);

  protected readonly total = computed(() => this.segments().reduce((sum, s) => sum + s.value, 0));

  protected readonly arcs = computed(() => {
    const total = this.total();
    if (total === 0) return [];
    let consumed = 0;
    return this.segments()
      .filter((s) => s.value > 0)
      .map((s) => {
        const length = (s.value / total) * CIRCUMFERENCE;
        // A small gap between arcs keeps neighbouring colours distinct.
        const gap = this.segments().filter((x) => x.value > 0).length > 1 ? 3 : 0;
        const arc = {
          key: s.key,
          color: s.color,
          dash: `${Math.max(length - gap, 0)} ${CIRCUMFERENCE}`,
          offset: -consumed,
        };
        consumed += length;
        return arc;
      });
  });

  protected readonly ariaLabel = computed(
    () =>
      `${this.total()} ${this.unit()}: ` +
      this.segments()
        .filter((s) => s.value > 0)
        .map((s) => `${s.value} ${s.label}`)
        .join(', '),
  );
}
