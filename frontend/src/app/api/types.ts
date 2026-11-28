/**
 * Friendly names for the generated API types (schema.d.ts, produced by `npm run api:types` from
 * docs/openapi.json). Components import from here and never reach into the generated file, so a
 * contract change surfaces as a compile error in one place.
 */
import type { components } from './schema';

type Schemas = components['schemas'];

export type Model = Schemas['ModelOut'];
export type ModelPage = Schemas['ModelListOut'];
export type ModelCreate = Schemas['ModelCreate'];
export type Version = Schemas['VersionOut'];
export type VersionCreate = Schemas['VersionCreate'];
export type Deployment = Schemas['DeploymentOut'];
export type DeploymentCreate = Schemas['DeploymentCreate'];
export type Accepted = Schemas['AcceptedOut'];
export type Timeline = Schemas['TimelineOut'];
export type Attempt = Schemas['AttemptOut'];
export type TimelineEntry = Schemas['TimelineEntryOut'];
export type Binding = Schemas['BindingOut'];
export type ModelStatus = Schemas['ModelStatusOut'];
export type VersionHealth = Schemas['VersionHealthOut'];
export type HealthReason = Schemas['ReasonOut'];
export type Series = Schemas['SeriesOut'];
export type SeriesPoint = Schemas['PointOut'];
export type Summary = Schemas['SummaryOut'];
export type Comparison = Schemas['ComparisonOut'];
export type VersionMeta = Schemas['VersionMetaOut'];
export type Delta = Schemas['DeltaOut'];
export type AuditEvent = Schemas['AuditEventOut'];
export type Problem = Schemas['Problem'];

export type Stage = Schemas['Stage'];
export type ApprovalStatus = Schemas['ApprovalStatus'];
export type DeploymentStatus = Schemas['DeploymentStatus'];

/** Health statuses the monitoring API can return (the schema types them as plain strings). */
export type HealthStatus = 'HEALTHY' | 'DEGRADED' | 'CRITICAL' | 'STALE' | 'NO_DATA';
export type Resolution = 'raw' | 'hour' | 'day';

/** The metrics every chart can show, in display order. */
export const METRICS = [
  { key: 'latency_ms', label: 'Latency', unit: 'ms', decimals: 0 },
  { key: 'error_rate', label: 'Error rate', unit: '%', decimals: 2, scale: 100 },
  { key: 'throughput_rpm', label: 'Throughput', unit: 'req/min', decimals: 0 },
  { key: 'quality_score', label: 'Quality', unit: '', decimals: 3 },
  { key: 'drift_score', label: 'Drift', unit: '', decimals: 3 },
  { key: 'availability', label: 'Availability', unit: '%', decimals: 2 },
] as const;

export type MetricKey = (typeof METRICS)[number]['key'];
