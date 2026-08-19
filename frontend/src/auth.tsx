import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, tokens } from "./api";
import type { User } from "./types";

interface AuthCtx {
  user: User | null;
  /** True while working anonymously (results are temporary). */
  isGuest: boolean;
  loading: boolean;
  /** Set when even an anonymous session could not be started. */
  sessionError: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const started = useRef(false); // StrictMode runs effects twice in dev

  const boot = useCallback(async () => {
    try {
      if (tokens.access) {
        try {
          setUser(await api.me());
          setSessionError(null);
          return;
        } catch {
          tokens.clear(); // expired/purged session -> fall through to a new guest
        }
      }
      // No valid session: start an anonymous one so the site is usable at once.
      tokens.set(await api.guest(), true);
      setUser(await api.me());
      setSessionError(null);
    } catch {
      // Guest mode unavailable (e.g. backend older than the frontend). Stay
      // usable: the header falls back to a "Войти" link instead of a dead end.
      tokens.clear();
      setUser(null);
      setSessionError(
        "Не удалось начать гостевую сессию. Войдите в аккаунт — или обновите сервер, если гостевой режим ещё не развёрнут."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    boot();
  }, [boot]);

  const login = useCallback(async (email: string, password: string) => {
    tokens.set(await api.login(email, password));
    setUser(await api.me());
    setSessionError(null);
  }, []);

  const register = useCallback(
    async (email: string, password: string, fullName?: string) => {
      tokens.set(await api.register(email, password, fullName));
      setUser(await api.me());
      setSessionError(null);
    },
    []
  );

  /** Leaving the account drops back to a fresh anonymous session. */
  const logout = useCallback(() => {
    tokens.clear();
    setUser(null);
    setLoading(true);
    started.current = false;
    boot();
  }, [boot]);

  const value = useMemo(
    () => ({
      user,
      isGuest: !!user?.is_guest,
      loading,
      sessionError,
      login,
      register,
      logout,
    }),
    [user, loading, sessionError, login, register, logout]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
