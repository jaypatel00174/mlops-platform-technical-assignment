import { DOCUMENT } from '@angular/common';
import { Injectable, effect, inject, signal } from '@angular/core';

export type ThemeChoice = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'mlops.theme';
const ORDER: ThemeChoice[] = ['system', 'light', 'dark'];

/** Light, dark or follow the system. The choice is remembered and applied as a class on <html>. */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly document = inject(DOCUMENT);
  readonly choice = signal<ThemeChoice>(this.read());

  constructor() {
    effect(() => {
      const root = this.document.documentElement;
      root.classList.remove('theme-light', 'theme-dark');
      if (this.choice() !== 'system') root.classList.add(`theme-${this.choice()}`);
      localStorage.setItem(STORAGE_KEY, this.choice());
    });
  }

  cycle(): void {
    const next = ORDER[(ORDER.indexOf(this.choice()) + 1) % ORDER.length];
    this.choice.set(next ?? 'system');
  }

  private read(): ThemeChoice {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === 'light' || stored === 'dark' ? stored : 'system';
  }
}
