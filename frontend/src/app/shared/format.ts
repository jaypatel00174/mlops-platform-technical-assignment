import { Pipe, PipeTransform } from '@angular/core';

import { METRICS, MetricKey } from '../api/types';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now", "5 minutes ago", "3 days ago". Future times read "in 5 minutes". */
export function relativeTime(value: string | Date | null | undefined, now = Date.now()): string {
  if (!value) return '—';
  const then = typeof value === 'string' ? Date.parse(value) : value.getTime();
  if (Number.isNaN(then)) return '—';
  const diff = now - then;
  const abs = Math.abs(diff);
  const words =
    abs < 45_000
      ? null
      : abs < HOUR
        ? plural(Math.round(abs / MINUTE), 'minute')
        : abs < DAY
          ? plural(Math.round(abs / HOUR), 'hour')
          : abs < 60 * DAY
            ? plural(Math.round(abs / DAY), 'day')
            : plural(Math.round(abs / (30 * DAY)), 'month');
  if (words === null) return 'just now';
  return diff >= 0 ? `${words} ago` : `in ${words}`;
}

function plural(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? '' : 's'}`;
}

@Pipe({ name: 'ago' })
export class AgoPipe implements PipeTransform {
  transform(value: string | Date | null | undefined): string {
    return relativeTime(value);
  }
}

const dateTime = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '—';
  const date = typeof value === 'string' ? new Date(value) : value;
  return Number.isNaN(date.getTime()) ? '—' : dateTime.format(date);
}

@Pipe({ name: 'dateTime' })
export class DateTimePipe implements PipeTransform {
  transform(value: string | Date | null | undefined): string {
    return formatDateTime(value);
  }
}

/** A metric value the way people read it: error rate as a percentage, trimmed decimals. */
export function formatMetric(key: MetricKey, value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const meta = METRICS.find((metric) => metric.key === key);
  if (!meta) return String(value);
  const scaled = value * ('scale' in meta ? meta.scale : 1);
  const text = scaled.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: meta.decimals,
  });
  return meta.unit ? `${text} ${meta.unit}` : text;
}

/** First letters of a name or id, for avatars: "pump-failure-predictor" becomes "PF". */
export function initials(text: string): string {
  const parts = text.split(/[\s_-]+/).filter(Boolean);
  return (parts[0]?.[0] ?? '?').toUpperCase() + (parts[1]?.[0] ?? '').toUpperCase();
}
