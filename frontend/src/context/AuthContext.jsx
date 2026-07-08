import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { api } from '../api.js';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [authenticated, setAuthenticated] = useState(null); // null = still checking
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .session()
      .then((r) => setAuthenticated(r.authenticated))
      .catch(() => setAuthenticated(false));
  }, []);

  const login = useCallback(async (password) => {
    setError(null);
    try {
      await api.login(password);
      setAuthenticated(true);
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    }
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setAuthenticated(false);
  }, []);

  return (
    <AuthContext.Provider value={{ authenticated, error, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
