import { HttpErrorResponse } from '@angular/common/http';

/**
 * One error type for every failure the UI can meet, whatever its source.
 *
 * The backend always answers with the same envelope (code, title, detail, correlation_id), so
 * the UI can show a clear message and a reference an operator can search in the logs. A network
 * failure or an unreadable body is turned into the same shape, so no screen needs to special-case it.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly title: string,
    readonly detail: string,
    readonly correlationId: string | null,
    readonly details: Record<string, unknown> = {},
  ) {
    super(detail);
    this.name = 'ApiError';
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /** The field a validation problem points at, when the server named one. */
  get field(): string | null {
    const direct = this.details['field'];
    if (typeof direct === 'string') return direct;
    const errors = this.details['errors'];
    if (Array.isArray(errors) && errors.length > 0) {
      const first = errors[0] as { field?: unknown };
      if (typeof first.field === 'string') return first.field;
    }
    return null;
  }

  /** Problems for each invalid field, for showing next to the form controls. */
  get fieldMessages(): Record<string, string> {
    const messages: Record<string, string> = {};
    const errors = this.details['errors'];
    if (Array.isArray(errors)) {
      for (const item of errors as { field?: string; message?: string }[]) {
        if (item.field && item.message) messages[item.field] = item.message;
      }
    }
    const field = this.details['field'];
    if (typeof field === 'string' && !(field in messages)) messages[field] = this.detail;
    return messages;
  }

  static from(error: unknown): ApiError {
    if (error instanceof ApiError) return error;
    if (error instanceof HttpErrorResponse) return ApiError.fromHttp(error);
    const message = error instanceof Error ? error.message : 'Something unexpected happened.';
    return new ApiError(-1, 'UNEXPECTED', 'Unexpected error', message, null);
  }

  private static fromHttp(response: HttpErrorResponse): ApiError {
    if (response.status === 0) {
      return new ApiError(
        0,
        'NETWORK_ERROR',
        'Cannot reach the server',
        'The platform did not answer. Check your connection and that the API is running, then try again.',
        null,
      );
    }
    const body = response.error as Partial<Record<string, unknown>> | null;
    if (body && typeof body === 'object' && typeof body['code'] === 'string') {
      return new ApiError(
        response.status,
        body['code'] as string,
        typeof body['title'] === 'string' ? body['title'] : response.statusText,
        typeof body['detail'] === 'string' ? body['detail'] : 'The request failed.',
        typeof body['correlation_id'] === 'string' ? body['correlation_id'] : null,
        (body['details'] as Record<string, unknown> | undefined) ?? {},
      );
    }
    // A proxy or gateway answered with something that is not our envelope.
    const header = response.headers?.get('X-Correlation-ID') ?? null;
    return new ApiError(
      response.status,
      `HTTP_${response.status}`,
      response.statusText || 'Request failed',
      response.status >= 500
        ? 'The server had a problem handling the request. Try again in a moment.'
        : 'The request could not be completed.',
      header,
    );
  }
}
