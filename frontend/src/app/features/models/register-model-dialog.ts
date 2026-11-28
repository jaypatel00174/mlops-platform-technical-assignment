import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { Model } from '../../api/types';
import { ApiError } from '../../core/api-error';
import { ApiService } from '../../core/api.service';
import { ErrorPanel } from '../../shared/states';

/** Turn a display name into a suggested id: "Pump Failure Predictor" becomes "pump-failure-predictor". */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63);
}

@Component({
  selector: 'app-register-model-dialog',
  imports: [
    ReactiveFormsModule,
    MatDialogModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    ErrorPanel,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>Register a model</h2>
    <form [formGroup]="form" (ngSubmit)="submit()">
      <mat-dialog-content>
        <p class="help-text">
          A model is the product (for example "Pump Failure Predictor"). Its trained, versioned
          artefacts are registered under it afterwards.
        </p>
        @if (problem(); as error) {
          @if (!hasFieldErrors()) {
            <app-error-panel [error]="error" (retry)="submit()" />
          }
        }
        <div class="form-grid">
          <mat-form-field appearance="outline" class="full">
            <mat-label>Name</mat-label>
            <input
              matInput
              formControlName="name"
              maxlength="255"
              (input)="suggestId()"
              cdkFocusInitial
            />
            @if (fieldError('name'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>

          <mat-form-field appearance="outline">
            <mat-label>Id</mat-label>
            <input matInput formControlName="id" maxlength="63" />
            <mat-hint>Lowercase letters, digits, hyphens. Cannot change later.</mat-hint>
            @if (fieldError('id'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>

          <mat-form-field appearance="outline">
            <mat-label>Framework</mat-label>
            <input
              matInput
              formControlName="framework"
              maxlength="255"
              placeholder="scikit-learn"
            />
            @if (fieldError('framework'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>

          <mat-form-field appearance="outline" class="full">
            <mat-label>Owner</mat-label>
            <input
              matInput
              formControlName="owner"
              maxlength="255"
              placeholder="Reliability AI Team"
            />
            @if (fieldError('owner'); as message) {
              <mat-error>{{ message }}</mat-error>
            }
          </mat-form-field>

          <mat-form-field appearance="outline" class="full">
            <mat-label>Tags</mat-label>
            <input matInput formControlName="tags" placeholder="plant-a, pumps" />
            <mat-hint>Separate with commas.</mat-hint>
          </mat-form-field>
        </div>
      </mat-dialog-content>
      <mat-dialog-actions align="end">
        <button mat-button type="button" mat-dialog-close [disabled]="busy()">Cancel</button>
        <button mat-flat-button type="submit" [disabled]="busy() || form.invalid">
          {{ busy() ? 'Registering…' : 'Register model' }}
        </button>
      </mat-dialog-actions>
    </form>
  `,
})
export class RegisterModelDialog {
  private readonly api = inject(ApiService);
  private readonly ref = inject<MatDialogRef<RegisterModelDialog, Model>>(MatDialogRef);

  protected readonly form = inject(FormBuilder).nonNullable.group({
    name: ['', [Validators.required, Validators.maxLength(255)]],
    id: ['', [Validators.required, Validators.pattern(/^[a-z0-9][a-z0-9-]{0,62}$/)]],
    framework: ['', [Validators.required]],
    owner: ['', [Validators.required]],
    tags: [''],
  });

  protected readonly busy = signal(false);
  protected readonly problem = signal<ApiError | null>(null);

  protected suggestId(): void {
    if (this.form.controls.id.dirty) return;
    this.form.controls.id.setValue(slugify(this.form.controls.name.value));
  }

  protected hasFieldErrors(): boolean {
    return Object.keys(this.problem()?.fieldMessages ?? {}).length > 0;
  }

  /** The browser-side rule first, then whatever the server said about this field. */
  protected fieldError(name: 'name' | 'id' | 'framework' | 'owner'): string | null {
    const control = this.form.controls[name];
    if (control.touched && control.errors?.['required']) return 'This is required.';
    if (control.touched && control.errors?.['pattern']) {
      return 'Use lowercase letters, digits and hyphens, starting with a letter or digit.';
    }
    return this.problem()?.fieldMessages[name] ?? null;
  }

  protected submit(): void {
    this.form.markAllAsTouched();
    if (this.form.invalid || this.busy()) return;
    const value = this.form.getRawValue();
    this.busy.set(true);
    this.problem.set(null);
    this.api
      .createModel({
        id: value.id,
        name: value.name.trim(),
        owner: value.owner.trim(),
        framework: value.framework.trim(),
        tags: value.tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
      })
      .subscribe({
        next: (model) => this.ref.close(model),
        error: (error: unknown) => {
          this.busy.set(false);
          this.problem.set(ApiError.from(error));
        },
      });
  }
}
