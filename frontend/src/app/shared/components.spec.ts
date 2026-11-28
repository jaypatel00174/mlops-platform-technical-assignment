import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ApiError } from '../core/api-error';
import { LineChart } from './line-chart';
import { ErrorPanel } from './states';
import { StatusChip } from './status-chip';

@Component({
  imports: [StatusChip],
  template: `<app-chip [kind]="kind" [value]="value" />`,
})
class ChipHost {
  kind: 'health' | 'deployment' | 'stage' | 'approval' = 'health';
  value = 'CRITICAL';
}

describe('StatusChip', () => {
  it('shows a readable label and tone', async () => {
    const fixture = TestBed.createComponent(ChipHost);
    await fixture.whenStable();

    const chip = fixture.nativeElement.querySelector('.chip') as HTMLElement;
    expect(chip.textContent).toContain('Critical');
    expect(chip.classList).toContain('crit');
  });

  it('marks work in progress as live', async () => {
    const fixture = TestBed.createComponent(ChipHost);
    fixture.componentInstance.kind = 'deployment';
    fixture.componentInstance.value = 'DEPLOYING';
    fixture.changeDetectorRef.markForCheck();
    await fixture.whenStable();

    expect((fixture.nativeElement.querySelector('.chip') as HTMLElement).classList).toContain(
      'live',
    );
  });

  it('falls back to a plain label for an unknown value', async () => {
    const fixture = TestBed.createComponent(ChipHost);
    fixture.componentInstance.value = 'BRAND_NEW';
    fixture.changeDetectorRef.markForCheck();
    await fixture.whenStable();

    expect((fixture.nativeElement.querySelector('.chip') as HTMLElement).textContent).toContain(
      'brand new',
    );
  });
});

describe('ErrorPanel', () => {
  function render(error: ApiError) {
    const fixture = TestBed.createComponent(ErrorPanel);
    fixture.componentRef.setInput('error', error);
    return fixture;
  }

  it('shows the message and the reference to quote', async () => {
    const fixture = render(new ApiError(500, 'INTERNAL', 'Server problem', 'it broke', 'ref-123'));
    await fixture.whenStable();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Server problem');
    expect(text).toContain('it broke');
    expect(text).toContain('ref-123');
  });

  it('offers a retry for server and network faults only', async () => {
    const server = render(new ApiError(503, 'X', 't', 'd', null));
    const missing = render(new ApiError(404, 'NOT_FOUND', 't', 'd', null));
    await server.whenStable();
    await missing.whenStable();

    expect(server.nativeElement.querySelector('button')).not.toBeNull();
    expect(missing.nativeElement.querySelector('button')).toBeNull();
  });
});

describe('LineChart', () => {
  // jsdom has no layout engine and no ResizeObserver; browsers do.
  beforeEach(() => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe(): void {}
        disconnect(): void {}
      },
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  const POINTS = [
    { t: Date.UTC(2026, 0, 1), v: 10 },
    { t: Date.UTC(2026, 0, 2), v: 20 },
    { t: Date.UTC(2026, 0, 3), v: 15 },
  ];

  function chart(points = POINTS) {
    const fixture = TestBed.createComponent(LineChart);
    fixture.componentRef.setInput('points', points);
    fixture.componentRef.setInput('format', (v: number) => `${v} ms`);
    fixture.componentRef.setInput('label', 'Latency');
    return fixture;
  }

  it('draws a line through the points and describes itself for screen readers', async () => {
    const fixture = chart();
    await fixture.whenStable();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('path.line')).not.toBeNull();
    expect(element.getAttribute('aria-label')).toContain('Latency: 3 points');
    expect(element.getAttribute('aria-label')).toContain('latest 15 ms');
  });

  it('says so when there is no data', async () => {
    const fixture = chart([]);
    await fixture.whenStable();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('No data in this range');
  });

  it('can be explored with the keyboard', async () => {
    const fixture = chart();
    await fixture.whenStable();
    const host = fixture.nativeElement as HTMLElement;

    host.dispatchEvent(new KeyboardEvent('keydown', { key: 'End' }));
    await fixture.whenStable();
    expect(host.querySelector('.tip')?.textContent).toContain('15 ms');

    host.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
    await fixture.whenStable();
    expect(host.querySelector('.tip')?.textContent).toContain('20 ms');

    host.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await fixture.whenStable();
    expect(host.querySelector('.tip')).toBeNull();
  });
});
