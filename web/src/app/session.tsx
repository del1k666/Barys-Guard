import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api, onUnauthorized } from "../api/client";
import type { SessionUser } from "../api/types";

interface SessionState {
  user: SessionUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<SessionUser>;
  logout: () => Promise<void>;
  refresh: () => Promise<unknown>;
}

const SessionContext = createContext<SessionState | null>(null);

export const ME_KEY = ["auth", "me"] as const;

export function SessionProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const navigate = useNavigate();

  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      try {
        return await api.get<SessionUser>("/auth/me");
      } catch (error) {
        // Отсутствие сессии — не сбой запроса, а обычное состояние
        // неавторизованного браузера.
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    retry: false,
    staleTime: 30_000,
  });

  useEffect(() => {
    // Сессия может истечь между запросами. Клиент сообщает об этом один
    // раз, здесь кеш сбрасывается и оператор оказывается на входе.
    onUnauthorized(() => {
      client.setQueryData(ME_KEY, null);
      client.clear();
      navigate("/login", { replace: true });
    });
    return () => onUnauthorized(() => {});
  }, [client, navigate]);

  const login = useMutation({
    mutationFn: (credentials: { username: string; password: string }) =>
      api.post<SessionUser>("/auth/login", credentials),
    onSuccess: (user) => client.setQueryData(ME_KEY, user),
  });

  const logout = useMutation({
    mutationFn: () => api.post<null>("/auth/logout"),
    onSuccess: () => {
      client.setQueryData(ME_KEY, null);
      client.clear();
    },
  });

  const value = useMemo<SessionState>(
    () => ({
      user: me.data ?? null,
      loading: me.isPending,
      login: async (username, password) => login.mutateAsync({ username, password }),
      logout: async () => {
        await logout.mutateAsync();
      },
      refresh: () => client.invalidateQueries({ queryKey: ME_KEY }),
    }),
    [me.data, me.isPending, login, logout, client],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useSession вызван вне SessionProvider");
  }
  return value;
}
