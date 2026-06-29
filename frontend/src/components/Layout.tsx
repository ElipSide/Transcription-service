import { Link, Outlet } from "react-router-dom";
import { useAuth } from "../auth";

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
      <main className="container">
        <Outlet />
      </main>
    </div>
  );
}
