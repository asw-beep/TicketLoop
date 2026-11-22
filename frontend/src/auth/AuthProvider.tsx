/**
 * Who is logged in, for every page. On first load it asks /auth/refresh for an access token
 * (the cookie survives reloads, the in-memory token does not), then loads /users/me.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import { getAccessToken, onAccessTokenChange, refreshAccessToken, setAccessToken } from "./session";

export type User = components["schemas"]["UserOut"];

export type AuthStatus = "loading" | "unavailable" | "signed-out" | "signed-in";

interface AuthContextValue {
  status: AuthStatus;
  user: User | null;
  logIn: (email: string, password: string) => Promise<User>;
  logOut: () => Promise<void>;
  /** Forget the session in this tab only, after the server has already ended it. */
  forget: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const ME_KEY = ["me"] as const;

export function fetchMe(): Promise<User> {
  return unwrap(api.GET("/api/v1/users/me"));
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const token = useSyncExternalStore(onAccessTokenChange, getAccessToken);

  // A query, not an effect: React's development double-render would otherwise send two
  // refreshes with one cookie, which the server reads as a stolen token.
  const restore = useQuery({
    queryKey: ["session-restore"],
    queryFn: refreshAccessToken,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const me = useQuery({ queryKey: ME_KEY, queryFn: fetchMe, enabled: token !== null });

  const logIn = useCallback(
    async (email: string, password: string) => {
      const body = await unwrap(api.POST("/api/v1/auth/login", { body: { email, password } }));
      setAccessToken(body.access_token);
      return queryClient.fetchQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: 0 });
    },
    [queryClient],
  );

  const forget = useCallback(() => {
    setAccessToken(null);
    queryClient.removeQueries({ queryKey: ME_KEY });
  }, [queryClient]);

  const logOut = useCallback(async () => {
    try {
      await unwrap(api.POST("/api/v1/auth/logout")); // the cookie says which session to end
    } finally {
      forget(); // even if the request failed, this tab stops acting as logged in
    }
  }, [forget]);

  const value = useMemo<AuthContextValue>(() => {
    let status: AuthStatus;
    if (restore.isPending) status = "loading";
    else if (restore.isError) status = "unavailable";
    else if (token === null) status = "signed-out";
    else if (me.data) status = "signed-in";
    else if (me.isError) status = "unavailable";
    else status = "loading";
    return { status, user: me.data ?? null, logIn, logOut, forget };
  }, [restore.isPending, restore.isError, token, me.data, me.isError, logIn, logOut, forget]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
