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
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const started = useRef(false); // StrictMode runs effects twice in dev

  const boot = useCallback(async () => {
    try {
      if (tokens.access) {
        try {
          setUser(await api.me());
          return;
        } catch {
          tokens.clear(); // expired/purged session -> fall through to a new guest
        }
      }
      // No valid session: start an anonymous one so the site is usable at once.
      tokens.set(await api.guest(), true);
      setUser(await api.me());
    } catch {
      tokens.clear();
      setUser(null);
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
  }, []);

  const register = useCallback(
    async (email: string, password: string, fullName?: string) => {
      tokens.set(await api.register(email, password, fullName));
      setUser(await api.me());
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
    () => ({ user, isGuest: !!user?.is_guest, loading, login, register, logout }),
    [user, loading, login, register, logout]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
