import { BreakpointObserver } from '@angular/cdk/layout';
import { HttpClient } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { MatTooltipModule } from '@angular/material/tooltip';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { catchError, filter, map, of, switchMap, timer } from 'rxjs';

import { AuthService } from './core/auth.service';
import { ThemeService } from './core/theme.service';
import { initials } from './shared/format';
import { Icon, IconName } from './shared/icon';

interface NavItem {
  path: string;
  label: string;
  icon: IconName;
  exact: boolean;
}

const NAV: NavItem[] = [
  { path: '/', label: 'Overview', icon: 'dashboard', exact: true },
  { path: '/models', label: 'Models', icon: 'cube', exact: false },
  { path: '/deployments', label: 'Deployments', icon: 'rocket', exact: false },
];

@Component({
  selector: 'app-root',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    MatButtonModule,
    MatMenuModule,
    MatTooltipModule,
    Icon,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  protected readonly auth = inject(AuthService);
  protected readonly theme = inject(ThemeService);
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  protected readonly nav = NAV;
  protected readonly menuOpen = signal(false);

  protected readonly narrow = toSignal(
    inject(BreakpointObserver)
      .observe('(max-width: 900px)')
      .pipe(map((state) => state.matches)),
    { initialValue: false },
  );

  /** Is the API answering? Checked every 20 seconds while signed in. */
  protected readonly apiOnline = toSignal(
    timer(0, 20_000).pipe(
      switchMap(() =>
        this.http.get('/health/live').pipe(
          map(() => true),
          catchError(() => of(false)),
        ),
      ),
    ),
    { initialValue: true },
  );

  protected readonly themeIcon = computed<IconName>(() => {
    const choice = this.theme.choice();
    return choice === 'light' ? 'sun' : choice === 'dark' ? 'moon' : 'monitor';
  });

  protected readonly userInitials = computed(() => initials(this.auth.subject()));

  constructor() {
    // Close the mobile menu after every navigation.
    this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => this.menuOpen.set(false));
  }

  protected roleLabel(): string {
    return this.auth.roles().join(', ') || 'no roles';
  }
}
