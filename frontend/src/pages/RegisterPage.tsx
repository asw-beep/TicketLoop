import { useMutation } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { Link, useLocation } from "react-router";
import { api, unwrap } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Field, FormError, SubmitButton, errorsByField, formValues } from "../components/Form";
import { Ticket } from "../components/Ticket";

export function RegisterPage() {
  const { logIn } = useAuth();
  const { state } = useLocation();
  const register = useMutation({
    mutationFn: async ({ full_name, email, password }: Record<string, string>) => {
      const body = { full_name: full_name!, email: email!, password: password! };
      await unwrap(api.POST("/api/v1/auth/register", { body }));
      return logIn(body.email, body.password); // straight in; GuestOnly leaves this page
    },
  });
  const errors = errorsByField(register.error);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    register.mutate(formValues(event.currentTarget));
  }

  return (
    <Ticket
      stub={<h1>Create account</h1>}
      footer={
        <p>
          Already have an account?{" "}
          <Link to="/login" state={state}>
            Log in
          </Link>
        </p>
      }
    >
      <form onSubmit={handleSubmit}>
        <FormError error={register.error} />
        <Field
          label="Full name"
          name="full_name"
          autoComplete="name"
          required
          maxLength={200}
          error={errors.full_name}
        />
        <Field
          label="Email"
          name="email"
          type="email"
          autoComplete="email"
          required
          error={errors.email}
        />
        <Field
          label="Password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={128}
          hint="At least 12 characters. A short sentence is easy to remember and hard to guess."
          error={errors.password}
        />
        <div className="actions">
          <SubmitButton pending={register.isPending}>Create account</SubmitButton>
        </div>
      </form>
    </Ticket>
  );
}
