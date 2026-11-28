import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { ApiError } from './api-error';
import { AuthService } from './auth.service';
import { authInterceptor, errorInterceptor } from './http.interceptors';

const encode = (value: unknown) => btoa(JSON.stringify(value)).replace(/=/g, '');
const TOKEN = `${encode({ alg: 'HS256' })}.${encode({
  sub: 'alice',
  roles: ['ml-engineer'],
  exp: Math.floor(Date.now() / 1000) + 3600,
})}.sig`;

describe('HTTP interceptors', () => {
  let http: HttpClient;
  let backend: HttpTestingController;
  let auth: AuthService;

  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        provideHttpClient(withInterceptors([authInterceptor, errorInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpClient);
    backend = TestBed.inject(HttpTestingController);
    auth = TestBed.inject(AuthService);
  });

  afterEach(() => backend.verify());

  it('adds the bearer token to API calls', () => {
    auth.signInWithToken(TOKEN);

    http.get('/api/v1/models').subscribe();

    expect(backend.expectOne('/api/v1/models').request.headers.get('Authorization')).toBe(
      `Bearer ${TOKEN}`,
    );
  });

  it('does not leak the token to other addresses', () => {
    auth.signInWithToken(TOKEN);

    http.get('https://example.com/data').subscribe();

    expect(backend.expectOne('https://example.com/data').request.headers.has('Authorization')).toBe(
      false,
    );
  });

  it('sends nothing when signed out', () => {
    http.get('/api/v1/models').subscribe();

    expect(backend.expectOne('/api/v1/models').request.headers.has('Authorization')).toBe(false);
  });

  it('turns an error response into an ApiError', () => {
    let caught: unknown;
    http.get('/api/v1/models/x').subscribe({ error: (error: unknown) => (caught = error) });

    backend
      .expectOne('/api/v1/models/x')
      .flush(
        {
          code: 'NOT_FOUND',
          title: 'Not found',
          detail: 'model does not exist',
          correlation_id: 'c1',
          details: {},
        },
        { status: 404, statusText: 'Not Found' },
      );

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).code).toBe('NOT_FOUND');
    expect((caught as ApiError).correlationId).toBe('c1');
  });

  it('signs out and returns to sign-in when the token is refused', () => {
    auth.signInWithToken(TOKEN);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);

    http.get('/api/v1/models').subscribe({ error: () => undefined });
    backend
      .expectOne('/api/v1/models')
      .flush(
        { code: 'UNAUTHENTICATED', title: 'Unauthenticated', detail: 'the token has expired' },
        { status: 401, statusText: 'Unauthorized' },
      );

    expect(auth.signedIn()).toBe(false);
    expect(navigate).toHaveBeenCalledWith(['/sign-in'], expect.anything());
  });

  it('does not sign out when the dev sign-in itself fails', () => {
    auth.signInWithToken(TOKEN);

    http.post('/api/v1/dev/token', {}).subscribe({ error: () => undefined });
    backend
      .expectOne('/api/v1/dev/token')
      .flush(
        { code: 'UNAUTHENTICATED', detail: 'no' },
        { status: 401, statusText: 'Unauthorized' },
      );

    expect(auth.signedIn()).toBe(true);
  });
});
