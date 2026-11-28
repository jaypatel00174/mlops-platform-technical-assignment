import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { ApiError } from './api-error';
import { AuthService } from './auth.service';

const API_PREFIX = '/api/';

/** Adds the bearer token to API calls (and only to API calls). */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const token = inject(AuthService).token();
  if (token && request.url.startsWith(API_PREFIX) && !request.headers.has('Authorization')) {
    request = request.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
  }
  return next(request);
};

/**
 * Turns every HTTP failure into an ApiError, so screens handle one error type.
 * A 401 on a signed-in session means the token expired or was revoked: sign out and come back.
 */
export const errorInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return next(request).pipe(
    catchError((error: unknown) => {
      if (!(error instanceof HttpErrorResponse)) return throwError(() => ApiError.from(error));
      const apiError = ApiError.from(error);
      const isSignInCall = request.url.endsWith('/dev/token');
      if (apiError.isUnauthenticated && auth.signedIn() && !isSignInCall) {
        auth.signOut(router.url);
      }
      return throwError(() => apiError);
    }),
  );
};
