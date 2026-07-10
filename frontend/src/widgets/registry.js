import { PositionsWidget } from './PositionsWidget.jsx';
import { RiskWidget } from './RiskWidget.jsx';
import { TradeLogWidget } from './TradeLogWidget.jsx';
import { RegimeWidget } from './RegimeWidget.jsx';
import { BacktestWidget } from './BacktestWidget.jsx';
import { EarningsWidget } from './EarningsWidget.jsx';
import { HermesControlWidget } from './HermesControlWidget.jsx';
import { EquityWidget } from './EquityWidget.jsx';

// Single source of truth for every widget the dashboard grid and the
// edit-mode tray both know about: id, display title, its summary
// component, the detail page route it taps through to (null = no detail
// page, e.g. Hermes control per the roadmap), and a default grid size.
export const WIDGET_REGISTRY = {
  positions: { title: 'Positions', Component: PositionsWidget, to: '/positions', defaultSize: { w: 3, h: 4 } },
  risk: { title: 'Risk state', Component: RiskWidget, to: '/risk', defaultSize: { w: 3, h: 4 } },
  tradelog: { title: 'Trade log', Component: TradeLogWidget, to: '/trades', defaultSize: { w: 3, h: 4 } },
  regime: { title: 'Regime', Component: RegimeWidget, to: '/regime', defaultSize: { w: 3, h: 4 } },
  earnings: { title: 'Earnings calendar', Component: EarningsWidget, to: '/earnings', defaultSize: { w: 3, h: 4 } },
  backtest: { title: 'Backtest results', Component: BacktestWidget, to: '/backtest', defaultSize: { w: 3, h: 4 } },
  hermes: { title: 'Hermes', Component: HermesControlWidget, to: null, defaultSize: { w: 3, h: 4 } },
  equity: { title: 'Equity', Component: EquityWidget, to: '/equity', defaultSize: { w: 3, h: 4 } },
};

export const DEFAULT_WIDGET_IDS = ['equity', 'positions', 'risk', 'tradelog', 'regime', 'backtest', 'hermes'];

export function defaultLayout() {
  const cols = 12;
  let x = 0;
  let y = 0;
  return DEFAULT_WIDGET_IDS.map((id) => {
    const { w, h } = WIDGET_REGISTRY[id].defaultSize;
    if (x + w > cols) {
      x = 0;
      y += h;
    }
    const item = { i: id, x, y, w, h };
    x += w;
    return item;
  });
}
