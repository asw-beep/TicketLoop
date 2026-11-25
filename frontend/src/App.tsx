import { Route, Routes } from "react-router";
import { useAuth } from "./auth/AuthProvider";
import { GuestOnly, RequireAdmin, RequireSession } from "./components/Guards";
import { Layout } from "./components/Layout";
import { AccountPage } from "./pages/AccountPage";
import { AdminEventsPage } from "./pages/AdminEventsPage";
import { AdminListingsPage } from "./pages/AdminListingsPage";
import { AdminTicketsPage } from "./pages/AdminTicketsPage";
import { EventPage } from "./pages/EventPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { ListingPage } from "./pages/ListingPage";
import { LoginPage } from "./pages/LoginPage";
import { MyHoldsPage } from "./pages/MyHoldsPage";
import { MyListingsPage } from "./pages/MyListingsPage";
import { MyTicketsPage } from "./pages/MyTicketsPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ReservationPage } from "./pages/ReservationPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { SearchPage } from "./pages/SearchPage";
import { ApiUnavailablePage, NotFoundPage } from "./pages/StatusPages";
import { TicketPage } from "./pages/TicketPage";

export function App() {
  const { status } = useAuth();

  return (
    <Layout>
      {status === "unavailable" ? (
        <ApiUnavailablePage />
      ) : (
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/events/:eventId" element={<EventPage />} />
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
            path="/listings"
            element={
              <RequireSession>
                <MyListingsPage />
              </RequireSession>
            }
          />
          {/* Not behind RequireSession: anyone may open an ACTIVE listing's address. */}
          <Route path="/listings/:listingId" element={<ListingPage />} />
          <Route
            path="/reservations"
            element={
              <RequireSession>
                <MyHoldsPage />
              </RequireSession>
            }
          />
          <Route
            path="/reservations/:reservationId"
            element={
              <RequireSession>
                <ReservationPage />
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
          <Route
            path="/admin/listings"
            element={
              <RequireAdmin>
                <AdminListingsPage />
              </RequireAdmin>
            }
          />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      )}
    </Layout>
  );
}
