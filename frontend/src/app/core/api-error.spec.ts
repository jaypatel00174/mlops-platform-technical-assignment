import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';

import { ApiError } from './api-error';

function respond(status: number, body: unknown, headers = new HttpHeaders()): HttpErrorResponse {
  return new HttpErrorResponse({ status, statusText: 'Status', error: body, headers });
}

describe('ApiError', () => {
  it('reads the platform error envelope', () => {
    const error = ApiError.from(
      respond(409, {
        code: 'DEPLOYMENT_CONFLICT',
        title: 'Deployment conflict',
        detail: 'another deployment is already in progress',
        correlation_id: 'abc123',
        details: { existing_deployment_id: 'dep-1' },
      }),
    );

    expect(error.status).toBe(409);
    expect(error.code).toBe('DEPLOYMENT_CONFLICT');
    expect(error.detail).toBe('another deployment is already in progress');
    expect(error.correlationId).toBe('abc123');
    expect(error.details['existing_deployment_id']).toBe('dep-1');
  });

  it('explains a network failure in words', () => {
    const error = ApiError.from(respond(0, null));

    expect(error.isNetwork).toBe(true);
    expect(error.code).toBe('NETWORK_ERROR');
    expect(error.detail).toMatch(/did not answer/);
  });

  it('copes with a gateway answer that is not our envelope', () => {
    const error = ApiError.from(
      respond(502, '<html>Bad gateway</html>', new HttpHeaders({ 'X-Correlation-ID': 'ref-9' })),
    );

    expect(error.code).toBe('HTTP_502');
    expect(error.detail).toMatch(/server had a problem/);
    expect(error.correlationId).toBe('ref-9');
  });

  it('turns any other thrown value into an ApiError', () => {
    expect(ApiError.from(new Error('boom')).detail).toBe('boom');
    expect(ApiError.from('weird').code).toBe('UNEXPECTED');
  });

  it('returns an ApiError unchanged', () => {
    const original = new ApiError(404, 'NOT_FOUND', 'Not found', 'nope', null);

    expect(ApiError.from(original)).toBe(original);
  });

  it('finds the field a validation problem points at', () => {
    const single = new ApiError(422, 'VALIDATION_ERROR', 'x', 'bad version', null, {
      field: 'version',
    });
    const many = new ApiError(422, 'VALIDATION_ERROR', 'x', 'invalid', null, {
      errors: [
        { field: 'artifact_digest', message: 'too short' },
        { field: 'algorithm', message: 'blank' },
      ],
    });

    expect(single.field).toBe('version');
    expect(single.fieldMessages).toEqual({ version: 'bad version' });
    expect(many.field).toBe('artifact_digest');
    expect(many.fieldMessages).toEqual({ artifact_digest: 'too short', algorithm: 'blank' });
  });

  it('flags a 401 so the app can sign out', () => {
    expect(
      ApiError.from(respond(401, { code: 'UNAUTHENTICATED', detail: 'expired' })).isUnauthenticated,
    ).toBe(true);
  });
});
