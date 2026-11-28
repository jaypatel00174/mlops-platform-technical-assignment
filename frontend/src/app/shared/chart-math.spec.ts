import { domainWithLimits, nearestIndex, niceScale, niceStep, timeFormatter } from './chart-math';

describe('niceStep', () => {
  it('rounds to 1, 2, 2.5, 5 or 10 times a power of ten', () => {
    expect(niceStep(100, 4)).toBe(25);
    expect(niceStep(10, 5)).toBe(2);
    expect(niceStep(1, 4)).toBe(0.25);
    expect(niceStep(7, 7)).toBe(1);
  });

  it('has a safe answer for an empty range', () => {
    expect(niceStep(0, 4)).toBe(1);
  });
});

describe('niceScale', () => {
  it('widens the domain to tick boundaries', () => {
    const { domain, ticks } = niceScale(79, 104, 4);

    expect(domain.min).toBeLessThanOrEqual(79);
    expect(domain.max).toBeGreaterThanOrEqual(104);
    expect(ticks[0]).toBe(domain.min);
    expect(ticks[ticks.length - 1]).toBe(domain.max);
  });

  it('gives a flat series some height instead of dividing by zero', () => {
    const { domain } = niceScale(5, 5);

    expect(domain.max).toBeGreaterThan(domain.min);
  });

  it('survives non-finite input', () => {
    expect(niceScale(NaN, 3).domain).toEqual({ min: 0, max: 1 });
  });
});

describe('domainWithLimits', () => {
  it('includes limits that sit near the data', () => {
    expect(domainWithLimits([1, 3], [5])).toEqual({ min: 1, max: 5 });
  });

  it('ignores limits far away, so they do not flatten the data', () => {
    expect(domainWithLimits([1, 3], [500])).toEqual({ min: 1, max: 3 });
  });

  it('has a default for no data', () => {
    expect(domainWithLimits([], [1])).toEqual({ min: 0, max: 1 });
  });
});

describe('nearestIndex', () => {
  const xs = [0, 10, 20, 30];

  it('finds the closest point', () => {
    expect(nearestIndex(xs, 4)).toBe(0);
    expect(nearestIndex(xs, 6)).toBe(1);
    expect(nearestIndex(xs, 100)).toBe(3);
    expect(nearestIndex(xs, -50)).toBe(0);
  });

  it('returns -1 with no points', () => {
    expect(nearestIndex([], 5)).toBe(-1);
  });
});

describe('timeFormatter', () => {
  it('shows clock time for a day, dates for longer', () => {
    const t = Date.UTC(2026, 6, 3, 14, 30);

    expect(timeFormatter(3_600_000)(t)).toMatch(/\d{1,2}:\d{2}/);
    expect(timeFormatter(30 * 86_400_000)(t)).toMatch(/Jul|3/);
  });
});
