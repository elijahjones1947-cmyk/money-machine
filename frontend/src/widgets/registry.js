import { PositionsWidget } from './PositionsWidget.jsx';
import { RiskWidget } from './RiskWidget.jsx';
import { TradeLogWidget } from './TradeLogWidget.jsx';
import { RegimeWidget } from './RegimeWidget.jsx';
import { BacktestWidget } from './BacktestWidget.jsx';
import { HermesControlWidget } from './HermesControlWidget.jsx';
import { EquityWidget } from './EquityWidget.jsx';

// Single source of truth for every widget the dashboard grid knows
// about: id, display title, its summary component, and the detail page
// route it taps through to (null = no detail page, e.g. Hermes control).
export const WIDGET_REGISTRY = {
  positions: { title: 'Positions', Component: PositionsWidget, to: '/positions' },
  risk: { title: 'Halt status', Component: RiskWidget, to: '/risk' },
  tradelog: { title: 'Trade log', Component: TradeLogWidget, to: '/trades' },
  regime: { title: 'Regime', Component: RegimeWidget, to: '/regime' },
  backtest: { title: 'Backtest & live', Component: BacktestWidget, to: '/backtest' },
  hermes: { title: 'Hermes', Component: HermesControlWidget, to: null },
  equity: { title: 'Equity', Component: EquityWidget, to: '/equity' },
};

export const DEFAULT_WIDGET_IDS = ['equity', 'positions', 'risk', 'tradelog', 'regime', 'backtest', 'hermes'];
