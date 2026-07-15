import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

function DiarStatusBanner() {
  const [available, setAvailable] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const { available } = await api.diarStatus();
        if (alive) setAvailable(available);
      } catch {
        /* ignore transient errors */
      }
    };
    check();
    const id = window.setInterval(check, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (available !== false) return null;
  return (
    <div className="diar-banner">
      ⚠️ Сервис распознавания сейчас недоступен. Вы можете загружать файлы — они
      встанут в очередь и обработаются автоматически, как только сервис вернётся.
    </div>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          🎙 Транскрипция
        </Link>
        <div className="topbar-right">
          <span className="muted">{user?.email}</span>
          <button className="btn ghost" onClick={logout}>
            Выйти
          </button>
        </div>
      </header>
      <DiarStatusBanner />
      <main className="container">
        <Outlet />
      </main>
    </div>
  );
}
