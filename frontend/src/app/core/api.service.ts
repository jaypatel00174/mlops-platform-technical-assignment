import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  Accepted,
  AuditEvent,
  Binding,
  Comparison,
  Deployment,
  DeploymentCreate,
  DeploymentStatus,
  Model,
  ModelCreate,
  ModelPage,
  ModelStatus,
  Resolution,
  Series,
  Timeline,
  Version,
  VersionCreate,
} from '../api/types';

type Query = Record<string, string | number | undefined | null>;

function params(query: Query): HttpParams {
  let result = new HttpParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') {
      result = result.set(key, String(value));
    }
  }
  return result;
}

const enc = encodeURIComponent;
const API = '/api/v1';

/** The typed client: one method per endpoint, nothing else. Screens never build URLs. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  // -- registry -------------------------------------------------------------------------

  models(query: { q?: string; framework?: string; limit?: number; cursor?: string } = {}) {
    return this.http.get<ModelPage>(`${API}/models`, { params: params(query) });
  }

  model(id: string): Observable<Model> {
    return this.http.get<Model>(`${API}/models/${enc(id)}`);
  }

  createModel(body: ModelCreate): Observable<Model> {
    return this.http.post<Model>(`${API}/models`, body);
  }

  versions(modelId: string): Observable<{ items: Version[] }> {
    return this.http.get<{ items: Version[] }>(`${API}/models/${enc(modelId)}/versions`);
  }

  createVersion(modelId: string, body: VersionCreate): Observable<Version> {
    return this.http.post<Version>(`${API}/models/${enc(modelId)}/versions`, body);
  }

  versionAction(
    modelId: string,
    version: string,
    action: 'validate' | 'approve' | 'reject' | 'archive',
    comment?: string,
  ): Observable<Version> {
    const url = `${API}/models/${enc(modelId)}/versions/${enc(version)}/${action}`;
    const body = action === 'approve' || action === 'reject' ? { comment: comment ?? null } : null;
    return this.http.post<Version>(url, body);
  }

  audit(entityType: string, entityId: string): Observable<{ items: AuditEvent[] }> {
    return this.http.get<{ items: AuditEvent[] }>(`${API}/audit-events`, {
      params: params({ entity_type: entityType, entity_id: entityId }),
    });
  }

  // -- deployments ----------------------------------------------------------------------

  deployments(
    filter: {
      model_id?: string;
      environment?: string;
      status?: DeploymentStatus;
      limit?: number;
    } = {},
  ): Observable<{ items: Deployment[] }> {
    return this.http.get<{ items: Deployment[] }>(`${API}/deployments`, { params: params(filter) });
  }

  deployment(id: string): Observable<Deployment> {
    return this.http.get<Deployment>(`${API}/deployments/${enc(id)}`);
  }

  timeline(id: string): Observable<Timeline> {
    return this.http.get<Timeline>(`${API}/deployments/${enc(id)}/events`);
  }

  /** The key makes the request safe to repeat: the same key returns the same deployment. */
  requestDeployment(body: DeploymentCreate, idempotencyKey: string): Observable<Accepted> {
    return this.http.post<Accepted>(`${API}/deployments`, body, {
      headers: { 'Idempotency-Key': idempotencyKey },
    });
  }

  retry(id: string): Observable<Deployment> {
    return this.http.post<Deployment>(`${API}/deployments/${enc(id)}/retry`, null);
  }

  rollback(id: string): Observable<Deployment> {
    return this.http.post<Deployment>(`${API}/deployments/${enc(id)}/rollback`, null);
  }

  bindings(environment: string): Observable<{ environment: string; items: Binding[] }> {
    return this.http.get<{ environment: string; items: Binding[] }>(
      `${API}/environments/${enc(environment)}/bindings`,
    );
  }

  // -- monitoring -----------------------------------------------------------------------

  status(modelId: string): Observable<ModelStatus> {
    return this.http.get<ModelStatus>(`${API}/models/${enc(modelId)}/status`);
  }

  series(
    modelId: string,
    query: {
      version: string;
      environment: string;
      resolution: Resolution;
      from?: string;
      to?: string;
    },
  ): Observable<Series> {
    return this.http.get<Series>(`${API}/models/${enc(modelId)}/metrics`, {
      params: params(query),
    });
  }

  compare(
    modelId: string,
    query: { a: string; b: string; environment: string },
  ): Observable<Comparison> {
    return this.http.get<Comparison>(`${API}/models/${enc(modelId)}/versions/compare`, {
      params: params(query),
    });
  }
}

/** A fresh key for one user intent, such as one press of the Deploy button. */
export function newIdempotencyKey(): string {
  return `ui-${crypto.randomUUID()}`;
}
