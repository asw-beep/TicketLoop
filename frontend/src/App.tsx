import { Route, Routes } from "react-router";
import { useAuth } from "./auth/AuthProvider";
import { GuestOnly, RequireAdmin, RequireSession } from "./components/Guards";
import { Layout } from "./components/Layout";
import { AccountPage } from "./pages/AccountPage";
import { AdminEventsPage } from "./pages/AdminEventsPage";
import { AdminTicketsPage } from "./pages/AdminTicketsPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { MyTicketsPage } from "./pages/MyTicketsPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { ApiUnavailablePage, HomeRedirect, NotFoundPage } from "./pages/StatusPages";
import { TicketPage } from "./pages/TicketPage";

export function App() {
  const { status } = useAuth();

  return (
    <Layout>
      {status === "unavailable" ? (
        <ApiUnavailablePage />
      ) : (
        <Routes>
          <Route path="/" element={<HomeRedirect />} />
          <Route
            path="/login"
            element={
              <GuestOnly>
                <LoginPage />
              </GuestOnly>
            }
          />
          <Route
            path="/register"
            element={
              <GuestOnly>
                <RegisterPage />
              </GuestOnly>
            }
          />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route
            path="/account"
            element={
              <RequireSession>
                <AccountPage />
              </RequireSession>
            }
          />
          <Route
            path="/tickets"
            element={
              <RequireSession>
                <MyTicketsPage />
              </RequireSession>
            }
          />
          <Route
            path="/tickets/:ticketId"
            element={
              <RequireSession>
                <TicketPage />
              </RequireSession>
            }
          />
          <Route
            path="/admin/events"
            element={
              <RequireAdmin>
                <AdminEventsPage />
              </RequireAdmin>
            }
          />
          <Route
            path="/admin/tickets"
            element={
              <RequireAdmin>
                <AdminTicketsPage />
              </RequireAdmin>
            }
          />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      )}
    </Layout>
  );
}
