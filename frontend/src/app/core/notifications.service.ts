import { Injectable, inject } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';

import { ApiError } from './api-error';

/** Short messages that confirm an action or explain a failure, with a reference to quote. */
@Injectable({ providedIn: 'root' })
export class Notifications {
  private readonly snackBar = inject(MatSnackBar);

  success(message: string): void {
    this.snackBar.open(message, 'Dismiss', {
      duration: 4000,
      panelClass: 'toast-success',
      politeness: 'polite',
    });
  }

  /** Errors stay until dismissed and carry the correlation ID, which can be copied. */
  error(error: unknown, context?: string): void {
    const apiError = ApiError.from(error);
    const lead = context ? `${context}: ` : '';
    const reference = apiError.correlationId ? ` (ref ${apiError.correlationId.slice(0, 8)})` : '';
    const ref = this.snackBar.open(
      `${lead}${apiError.detail}${reference}`,
      apiError.correlationId ? 'Copy ref' : 'Dismiss',
      { duration: 12000, panelClass: 'toast-error', politeness: 'assertive' },
    );
    if (apiError.correlationId) {
      ref.onAction().subscribe(() => {
        void navigator.clipboard?.writeText(apiError.correlationId ?? '');
      });
    }
  }
}
