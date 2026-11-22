import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { ApiError, describeError, fieldErrors } from "../api/problem";

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  name: string;
  hint?: ReactNode;
  error?: string;
};

/** A labelled input. Its hint and error are tied to it, so screen readers announce them. */
export function Field({ label, name, hint, error, id = name, ...input }: FieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        name={name}
        aria-invalid={error ? true : undefined}
        aria-describedby={[hintId, errorId].filter(Boolean).join(" ") || undefined}
        {...input}
      />
      {hint && (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      )}
      {error && (
        <p className="field__error" id={errorId}>
          {error}
        </p>
      )}
    </div>
  );
}

type Described = { label: string; name: string; hint?: ReactNode; error?: string; id?: string };

/** The ids that tie a control to its hint and error, for aria-describedby. */
function describedBy(id: string, hint?: ReactNode, error?: string) {
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return { hintId, errorId, ids: [hintId, errorId].filter(Boolean).join(" ") || undefined };
}

function FieldNotes({
  hint,
  error,
  hintId,
  errorId,
}: Partial<Described> & {
  hintId?: string;
  errorId?: string;
}) {
  return (
    <>
      {hint && (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      )}
      {error && (
        <p className="field__error" id={errorId}>
          {error}
        </p>
      )}
    </>
  );
}

/** A labelled drop-down, wired up like Field. */
export function SelectField({
  label,
  name,
  hint,
  error,
  id = name,
  children,
  ...select
}: Described & SelectHTMLAttributes<HTMLSelectElement>) {
  const notes = describedBy(id, hint, error);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        name={name}
        aria-invalid={error ? true : undefined}
        aria-describedby={notes.ids}
        {...select}
      >
        {children}
      </select>
      <FieldNotes hint={hint} error={error} hintId={notes.hintId} errorId={notes.errorId} />
    </div>
  );
}

/** A labelled multi-line text box, wired up like Field. */
export function TextAreaField({
  label,
  name,
  hint,
  error,
  id = name,
  ...textarea
}: Described & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const notes = describedBy(id, hint, error);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea
        id={id}
        name={name}
        aria-invalid={error ? true : undefined}
        aria-describedby={notes.ids}
        {...textarea}
      />
      <FieldNotes hint={hint} error={error} hintId={notes.hintId} errorId={notes.errorId} />
    </div>
  );
}

/**
 * What went wrong with a submit. Field-level validation errors are shown on their fields,
 * so this banner then only says that some fields need fixing.
 */
export function FormError({ error, children }: { error: unknown; children?: ReactNode }) {
  if (!error) return null;
  return (
    <div className="message message--error" role="alert">
      <p>{describeError(error)}</p>
      {children}
    </div>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="message message--ok" role="status">
      {children}
    </div>
  );
}

/** Validation messages from a failed submit, keyed by field name. */
export function errorsByField(error: unknown): Record<string, string> {
  return error instanceof ApiError ? fieldErrors(error.problem) : {};
}

export function SubmitButton({ pending, children }: { pending: boolean; children: ReactNode }) {
  return (
    <button className="button" type="submit" disabled={pending} aria-busy={pending}>
      {children}
    </button>
  );
}

/** The values of a submitted form, as strings. */
export function formValues(form: HTMLFormElement): Record<string, string> {
  return Object.fromEntries(
    [...new FormData(form).entries()].map(([key, value]) => [key, String(value)]),
  );
}
