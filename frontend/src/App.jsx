import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext.jsx';
import { ProtectedRoute } from './components/ProtectedRoute.jsx';
import { TopBar } from './components/TopBar.jsx';
import { Login } from './pages/Login.jsx';
import { Dashboard } from './pages/Dashboard.jsx';
import { PositionsDetail } from './pages/PositionsDetail.jsx';
import { RiskDetail } from './pages/RiskDetail.jsx';
import { TradeLogDetail } from './pages/TradeLogDetail.jsx';
import { RegimeDetail } from './pages/RegimeDetail.jsx';
import { BacktestDetail } from './pages/BacktestDetail.jsx';
import { EarningsDetail } from './pages/EarningsDetail.jsx';
import { HermesChat } from './pages/HermesChat.jsx';

const THEME_STORAGE_KEY = 'rentgen-theme';

function AppShell({ theme, onThemeChange }) {
  return (
    <div className="app-shell">
      <TopBar theme={theme} onThemeChange={onThemeChange} />
      <div className="page-content">
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/positions" element={<PositionsDetail />} />
          <Route path="/risk" element={<RiskDetail />} />
          <Route path="/trades" element={<TradeLogDetail />} />
          <Route path="/regime" element={<RegimeDetail />} />
          <Route path="/backtest" element={<BacktestDetail />} />
          <Route path="/earnings" element={<EarningsDetail />} />
          <Route path="/hermes" element={<HermesChat />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </div>
    </div>
  );
}

export default function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem(THEME_STORAGE_KEY) || 'neon');

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/*"
            element={
              <ProtectedRoute>
                <AppShell theme={theme} onThemeChange={setTheme} />
              </ProtectedRoute>
            }
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
