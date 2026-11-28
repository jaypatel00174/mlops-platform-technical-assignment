import { Subject, throwError } from 'rxjs';

import { slugify } from '../features/models/register-model-dialog';
import { compareSemver } from '../features/model-detail/versions-tab';
import { ApiError } from './api-error';
import { Loader } from './loader';

describe('Loader', () => {
  it('moves from loading to loaded', () => {
    const source = new Subject<string>();
    const loader = new Loader(() => source);

    loader.load();
    expect(loader.loading()).toBe(true);

    source.next('hello');
    expect(loader.data()).toBe('hello');
    expect(loader.loading()).toBe(false);
    expect(loader.error()).toBeNull();
  });

  it('records a failure as an ApiError', () => {
    const loader = new Loader<string>(() => throwError(() => new Error('boom')));

    loader.load();

    expect(loader.error()).toBeInstanceOf(ApiError);
    expect(loader.error()?.detail).toBe('boom');
    expect(loader.loading()).toBe(false);
  });

  it('keeps good data on screen when a quiet refresh fails', () => {
    let fail = false;
    const first = new Subject<string>();
    const loader = new Loader<string>(() => (fail ? throwError(() => new Error('later')) : first));

    loader.load();
    first.next('kept');
    fail = true;
    loader.refresh();

    expect(loader.data()).toBe('kept');
    expect(loader.error()).toBeNull();
    expect(loader.refreshFailed()).toBe(true);
  });

  it('ignores an older response once a newer request started', () => {
    const slow = new Subject<string>();
    const fast = new Subject<string>();
    const sources = [slow, fast];
    const loader = new Loader<string>(() => sources.shift()!);

    loader.load();
    loader.load();
    fast.next('new');
    slow.next('old');

    expect(loader.data()).toBe('new');
  });
});

describe('compareSemver', () => {
  it('compares numbers, not text', () => {
    expect(compareSemver('1.10.0', '1.2.0')).toBeGreaterThan(0);
    expect(compareSemver('2.0.0', '10.0.0')).toBeLessThan(0);
    expect(compareSemver('1.0.0', '1.0.0')).toBe(0);
  });

  it('sorts a list newest first', () => {
    expect(['1.2.0', '1.10.0', '2.0.0', '1.9.9'].sort((a, b) => compareSemver(b, a))).toEqual([
      '2.0.0',
      '1.10.0',
      '1.9.9',
      '1.2.0',
    ]);
  });
});

describe('slugify', () => {
  it('suggests an id from a name', () => {
    expect(slugify('Pump Failure Predictor')).toBe('pump-failure-predictor');
    expect(slugify('  ML / Model #3!  ')).toBe('ml-model-3');
    expect(slugify('x'.repeat(100)).length).toBe(63);
  });
});
