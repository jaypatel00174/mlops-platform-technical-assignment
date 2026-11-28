/** Pure helpers for the charts, kept free of Angular so they are easy to test. */

export interface ChartPoint {
  /** Milliseconds since the epoch. */
  t: number;
  v: number;
  /** How many raw samples this point stands for, shown in the tooltip. */
  n?: number;
}

export interface Domain {
  min: number;
  max: number;
}

/** A "nice" step (1, 2, 2.5, 5 times a power of ten) that gives about `target` intervals. */
export function niceStep(range: number, target: number): number {
  if (range <= 0) return 1;
  const rough = range / Math.max(target, 1);
  const power = Math.pow(10, Math.floor(Math.log10(rough)));
  const fraction = rough / power;
  const factor =
    fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return factor * power;
}

/** Round the domain outwards to tick boundaries and return the ticks inside it. */
export function niceScale(
  min: number,
  max: number,
  target = 4,
): { domain: Domain; ticks: number[] } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return { domain: { min: 0, max: 1 }, ticks: [0, 1] };
  }
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const step = niceStep(max - min, target);
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let value = lo; value <= hi + step / 1e6; value += step) {
    ticks.push(Number(value.toPrecision(12)));
  }
  return { domain: { min: lo, max: hi }, ticks };
}

/** The domain of the data, widened to include limits that sit near it (so they are visible). */
export function domainWithLimits(values: number[], limits: number[]): { min: number; max: number } {
  if (values.length === 0) return { min: 0, max: 1 };
  let min = Math.min(...values);
  let max = Math.max(...values);
  const span = max - min || Math.abs(max) || 1;
  for (const limit of limits) {
    if (limit >= min - span && limit <= max + span) {
      min = Math.min(min, limit);
      max = Math.max(max, limit);
    }
  }
  return { min, max };
}

/** Index of the point whose x is closest to `x`. Points are sorted by time. */
export function nearestIndex(xs: number[], x: number): number {
  if (xs.length === 0) return -1;
  let low = 0;
  let high = xs.length - 1;
  while (low < high) {
    const mid = (low + high) >> 1;
    if ((xs[mid] ?? 0) < x) low = mid + 1;
    else high = mid;
  }
  const after = low;
  const before = Math.max(low - 1, 0);
  return Math.abs((xs[before] ?? 0) - x) <= Math.abs((xs[after] ?? 0) - x) ? before : after;
}

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** Pick a tick label format that suits how long the time axis is. */
export function timeFormatter(spanMs: number): (t: number) => string {
  const clock = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });
  const day = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
  const dayAndClock = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
  if (spanMs <= 2 * DAY) return (t) => (spanMs <= DAY ? clock.format(t) : dayAndClock.format(t));
  return (t) => day.format(t);
}
