import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

export interface ConfirmData {
  title: string;
  message: string;
  confirmLabel: string;
  /** Marks the action as risky (red button). */
  danger?: boolean;
  /** Ask for a free-text note, such as the reason for a decision. */
  commentLabel?: string;
}

export interface ConfirmResult {
  comment: string;
}

/** A small "are you sure" dialog, optionally collecting a comment. Closes with the result or nothing. */
@Component({
  selector: 'app-confirm-dialog',
  imports: [MatDialogModule, MatButtonModule, MatFormFieldModule, MatInputModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ data.title }}</h2>
    <mat-dialog-content>
      <p class="help-text">{{ data.message }}</p>
      @if (data.commentLabel) {
        <mat-form-field appearance="outline" style="width: 100%; margin-top: 14px">
          <mat-label>{{ data.commentLabel }}</mat-label>
          <textarea
            matInput
            rows="3"
            maxlength="500"
            [ngModel]="comment()"
            (ngModelChange)="comment.set($event)"
          ></textarea>
        </mat-form-field>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>Cancel</button>
      <button
        mat-flat-button
        type="button"
        [class.danger]="data.danger"
        [mat-dialog-close]="{ comment: comment() }"
        cdkFocusInitial
      >
        {{ data.confirmLabel }}
      </button>
    </mat-dialog-actions>
  `,
  styles: `
    .danger {
      --mat-button-filled-container-color: var(--crit);
      --mat-button-filled-label-text-color: #fff;
    }
  `,
})
export class ConfirmDialog {
  protected readonly data = inject<ConfirmData>(MAT_DIALOG_DATA);
  protected readonly ref = inject<MatDialogRef<ConfirmDialog, ConfirmResult>>(MatDialogRef);
  protected readonly comment = signal('');
}
