import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { useAuth } from "../auth/AuthProvider";

/** Pages for a logged-in user. Anyone else goes to /login and comes back afterwards. */
export function RequireSession({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "signed-out") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return status === "signed-in" ? children : null;
}

/** Log in and create account: a logged-in user has no business there. */
export function GuestOnly({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "signed-in") return <Navigate to="/account" replace />;
  return status === "signed-out" ? children : null;
}
