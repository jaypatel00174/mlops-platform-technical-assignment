import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { AuthService, PERSONAS, readToken } from './auth.service';

/** A token with the given claims. The signature is fake: the UI never verifies it. */
function tokenFor(claims: Record<string, unknown>): string {
  const encode = (value: unknown) => btoa(JSON.stringify(value)).replace(/=/g, '');
  return `${encode({ alg: 'HS256' })}.${encode(claims)}.signature`;
}

const FUTURE = Math.floor(Date.now() / 1000) + 3600;

describe('readToken', () => {
  it('reads who the token is for, their roles and expiry', () => {
    const claims = readToken(tokenFor({ sub: 'alice', roles: ['ml-engineer'], exp: FUTURE }));

    expect(claims?.subject).toBe('alice');
    expect(claims?.roles).toEqual(['ml-engineer']);
    expect(claims?.expiresAt?.getTime()).toBe(FUTURE * 1000);
  });

  it('ignores roles it does not know', () => {
    expect(readToken(tokenFor({ sub: 'x', roles: ['admin', 'wizard'] }))?.roles).toEqual(['admin']);
  });

  it('rejects text that is not a token', () => {
    expect(readToken('nonsense')).toBeNull();
    expect(readToken(tokenFor({ roles: [] }))).toBeNull();
    expect(readToken('a.%%%.c')).toBeNull();
  });
});

describe('AuthService', () => {
  let auth: AuthService;
  let http: HttpTestingController;

  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    auth = TestBed.inject(AuthService);
    http = TestBed.inject(HttpTestingController);
  });

  it('starts signed out', () => {
    expect(auth.signedIn()).toBe(false);
    expect(auth.can('view')).toBe(false);
  });

  it('signs in as a persona through the dev token endpoint', () => {
    const persona = PERSONAS.find((p) => p.subject === 'bob')!;
    auth.signInAs(persona).subscribe();

    const request = http.expectOne('/api/v1/dev/token');
    expect(request.request.body).toEqual({ subject: 'bob', roles: ['approver'] });
    request.flush({
      access_token: tokenFor({ sub: 'bob', roles: ['approver'], exp: FUTURE }),
      subject: 'bob',
      roles: ['approver'],
      expires_at: new Date(FUTURE * 1000).toISOString(),
    });

    expect(auth.signedIn()).toBe(true);
    expect(auth.subject()).toBe('bob');
    expect(auth.can('decide')).toBe(true);
    expect(auth.can('deployProduction')).toBe(false);
  });

  it('keeps the session for the tab and forgets it on sign out', () => {
    expect(
      auth.signInWithToken(tokenFor({ sub: 'carol', roles: ['release-manager'], exp: FUTURE })),
    ).toBe(true);
    expect(sessionStorage.getItem('mlops.session')).not.toBeNull();

    auth.signOut();

    expect(auth.signedIn()).toBe(false);
    expect(sessionStorage.getItem('mlops.session')).toBeNull();
  });

  it('refuses a token it cannot read', () => {
    expect(auth.signInWithToken('garbage')).toBe(false);
    expect(auth.signedIn()).toBe(false);
  });

  it('knows an expired token', () => {
    auth.signInWithToken(
      tokenFor({ sub: 'x', roles: ['viewer'], exp: Math.floor(Date.now() / 1000) - 10 }),
    );

    expect(auth.expired()).toBe(true);
  });

  it('maps roles to what the UI offers', () => {
    auth.signInWithToken(tokenFor({ sub: 'vera', roles: ['viewer'], exp: FUTURE }));
    expect(auth.can('view')).toBe(true);
    expect(auth.can('registerModel')).toBe(false);

    auth.signInWithToken(tokenFor({ sub: 'root', roles: ['admin'], exp: FUTURE }));
    expect(auth.can('deployProduction')).toBe(true);
    expect(auth.can('readAudit')).toBe(true);
  });
});
