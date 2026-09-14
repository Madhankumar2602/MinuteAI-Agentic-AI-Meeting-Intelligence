import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "../api/endpoints";
import type { User } from "../api/types";
import { session } from "./session";
import { AuthContext } from "./useAuth";

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [token, setToken] = useState<string | null>(session.getToken);
  const [user, setUser] = useState<User | null>(null);

  const logout = useCallback(() => {
    session.setToken(null);
    setToken(null);
    setUser(null);
    // Nothing cached from the previous session may be shown to the next one.
    queryClient.clear();
  }, [queryClient]);

  // An expired or revoked token anywhere in the app signs the user out.
  useEffect(() => session.onUnauthorized(logout), [logout]);

  // A token restored from storage must be confirmed with the server before
  // the user is treated as signed in.
  useEffect(() => {
    if (!token || user) return;
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        if (!cancelled) logout();
      });
    return () => {
      cancelled = true;
    };
  }, [token, user, logout]);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.login(email, password);
    session.setToken(result.access_token);
    const me = await api.me();
    setUser(me);
    setToken(result.access_token);
  }, []);

  const register = useCallback(
    async (email: string, password: string, fullName: string) => {
      await api.register({ email, password, full_name: fullName });
      await login(email, password);
    },
    [login],
  );

  // Derived, not stored: loading exactly while a token awaits confirmation.
  const loading = token !== null && user === null;

  const value = useMemo(
    () => ({ user, token, loading, login, register, logout }),
    [user, token, loading, login, register, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
