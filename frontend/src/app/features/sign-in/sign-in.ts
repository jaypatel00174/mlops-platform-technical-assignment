import { ChangeDetectionStrategy, Component, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { Router } from '@angular/router';

import { ApiError } from '../../core/api-error';
import { AuthService, PERSONAS, Persona } from '../../core/auth.service';
import { ThemeService } from '../../core/theme.service';
import { initials } from '../../shared/format';
import { Icon } from '../../shared/icon';
import { ErrorPanel } from '../../shared/states';

@Component({
  selector: 'app-sign-in',
  imports: [FormsModule, MatButtonModule, MatFormFieldModule, MatInputModule, Icon, ErrorPanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './sign-in.scss',
  template: `
    <div class="wrap">
      <section class="hero">
        <span class="mark"><app-icon name="sparkles" [size]="22" /></span>
        <h1>MLOps Platform</h1>
        <p>
          Register models, approve and promote versions, deploy safely and watch how they behave in
          production. One place, with an audit trail behind every change.
        </p>
        <ul class="points">
          <li><app-icon name="shield" [size]="18" /> Approvals with separation of duties</li>
          <li>
            <app-icon name="rocket" [size]="18" /> Asynchronous deployments you can retry and roll
            back
          </li>
          <li><app-icon name="pulse" [size]="18" /> Health, drift and quality in one view</li>
        </ul>
        <button class="theme" type="button" mat-button (click)="theme.cycle()">
          Theme: {{ theme.choice() }}
        </button>
      </section>

      <section class="panel card">
        <h2>Sign in</h2>
        <p class="help-text">
          Pick a person to explore the platform with their permissions. This development sign-in is
          available only on a local or test server.
        </p>

        @if (error(); as problem) {
          <app-error-panel [error]="problem" (retry)="error.set(null)" />
        }

        <div class="personas">
          @for (persona of personas; track persona.subject) {
            <button
              type="button"
              class="persona"
              [style.--tone]="persona.accent"
              [disabled]="busy() !== null"
              (click)="signInAs(persona)"
            >
              <span class="avatar">{{ initials(persona.title) }}</span>
              <span class="text">
                <strong>{{ persona.title }}</strong>
                <small>{{ persona.description }}</small>
              </span>
              @if (busy() === persona.subject) {
                <span class="spin" aria-label="Signing in"></span>
              } @else {
                <app-icon name="chevron" [size]="18" />
              }
            </button>
          }
        </div>

        <details class="token">
          <summary><app-icon name="key" [size]="16" /> Use a token instead</summary>
          <p class="help-text">
            Paste a bearer token issued by your identity provider (or by <code>make token</code>).
          </p>
          <mat-form-field appearance="outline" class="full">
            <mat-label>Bearer token</mat-label>
            <textarea
              matInput
              rows="3"
              [ngModel]="token()"
              (ngModelChange)="token.set($event)"
            ></textarea>
            @if (tokenInvalid()) {
              <mat-error>That does not look like a token.</mat-error>
            }
          </mat-form-field>
          <button mat-flat-button type="button" [disabled]="!token().trim()" (click)="useToken()">
            Continue with token
          </button>
        </details>
      </section>
    </div>
  `,
})
export class SignIn {
  /** Where to go after signing in (set by the guard). */
  readonly returnUrl = input<string>();

  protected readonly auth = inject(AuthService);
  protected readonly theme = inject(ThemeService);
  private readonly router = inject(Router);

  protected readonly personas = PERSONAS;
  protected readonly busy = signal<string | null>(null);
  protected readonly error = signal<ApiError | null>(null);
  protected readonly token = signal('');
  protected readonly tokenInvalid = signal(false);
  protected readonly initials = initials;

  protected signInAs(persona: Persona): void {
    this.busy.set(persona.subject);
    this.error.set(null);
    this.auth.signInAs(persona).subscribe({
      next: () => this.done(),
      error: (error: unknown) => {
        this.busy.set(null);
        this.error.set(this.explain(ApiError.from(error)));
      },
    });
  }

  protected useToken(): void {
    this.tokenInvalid.set(false);
    if (this.auth.signInWithToken(this.token())) this.done();
    else this.tokenInvalid.set(true);
  }

  private done(): void {
    this.busy.set(null);
    const target = this.returnUrl();
    void this.router.navigateByUrl(target && target.startsWith('/') ? target : '/');
  }

  /** A 404 here means the server is not a local one, so the persona sign-in does not exist. */
  private explain(error: ApiError): ApiError {
    if (error.status !== 404) return error;
    return new ApiError(
      404,
      'DEV_SIGN_IN_UNAVAILABLE',
      'Development sign-in is not available here',
      'This server does not offer the persona sign-in. Use a token from your identity provider instead.',
      error.correlationId,
    );
  }
}
