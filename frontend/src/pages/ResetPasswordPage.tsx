import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/problem";
import { useAuth } from "../auth/AuthProvider";
import { setLoginNotice } from "../auth/loginNotice";
import { Field, FormError, SubmitButton, errorsByField, formValues } from "../components/Form";
import { Ticket } from "../components/Ticket";

/** The token arrives after "#" in the emailed link, so no server ever receives it. */
function tokenFromHash(hash: string): string | null {
  return new URLSearchParams(hash.replace(/^#/, "")).get("token");
}

export function ResetPasswordPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { forget } = useAuth();
  const [token] = useState(() => tokenFromHash(location.hash));

  useEffect(() => {
    // Take the token out of the address bar, so it is not left in the browser's history.
    if (location.hash) navigate(location.pathname, { replace: true });
  }, [location.hash, location.pathname, navigate]);

  const confirm = useMutation({
    mutationFn: ({ new_password }: Record<string, string>) =>
      unwrap(
        api.POST("/api/v1/auth/password-reset/confirm", {
          body: { token: token!, new_password: new_password! },
        }),
      ),
    onSuccess: () => {
      setLoginNotice("Password changed. Log in with your new password.");
      forget(); // the server just ended every session of this account
      navigate("/login", { replace: true });
    },
  });
  const errors = errorsByField(confirm.error);
  const linkSpent =
    confirm.error instanceof ApiError && confirm.error.code === "invalid-reset-token";

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    confirm.mutate(formValues(event.currentTarget));
  }

  return (
    <Ticket stub={<h1>New password</h1>}>
      {!token ? (
        <div className="message message--error" role="alert">
          <p>This page needs the link from your reset email.</p>
          <p>
            <Link to="/forgot-password">Send me a reset link</Link>
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit}>
          <FormError error={confirm.error}>
            {linkSpent && (
              <p>
                Links work once, for 30 minutes. <Link to="/forgot-password">Send a new link</Link>
              </p>
            )}
          </FormError>
          <Field
            label="New password"
            name="new_password"
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={128}
            hint="At least 12 characters. Saving it logs you out on every device."
            error={errors.new_password}
          />
          <div className="actions">
            <SubmitButton pending={confirm.isPending}>Save new password</SubmitButton>
          </div>
        </form>
      )}
    </Ticket>
  );
}
