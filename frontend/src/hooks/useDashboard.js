import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '../api.js';

// Single shared poll for the whole dashboard payload (equity, positions,
// risk state, trade log, regimes, watchlist, bot toggle). Every widget's
// summary AND its full detail page both read from this same hook — per
// the roadmap, detail pages should reuse the same data-fetching hooks as
// their widget summary, just render more of it.
export function useDashboard(pollMs = 15000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef(null);

  const refetch = useCallback(async () => {
    try {
      const result = await api.dashboard();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
    if (pollMs > 0) {
      timerRef.current = setInterval(refetch, pollMs);
      return () => clearInterval(timerRef.current);
    }
  }, [refetch, pollMs]);

  return { data, error, loading, refetch };
}
