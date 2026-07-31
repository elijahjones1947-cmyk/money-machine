import { PositionsWidget } from './PositionsWidget.jsx';
import { StockHaltWidget, ForexHaltWidget, CryptoHaltWidget } from './HaltStatusWidgets.jsx';
import { TradeLogWidget } from './TradeLogWidget.jsx';
import { RegimeWidget } from './RegimeWidget.jsx';
import { BacktestWidget } from './BacktestWidget.jsx';
import { HermesControlWidget } from './HermesControlWidget.jsx';
import { EquityWidget } from './EquityWidget.jsx';
import { NotesWidget } from './NotesWidget.jsx';
import { WatchlistCapacityWidget } from './WatchlistCapacityWidget.jsx';
import { TradeRationaleWidget } from './TradeRationaleWidget.jsx';
import { AlertHealthWidget } from './AlertHealthWidget.jsx';
import { RecentErrorsWidget } from './RecentErrorsWidget.jsx';
import { MonthlyProfitWidget } from './MonthlyProfitWidget.jsx';

// Single source of truth for every widget the dashboard grid knows
// about: id, display title, its summary component, and the detail page
// route it taps through to (null = no detail page, e.g. Hermes control).
// `description` is optional -- shown as an (i) tooltip next to the title
// (Widget.jsx) -- most widgets are self-explanatory enough not to need
// one; only add it where the widget's meaning genuinely isn't obvious
// from its title + content alone.
export const WIDGET_REGISTRY = {
  positions: { title: 'Positions', Component: PositionsWidget, to: '/positions' },
  tradelog: { title: 'Trade log', Component: TradeLogWidget, to: '/trades' },
  regime: { title: 'Regime', Component: RegimeWidget, to: '/regime' },
  backtest: { title: 'Backtest & live', Component: BacktestWidget, to: '/backtest' },
  hermes: { title: 'Hermes', Component: HermesControlWidget, to: '/hermes' },
  equity: { title: 'Equity', Component: EquityWidget, to: '/equity' },
  notes: { title: 'Notes', Component: NotesWidget, to: '/notes' },
  watchlistCapacity: {
    title: 'Bucket of Funds capacity', Component: WatchlistCapacityWidget, to: '/capacity',
    description: 'Read-only view of watchlist size vs. the max_open_positions cap per asset class. To change the cap or the watchlist itself, use Settings.',
  },
  tradeRationale: { title: 'Trade rationale', Component: TradeRationaleWidget, to: '/trade-rationale' },
  alertHealth: {
    title: 'Alert health', Component: AlertHealthWidget, to: '/alert-health',
    description: "Time since the last webhook signal received per watched symbol, so a TradingView alert that's gone silent is easy to spot.",
  },
  recentErrors: { title: 'Recent errors', Component: RecentErrorsWidget, to: '/errors' },
  monthlyProfit: {
    title: 'Monthly profit', Component: MonthlyProfitWidget, to: '/monthly-profit',
    description: 'Month-to-date REALIZED P&L (closed trades only, not open-position P&L or the equity curve) against the monthly goal set in Settings. Resets each calendar month.',
  },
  // Split out of the single, taller "Halt status" card into 3 compact
  // per-asset-class widgets -- same underlying /api/dashboard risk_state
  // data, all tapping through to the same /risk detail page (not
  // duplicated info across 3 pages, just 3 small glances at one).
  riskStock: { title: 'Stock halt', Component: StockHaltWidget, to: '/risk', compact: true },
  riskForex: { title: 'Forex halt', Component: ForexHaltWidget, to: '/risk', compact: true },
  riskCrypto: { title: 'Crypto halt', Component: CryptoHaltWidget, to: '/risk', compact: true },
};

export const DEFAULT_WIDGET_IDS = [
  'monthlyProfit', 'equity', 'positions', 'tradelog', 'regime', 'backtest', 'hermes', 'notes',
  'watchlistCapacity', 'tradeRationale', 'alertHealth', 'recentErrors',
  // Compact widgets last so they land at the bottom of the grid.
  'riskStock', 'riskForex', 'riskCrypto',
];
