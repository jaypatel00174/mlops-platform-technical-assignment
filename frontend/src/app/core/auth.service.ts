import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, tap } from 'rxjs';

export type Role = 'viewer' | 'ml-engineer' | 'approver' | 'release-manager' | 'admin' | 'service';

/** What a signed-in person can do. The server decides; this only hides buttons that would fail. */
export type Capability =
  | 'view'
  | 'registerModel'
  | 'registerVersion'
  | 'validate'
  | 'decide'
  | 'deployNonProduction'
  | 'deployProduction'
  | 'archive'
  | 'readAudit';

const CAPABILITIES: Record<Role, readonly Capability[]> = {
  viewer: ['view'],
  'ml-engineer': ['view', 'registerModel', 'registerVersion', 'validate', 'deployNonProduction'],
  approver: ['view', 'decide', 'readAudit'],
  'release-manager': ['view', 'deployNonProduction', 'deployProduction', 'archive'],
  admin: [
    'view',
    'registerModel',
    'registerVersion',
    'validate',
    'decide',
    'deployNonProduction',
    'deployProduction',
    'archive',
    'readAudit',
  ],
  service: [],
};

const KNOWN_ROLES = new Set<string>(Object.keys(CAPABILITIES));

export interface Session {
  token: string;
  subject: string;
  roles: Role[];
  expiresAt: Date | null;
}

export interface Persona {
  subject: string;
  title: string;
  description: string;
  roles: Role[];
  accent: string;
}

/** The people the development sign-in offers. Each maps to one role of the platform. */
export const PERSONAS: readonly Persona[] = [
  {
    subject: 'alice',
    title: 'Alice',
    description: 'ML engineer. Registers models and versions, deploys to dev and staging.',
    roles: ['ml-engineer'],
    accent: 'var(--accent-blue)',
  },
  {
    subject: 'bob',
    title: 'Bob',
    description: 'Approver. Reviews versions, approves or rejects, reads the audit trail.',
    roles: ['approver'],
    accent: 'var(--accent-violet)',
  },
  {
    subject: 'carol',
    title: 'Carol',
    description: 'Release manager. Deploys to production, rolls back, archives.',
    roles: ['release-manager'],
    accent: 'var(--accent-amber)',
  },
  {
    subject: 'vera',
    title: 'Vera',
    description: 'Viewer. Read-only access to everything the platform shows.',
    roles: ['viewer'],
    accent: 'var(--accent-teal)',
  },
  {
    subject: 'root',
    title: 'Admin',
    description: 'Every permission. Useful to see all actions at once.',
    roles: ['admin'],
    accent: 'var(--accent-rose)',
  },
];

interface DevTokenResponse {
  access_token: string;
  subject: string;
  roles: Role[];
  expires_at: string;
}

const STORAGE_KEY = 'mlops.session';

/** Reads the display fields of a JWT. It does not verify it: the server does that on every call. */
export function readToken(token: string): Omit<Session, 'token'> | null {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    const claims = JSON.parse(json) as { sub?: unknown; roles?: unknown; exp?: unknown };
    if (typeof claims.sub !== 'string') return null;
    const roles = Array.isArray(claims.roles)
      ? (claims.roles.filter((r) => typeof r === 'string' && KNOWN_ROLES.has(r)) as Role[])
      : [];
    const exp = typeof claims.exp === 'number' ? new Date(claims.exp * 1000) : null;
    return { subject: claims.sub, roles, expiresAt: exp };
  } catch {
    return null;
  }
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  private readonly state = signal<Session | null>(this.restore());

  readonly session = this.state.asReadonly();
  readonly signedIn = computed(() => this.state() !== null);
  readonly subject = computed(() => this.state()?.subject ?? '');
  readonly roles = computed(() => this.state()?.roles ?? []);
  readonly token = computed(() => this.state()?.token ?? null);

  /** True when the token is past its expiry (the server will refuse it anyway). */
  readonly expired = computed(() => {
    const expiresAt = this.state()?.expiresAt;
    return expiresAt ? expiresAt.getTime() <= Date.now() : false;
  });

  can(capability: Capability): boolean {
    return this.roles().some((role) => CAPABILITIES[role]?.includes(capability));
  }

  /** Development sign-in: the API trades a persona for a token (local and test only). */
  signInAs(persona: Persona): Observable<DevTokenResponse> {
    return this.http
      .post<DevTokenResponse>('/api/v1/dev/token', {
        subject: persona.subject,
        roles: persona.roles,
      })
      .pipe(tap((response) => this.accept(response.access_token)));
  }

  /** Sign in with a token from any source, for example one issued by an identity provider. */
  signInWithToken(token: string): boolean {
    return this.accept(token.trim());
  }

  signOut(returnUrl?: string): void {
    this.state.set(null);
    sessionStorage.removeItem(STORAGE_KEY);
    void this.router.navigate(['/sign-in'], returnUrl ? { queryParams: { returnUrl } } : {});
  }

  private accept(token: string): boolean {
    const claims = readToken(token);
    if (!claims) return false;
    const session: Session = { token, ...claims };
    this.state.set(session);
    sessionStorage.setItem(STORAGE_KEY, token);
    return true;
  }

  private restore(): Session | null {
    const token = sessionStorage.getItem(STORAGE_KEY);
    const claims = token ? readToken(token) : null;
    return token && claims ? { token, ...claims } : null;
  }
}
