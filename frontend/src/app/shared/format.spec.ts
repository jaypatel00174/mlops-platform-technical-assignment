import { formatDateTime, formatMetric, initials, relativeTime } from './format';

const NOW = Date.UTC(2026, 0, 15, 12, 0, 0);
const ago = (ms: number) => new Date(NOW - ms).toISOString();

describe('relativeTime', () => {
  it('reads naturally across scales', () => {
    expect(relativeTime(ago(10_000), NOW)).toBe('just now');
    expect(relativeTime(ago(5 * 60_000), NOW)).toBe('5 minutes ago');
    expect(relativeTime(ago(60 * 60_000), NOW)).toBe('1 hour ago');
    expect(relativeTime(ago(3 * 86_400_000), NOW)).toBe('3 days ago');
    expect(relativeTime(ago(90 * 86_400_000), NOW)).toBe('3 months ago');
  });

  it('handles the future', () => {
    expect(relativeTime(new Date(NOW + 5 * 60_000), NOW)).toBe('in 5 minutes');
  });

  it('shows a dash for missing or invalid values', () => {
    expect(relativeTime(null, NOW)).toBe('—');
    expect(relativeTime('not a date', NOW)).toBe('—');
  });
});

describe('formatMetric', () => {
  it('shows the error rate as a percentage', () => {
    expect(formatMetric('error_rate', 0.0125)).toBe('1.25 %');
  });

  it('keeps units and trims decimals', () => {
    expect(formatMetric('latency_ms', 89.9)).toBe('90 ms');
    expect(formatMetric('quality_score', 0.8912)).toBe('0.891');
  });

  it('shows a dash when there is no value', () => {
    expect(formatMetric('latency_ms', null)).toBe('—');
    expect(formatMetric('latency_ms', Number.NaN)).toBe('—');
  });
});

describe('initials', () => {
  it('uses the first letters of the first two words', () => {
    expect(initials('pump-failure-predictor')).toBe('PF');
    expect(initials('Smoke')).toBe('S');
    expect(initials('')).toBe('?');
  });
});

describe('formatDateTime', () => {
  it('formats a date and falls back to a dash', () => {
    expect(formatDateTime('2026-01-15T12:00:00Z')).toMatch(/2026/);
    expect(formatDateTime(undefined)).toBe('—');
  });
});
