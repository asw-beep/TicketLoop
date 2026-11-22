import { Route, Routes } from "react-router";
import { useAuth } from "./auth/AuthProvider";
import { GuestOnly, RequireSession } from "./components/Guards";
import { Layout } from "./components/Layout";
import { AccountPage } from "./pages/AccountPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { ApiUnavailablePage, HomeRedirect, NotFoundPage } from "./pages/StatusPages";

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
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      )}
    </Layout>
  );
}
