import { useMutation } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { Link } from "react-router";
import { api, unwrap } from "../api/client";
import {
  Field,
  FormError,
  Notice,
  SubmitButton,
  errorsByField,
  formValues,
} from "../components/Form";
import { Ticket } from "../components/Ticket";

export function ForgotPasswordPage() {
  const request = useMutation({
    mutationFn: async ({ email }: Record<string, string>) =>
      unwrap(api.POST("/api/v1/auth/password-reset/request", { body: { email: email! } })),
  });
  const errors = errorsByField(request.error);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    request.mutate(formValues(event.currentTarget));
  }

  return (
    <Ticket
      stub={<h1>Reset password</h1>}
      footer={
        <p>
          Remembered it? <Link to="/login">Log in</Link>
        </p>
      }
    >
      {request.isSuccess ? (
        <Notice>
          {/* The same words whether or not the account exists: the server sends no hint. */}
          <p>
            If an account uses that email, a reset link is on its way. It works once, for 30
            minutes.
          </p>
          {import.meta.env.DEV && (
            <p>
              Running locally, the email lands in the{" "}
              <a href="http://localhost:8025" target="_blank" rel="noreferrer">
                Mailpit inbox
              </a>
              .
            </p>
          )}
        </Notice>
      ) : (
        <form onSubmit={handleSubmit}>
          <p className="lede">
            Enter the email you signed up with and we'll send you a link to choose a new password.
          </p>
          <FormError error={request.error} />
          <Field
            label="Email"
            name="email"
            type="email"
            autoComplete="email"
            required
            error={errors.email}
          />
          <div className="actions">
            <SubmitButton pending={request.isPending}>Send reset link</SubmitButton>
          </div>
        </form>
      )}
    </Ticket>
  );
}
