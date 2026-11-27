import type { ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router";
import { useAuth } from "../auth/AuthProvider";
import { setLoginNotice } from "../auth/loginNotice";

export function Layout({ children }: { children: ReactNode }) {
  const { status, user, logOut } = useAuth();
  const navigate = useNavigate();

  async function handleLogOut() {
    setLoginNotice("You're logged out.");
    await logOut().catch(() => undefined); // logged out in this tab either way
    navigate("/login", { replace: true });
  }

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="masthead">
        <Link to="/" className="wordmark" aria-label="TicketLoop home">
          {/* The ticket mark in two inks: pink printed first, blue overprinted a little off. */}
          <svg viewBox="0 0 34 34" aria-hidden="true" className="wordmark__mark">
            <path fill="var(--pink)" d="M6 9h24v6a3 3 0 0 0 0 6v6H6v-6a3 3 0 0 0 0-6z" />
            <path
              className="wordmark__ink"
              fill="var(--blue)"
              d="M4 7h24v6a3 3 0 0 0 0 6v6H4v-6a3 3 0 0 0 0-6z"
            />
            <path
              stroke="var(--paper)"
              strokeWidth="1.5"
              strokeDasharray="2 2"
              d="M20 9v14"
              fill="none"
            />
          </svg>
          TicketLoop
        </Link>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Find tickets
          </NavLink>
          {status === "signed-in" && user && (
            <>
              <NavLink to="/tickets">My tickets</NavLink>
              {/* `end`: a listing page is anyone's listing, not one of mine. */}
              <NavLink to="/listings" end>
                My listings
              </NavLink>
              <NavLink to="/reservations">My holds</NavLink>
              <NavLink to="/orders">My orders</NavLink>
              {user.role === "ADMIN" && (
                <>
                  <NavLink to="/admin/events">Events</NavLink>
                  <NavLink to="/admin/tickets">Verify tickets</NavLink>
                  <NavLink to="/admin/listings">Review listings</NavLink>
                </>
              )}
              <NavLink to="/account">{user.full_name.split(" ")[0]}</NavLink>
              <button type="button" className="link-button" onClick={handleLogOut}>
                Log out
              </button>
            </>
          )}
          {status === "signed-out" && (
            <>
              <NavLink to="/login">Log in</NavLink>
              <NavLink to="/register">Create account</NavLink>
            </>
          )}
        </nav>
      </header>

      <main id="main" className="page">
        {children}
      </main>

      {import.meta.env.DEV && (
        <footer className="devbar">
          <span>Running locally:</span>
          <a href="http://localhost:8025" target="_blank" rel="noreferrer">
            Mailpit inbox
          </a>
          <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">
            API docs
          </a>
        </footer>
      )}
    </div>
  );
}
