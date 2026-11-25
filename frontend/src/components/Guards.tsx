import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { useAuth } from "../auth/AuthProvider";
import { NotFoundPage } from "../pages/StatusPages";

/** Router state on the way to /login or /register: where to go afterwards. */
export type LoginState = { from?: string } | null;

/** Pages for a logged-in user. Anyone else goes to /login and comes back afterwards. */
export function RequireSession({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "signed-out") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return status === "signed-in" ? children : null;
}

/**
 * Admin screens. Anyone else signed in sees the not-found page, as if the screen did not
 * exist; the API refuses their calls with 403 either way.
 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return <RequireSession>{user?.role === "ADMIN" ? children : <NotFoundPage />}</RequireSession>;
}

/**
 * Log in and create account: a logged-in user has no business there. This is also what takes
 * someone back, the moment they are logged in, to the page that sent them to log in.
 */
export function GuestOnly({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const state = useLocation().state as LoginState;
  if (status === "signed-in") return <Navigate to={state?.from ?? "/account"} replace />;
  return status === "signed-out" ? children : null;
}
