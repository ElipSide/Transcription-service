import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import JobDetailPage from "./pages/JobDetail";
import Login from "./pages/Login";
import Register from "./pages/Register";

export default function App() {
  const { user, isGuest, loading } = useAuth();
  // Registration is optional: an anonymous session starts automatically, so the
  // app is usable without logging in. Only a *real* account gets bounced away
  // from the login pages — a guest may well be on the way to signing in.
  const signedIn = !!user && !isGuest;

  if (loading) return <div className="center muted">Загрузка…</div>;

  return (
    <Routes>
      <Route path="/login" element={signedIn ? <Navigate to="/" replace /> : <Login />} />
      <Route
        path="/register"
        element={signedIn ? <Navigate to="/" replace /> : <Register />}
      />
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/jobs/:id" element={<JobDetailPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
