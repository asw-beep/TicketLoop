import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { useNavigate } from "react-router";
import { api, unwrap } from "../api/client";
import { ME_KEY, useAuth } from "../auth/AuthProvider";
import { setLoginNotice } from "../auth/loginNotice";
import type { User } from "../auth/AuthProvider";
import {
  Field,
  FormError,
  Notice,
  SubmitButton,
  errorsByField,
  formValues,
} from "../components/Form";
import { Ticket } from "../components/Ticket";

const memberSince = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

const ROLE_NAMES: Record<User["role"], string> = { USER: "Member", ADMIN: "Admin" };
const STATUS_NAMES: Record<User["account_status"], string> = {
  ACTIVE: "Active",
  SUSPENDED: "Suspended",
};

export function AccountPage() {
  const { user } = useAuth();
  if (!user) return null; // RequireSession renders this page only once the user is loaded

  return (
    <>
      <Ticket
        stub={
          <>
            <h1>Your pass</h1>
            <p className="stub__serial" title="Your account ID">
              No. {user.id.slice(0, 8)}
            </p>
          </>
        }
      >
        <p className="pass__name">{user.full_name}</p>
        <p className="pass__email">{user.email}</p>
        <dl className="pass__details">
          <div>
            <dt>Role</dt>
            <dd>{ROLE_NAMES[user.role]}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{STATUS_NAMES[user.account_status]}</dd>
          </div>
          <div>
            <dt>Member since</dt>
            <dd>{memberSince.format(new Date(user.created_at))}</dd>
          </div>
        </dl>
      </Ticket>

      <NameSection user={user} />
      <PasswordSection />
    </>
  );
}

function NameSection({ user }: { user: User }) {
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: ({ full_name }: Record<string, string>) =>
      unwrap(api.PATCH("/api/v1/users/me", { body: { full_name: full_name! } })),
    onSuccess: (updated) => queryClient.setQueryData(ME_KEY, updated),
  });
  const errors = errorsByField(save.error);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate(formValues(event.currentTarget));
  }

  return (
    <section className="panel" aria-labelledby="name-heading">
      <h2 id="name-heading">Your name</h2>
      <form onSubmit={handleSubmit} onChange={() => save.isSuccess && save.reset()}>
        <FormError error={save.error} />
        {save.isSuccess && <Notice>Name saved.</Notice>}
        <Field
          label="Full name"
          name="full_name"
          autoComplete="name"
          defaultValue={user.full_name}
          required
          maxLength={200}
          error={errors.full_name}
        />
        <div className="actions">
          <SubmitButton pending={save.isPending}>Save name</SubmitButton>
        </div>
      </form>
    </section>
  );
}

function PasswordSection() {
  const { forget } = useAuth();
  const navigate = useNavigate();
  const change = useMutation({
    mutationFn: ({ current_password, new_password }: Record<string, string>) =>
      unwrap(
        api.POST("/api/v1/users/me/password", {
          body: { current_password: current_password!, new_password: new_password! },
        }),
      ),
    onSuccess: () => {
      setLoginNotice("Password changed. You're logged out everywhere, so log in again.");
      forget(); // the server has ended every session, this one included
      navigate("/login", { replace: true });
    },
  });
  const errors = errorsByField(change.error);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    change.mutate(formValues(event.currentTarget));
  }

  return (
    <section className="panel" aria-labelledby="password-heading">
      <h2 id="password-heading">Password</h2>
      <p className="lede">Changing it logs you out on every device, this one included.</p>
      <form onSubmit={handleSubmit}>
        <FormError error={change.error} />
        <Field
          label="Current password"
          name="current_password"
          type="password"
          autoComplete="current-password"
          required
          error={errors.current_password}
        />
        <Field
          label="New password"
          name="new_password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={128}
          hint="At least 12 characters."
          error={errors.new_password}
        />
        <div className="actions">
          <SubmitButton pending={change.isPending}>Change password</SubmitButton>
        </div>
      </form>
    </section>
  );
}
