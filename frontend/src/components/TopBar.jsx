import { Link } from 'react-router-dom';
import { useDashboard } from '../hooks/useDashboard.js';
import { useAuth } from '../context/AuthContext.jsx';
import { api } from '../api.js';
import { ThemeSwitcher } from './ThemeSwitcher.jsx';
import './TopBar.css';

export function TopBar({ theme, onThemeChange }) {
  const { data, refetch } = useDashboard(0); // no separate poll, just reuse cache-ish fetch on mount
  const { logout } = useAuth();

  const handleToggleBot = async () => {
    await api.toggleBot();
    refetch();
  };

  return (
    <div className="topbar">
      <div className="topbar-left">
        <Link to="/dashboard" className="topbar-logo">Rent Generator</Link>
        {data && (
          <span className={`mode-pill ${data.trading_mode === 'live' ? 'live' : ''}`}>{data.trading_mode}</span>
        )}
      </div>
      <div className="topbar-right">
        <ThemeSwitcher theme={theme} onChange={onThemeChange} />
        {data && (
          <span className="pill">
            <span className={`pill-dot ${data.bot_enabled ? '' : 'off'}`} />
            {data.bot_enabled ? 'Bot running' : 'Bot paused'}
          </span>
        )}
        <button className="button" onClick={handleToggleBot}>
          {data?.bot_enabled ? 'Pause' : 'Resume'}
        </button>
        <Link to="/strategies" className="button">Strategies</Link>
        <Link to="/settings" className="button">Settings</Link>
        <button className="button" onClick={logout}>Log out</button>
      </div>
    </div>
  );
}
