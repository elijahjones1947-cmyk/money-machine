import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '../api.js';

// Polls like useDashboard does -- live_performance (real closed trades,
// broken out per asset class) is computed fresh on every /api/backtest
// call, so the Backtest page needs to keep re-fetching to actually read
// as "continuously updating" rather than a one-shot snapshot. `results`
// (the simulated strategy backtest) only changes when someone reruns
// `python -m backtest.runner`, but re-fetching it alongside is cheap and
// keeps `generated_at` current if that happens while the page is open.
export function useBacktest(pollMs = 20000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef(null);

  const refetch = useCallback(async () => {
    try {
      const result = await api.backtest();
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
