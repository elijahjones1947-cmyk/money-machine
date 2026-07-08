import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';

export function ProtectedRoute({ children }) {
  const { authenticated } = useAuth();

  if (authenticated === null) {
    return <div className="empty-state">Checking session…</div>;
  }
  if (!authenticated) {
    return <Navigate to="/login" replace />;
  }
  return children;
}
