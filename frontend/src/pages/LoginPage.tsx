import { useMutation } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { Link, useLocation } from "react-router";
import { useAuth } from "../auth/AuthProvider";
import { clearLoginNotice, useLoginNotice } from "../auth/loginNotice";
import {
  Field,
  FormError,
  Notice,
  SubmitButton,
  errorsByField,
  formValues,
} from "../components/Form";
import { Ticket } from "../components/Ticket";

export function LoginPage() {
  const { logIn } = useAuth();
  const { state } = useLocation();
  const notice = useLoginNotice();
  const login = useMutation({
    mutationFn: ({ email, password }: Record<string, string>) => logIn(email!, password!),
    onSuccess: clearLoginNotice, // GuestOnly leaves this page
  });
  const errors = errorsByField(login.error);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    login.mutate(formValues(event.currentTarget));
  }

  return (
    <Ticket
      stub={<h1>Log in</h1>}
      footer={
        <p>
          No account yet?{" "}
          <Link to="/register" state={state}>
            Create one
          </Link>
        </p>
      }
    >
      {notice && !login.error && <Notice>{notice}</Notice>}
      <form onSubmit={handleSubmit}>
        <FormError error={login.error} />
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
          autoComplete="current-password"
          required
          error={errors.password}
        />
        <div className="actions">
          <SubmitButton pending={login.isPending}>Log in</SubmitButton>
          <Link to="/forgot-password">Forgot your password?</Link>
        </div>
      </form>
    </Ticket>
  );
}
