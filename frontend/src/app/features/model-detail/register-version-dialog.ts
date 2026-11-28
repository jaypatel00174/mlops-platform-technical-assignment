import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import {
  AbstractControl,
  FormBuilder,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { Version } from '../../api/types';
import { ApiError } from '../../core/api-error';
import { ApiService } from '../../core/api.service';
import { Icon } from '../../shared/icon';
import { ErrorPanel } from '../../shared/states';

const SEMVER = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;

/** The metadata box must hold a JSON object (or be empty). */
function jsonObject(control: AbstractControl<string>): ValidationErrors | null {
  const text = control.value.trim();
  if (!text) return null;
  try {
    const parsed: unknown = JSON.parse(text);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? null : { json: true };
  } catch {
    return { json: true };
  }
}

export interface RegisterVersionData {
  modelId: string;
  suggested: string;
}

@Component({
  selector: 'app-register-version-dialog',
  imports: [
    ReactiveFormsModule,
    MatDialogModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    ErrorPanel,
    Icon,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>Register a version of {{ data.modelId }}</h2>
    <form [formGroup]="form" (ngSubmit)="submit()">
      <mat-dialog-content>
        <p class="help-text">
          A version points at one trained artefact. Its digest is checked again before every
          deployment, so a changed file is caught.
          <button type="button" class="link" (click)="fillSample()">
            <app-icon name="sparkles" [size]="14" /> Fill with sample values
          </button>
        </p>

        @if (problem(); as error) {
          @if (!hasFieldErrors()) {
            <app-error-panel [error]="error" (retry)="submit()" />
          }
        }

        <div class="form-grid">
          <mat-form-field appearance="outline">
            <mat-label>Version</mat-label>
            <input matInput formControlName="version" placeholder="2.0.0" cdkFocusInitial />
            <mat-hint>Semantic version, such as 2.0.0</mat-hint>
            @if (error('version'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>

          <mat-form-field appearance="outline">
            <mat-label>Algorithm</mat-label>
            <input matInput formControlName="algorithm" placeholder="gradient-boosting" />
            @if (error('algorithm'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>

          <mat-form-field appearance="outline" class="full">
            <mat-label>Artifact URI</mat-label>
            <input matInput formControlName="artifact_uri" placeholder="s3://models/pump/2.0.0" />
            @if (error('artifact_uri'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>

          <mat-form-field appearance="outline" class="full">
            <mat-label>Artifact digest</mat-label>
            <input
              matInput
              formControlName="artifact_digest"
              placeholder="sha256:…64 hex characters"
            />
            @if (error('artifact_digest'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>

          <mat-form-field appearance="outline" class="full">
            <mat-label>Training data reference</mat-label>
            <input
              matInput
              formControlName="training_data_ref"
              placeholder="s3://datasets/pump/2026-06"
            />
            @if (error('training_data_ref'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>

          <mat-form-field appearance="outline">
            <mat-label>Runtime type</mat-label>
            <input matInput formControlName="runtime_type" />
          </mat-form-field>

          <mat-form-field appearance="outline">
            <mat-label>Tags</mat-label>
            <input matInput formControlName="tags" placeholder="candidate, plant-a" />
          </mat-form-field>

          <mat-form-field appearance="outline" class="full">
            <mat-label>Metadata (JSON, optional)</mat-label>
            <textarea
              matInput
              rows="3"
              formControlName="metadata"
              placeholder='{"accuracy": 0.93}'
            ></textarea>
            @if (error('metadata'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>
        </div>
      </mat-dialog-content>
      <mat-dialog-actions align="end">
        <button mat-button type="button" mat-dialog-close [disabled]="busy()">Cancel</button>
        <button mat-flat-button type="submit" [disabled]="busy()">
          {{ busy() ? 'Registering…' : 'Register version' }}
        </button>
      </mat-dialog-actions>
    </form>
  `,
  styles: `
    .link {
      all: unset;
      cursor: pointer;
      color: var(--accent-blue);
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-left: 6px;
    }
    .link:focus-visible {
      outline: 2px solid var(--focus);
      outline-offset: 2px;
    }
  `,
})
export class RegisterVersionDialog {
  protected readonly data = inject<RegisterVersionData>(MAT_DIALOG_DATA);
  private readonly api = inject(ApiService);
  private readonly ref = inject<MatDialogRef<RegisterVersionDialog, Version>>(MatDialogRef);

  protected readonly form = inject(FormBuilder).nonNullable.group({
    version: ['', [Validators.required, Validators.pattern(SEMVER)]],
    algorithm: ['', Validators.required],
    artifact_uri: ['', Validators.required],
    artifact_digest: ['', [Validators.required, Validators.pattern(DIGEST)]],
    training_data_ref: ['', Validators.required],
    runtime_type: ['generic', Validators.required],
    tags: [''],
    metadata: ['', jsonObject],
  });

  protected readonly busy = signal(false);
  protected readonly problem = signal<ApiError | null>(null);

  protected hasFieldErrors(): boolean {
    return Object.keys(this.problem()?.fieldMessages ?? {}).length > 0;
  }

  protected error(name: keyof typeof this.form.controls): string | null {
    const control = this.form.controls[name];
    if (control.touched) {
      if (control.errors?.['required']) return 'This is required.';
      if (name === 'version' && control.errors?.['pattern'])
        return 'Use a semantic version such as 2.0.0.';
      if (name === 'artifact_digest' && control.errors?.['pattern']) {
        return 'Use sha256: followed by 64 lowercase hex characters.';
      }
      if (name === 'metadata' && control.errors?.['json'])
        return 'Enter a JSON object, such as {"a": 1}.';
    }
    return this.problem()?.fieldMessages[name] ?? null;
  }

  protected fillSample(): void {
    const version = this.form.controls.version.value || this.data.suggested;
    this.form.patchValue({
      version,
      algorithm: 'gradient-boosting',
      artifact_uri: `s3://models/${this.data.modelId}/${version}`,
      artifact_digest: `sha256:${'a'.repeat(64)}`,
      training_data_ref: `s3://datasets/${this.data.modelId}/latest`,
      tags: 'candidate',
      metadata: '{"accuracy": 0.93}',
    });
    this.form.markAsTouched();
  }

  protected submit(): void {
    this.form.markAllAsTouched();
    if (this.form.invalid || this.busy()) return;
    const value = this.form.getRawValue();
    this.busy.set(true);
    this.problem.set(null);
    this.api
      .createVersion(this.data.modelId, {
        version: value.version.trim(),
        algorithm: value.algorithm.trim(),
        artifact_uri: value.artifact_uri.trim(),
        artifact_digest: value.artifact_digest.trim(),
        training_data_ref: value.training_data_ref.trim(),
        runtime_type: value.runtime_type.trim(),
        tags: value.tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
        metadata: value.metadata.trim()
          ? (JSON.parse(value.metadata) as Record<string, unknown>)
          : {},
      })
      .subscribe({
        next: (version) => this.ref.close(version),
        error: (error: unknown) => {
          this.busy.set(false);
          this.problem.set(ApiError.from(error));
        },
      });
  }
}
