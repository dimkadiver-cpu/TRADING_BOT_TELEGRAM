# Dashboard Centrale HTML Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a static, locally-openable multi-page HTML prototype for the central multi-instance dashboard with realistic demo data and full `Fleet -> Istanza -> Trader -> Trade` drill-down.

**Architecture:** The prototype lives entirely under `docs/Raggionamento/DASHBOARD_CENTRALE/` and uses four HTML pages plus shared `styles.css`, `data.js`, and `app.js`. Data is centralized in one demo dataset, then each page renders its own view from query-string routing so the prototype stays coherent, static, and easy to inspect.

**Tech Stack:** Plain HTML, CSS, vanilla JavaScript, local file navigation via query strings

---

## File Structure

**Create:**
- `docs/Raggionamento/DASHBOARD_CENTRALE/index.html`
- `docs/Raggionamento/DASHBOARD_CENTRALE/instance.html`
- `docs/Raggionamento/DASHBOARD_CENTRALE/trader.html`
- `docs/Raggionamento/DASHBOARD_CENTRALE/trade.html`
- `docs/Raggionamento/DASHBOARD_CENTRALE/assets/styles.css`
- `docs/Raggionamento/DASHBOARD_CENTRALE/assets/data.js`
- `docs/Raggionamento/DASHBOARD_CENTRALE/assets/app.js`

**Reference:**
- `docs/Raggionamento/DASHBOARD_CENTRALE/2026-06-30-dashboard-centrale-html-prototype-design.md`

**Responsibilities:**
- `index.html`: fleet overview and entry point
- `instance.html`: instance operational detail
- `trader.html`: trader/source flow detail
- `trade.html`: trade chain detail
- `styles.css`: shared dashboard visual language
- `data.js`: authoritative fake dataset for the prototype
- `app.js`: rendering helpers, query-string routing, page bootstrapping

---

### Task 1: Create Shared Dataset And Rendering Foundation

**Files:**
- Create: `docs/Raggionamento/DASHBOARD_CENTRALE/assets/data.js`
- Create: `docs/Raggionamento/DASHBOARD_CENTRALE/assets/app.js`
- Test: open `docs/Raggionamento/DASHBOARD_CENTRALE/index.html` after later HTML tasks

- [ ] **Step 1: Create the demo dataset file**

Write `docs/Raggionamento/DASHBOARD_CENTRALE/assets/data.js` with a single exported global `window.dashboardData`:

```javascript
window.dashboardData = {
  currentRevision: "main@9c1d2a7",
  fleetSummary: {
    totalInstances: 5,
    liveInstances: 3,
    demoInstances: 2,
    openAlerts: 4,
  },
  instances: [
    {
      id: "alpha_live",
      name: "alpha_live",
      type: "LIVE",
      status: "active",
      server: "vps-main-01",
      revision: "main@9c1d2a7",
      heartbeat: "2026-06-30 09:38:12",
      lastEvent: "Fill BTCUSDT TP1",
      openPositions: 3,
      activeOrders: 8,
      pnl: "+428.15 USDT",
      alertSeverity: "warning",
      traderIds: ["trader_alpha", "trader_delta"],
      rolloutStatus: "canary passed",
      subsystems: {
        telegram: "healthy",
        parser: "healthy",
        lifecycle: "healthy",
        execution: "degraded",
      },
      operations: {
        lastDeploy: "2026-06-30 08:55",
        lastRollout: "2026-06-30 09:05",
        lastFill: "BTCUSDT long TP1",
      },
      alerts: [
        "Execution gateway latency above target",
      ],
      recentTradeIds: ["chain_2031", "chain_2032"],
    },
    {
      id: "beta_demo",
      name: "beta_demo",
      type: "DEMO",
      status: "active",
      server: "vps-demo-01",
      revision: "main@9c1d2a7",
      heartbeat: "2026-06-30 09:38:09",
      lastEvent: "Signal ETHUSDT parsed",
      openPositions: 1,
      activeOrders: 2,
      pnl: "+24.40 USDT",
      alertSeverity: "info",
      traderIds: ["trader_beta"],
      rolloutStatus: "canary active",
      subsystems: {
        telegram: "healthy",
        parser: "healthy",
        lifecycle: "healthy",
        execution: "healthy",
      },
      operations: {
        lastDeploy: "2026-06-30 08:55",
        lastRollout: "2026-06-30 09:00",
        lastFill: "ETHUSDT partial take profit",
      },
      alerts: [],
      recentTradeIds: ["chain_2033"],
    },
    {
      id: "gamma_live",
      name: "gamma_live",
      type: "LIVE",
      status: "error",
      server: "vps-main-02",
      revision: "main@9c1d2a7",
      heartbeat: "2026-06-30 09:31:40",
      lastEvent: "Exchange sync timeout",
      openPositions: 2,
      activeOrders: 5,
      pnl: "-61.80 USDT",
      alertSeverity: "critical",
      traderIds: ["trader_gamma"],
      rolloutStatus: "restart failed",
      subsystems: {
        telegram: "healthy",
        parser: "healthy",
        lifecycle: "degraded",
        execution: "down",
      },
      operations: {
        lastDeploy: "2026-06-29 22:20",
        lastRollout: "2026-06-30 09:07",
        lastFill: "No recent fill",
      },
      alerts: [
        "Execution gateway disconnected",
        "Heartbeat stale",
      ],
      recentTradeIds: ["chain_2034"],
    },
    {
      id: "delta_demo",
      name: "delta_demo",
      type: "DEMO",
      status: "ready",
      server: "vps-demo-02",
      revision: "main@9c1d2a7",
      heartbeat: "never started",
      lastEvent: "Waiting for first start",
      openPositions: 0,
      activeOrders: 0,
      pnl: "n/a",
      alertSeverity: "info",
      traderIds: ["trader_sigma"],
      rolloutStatus: "prepared",
      subsystems: {
        telegram: "unknown",
        parser: "unknown",
        lifecycle: "unknown",
        execution: "unknown",
      },
      operations: {
        lastDeploy: "2026-06-30 07:45",
        lastRollout: "not started",
        lastFill: "n/a",
      },
      alerts: [],
      recentTradeIds: [],
    },
    {
      id: "omega_live",
      name: "omega_live",
      type: "LIVE",
      status: "deployed",
      server: "vps-main-03",
      revision: "main@9c1d2a7",
      heartbeat: "stopped",
      lastEvent: "Deployed, awaiting manual start",
      openPositions: 0,
      activeOrders: 0,
      pnl: "n/a",
      alertSeverity: "warning",
      traderIds: ["trader_omega"],
      rolloutStatus: "awaiting start",
      subsystems: {
        telegram: "stopped",
        parser: "stopped",
        lifecycle: "stopped",
        execution: "stopped",
      },
      operations: {
        lastDeploy: "2026-06-30 09:10",
        lastRollout: "2026-06-30 09:12",
        lastFill: "n/a",
      },
      alerts: [
        "Instance deployed but not active",
      ],
      recentTradeIds: [],
    },
  ],
  traders: [
    {
      id: "trader_alpha",
      instanceId: "alpha_live",
      name: "Trader Alpha",
      profile: "trader_alpha_v2",
      sourceChannel: "@alpha_signals",
      mappingStatus: "active",
      recentSignals: [
        "BTCUSDT long 67250-66900",
        "SOLUSDT add risk trimmed",
      ],
      parseStats: {
        success: 18,
        failed: 1,
      },
      anomalies: [
        "One ambiguous target update in last 24h",
      ],
      tradeIds: ["chain_2031"],
    },
    {
      id: "trader_delta",
      instanceId: "alpha_live",
      name: "Trader Delta",
      profile: "trader_delta_fast",
      sourceChannel: "@delta_scalp",
      mappingStatus: "active",
      recentSignals: [
        "ETHUSDT short scalp",
        "BNBUSDT stop to breakeven",
      ],
      parseStats: {
        success: 31,
        failed: 0,
      },
      anomalies: [],
      tradeIds: ["chain_2032"],
    },
    {
      id: "trader_beta",
      instanceId: "beta_demo",
      name: "Trader Beta",
      profile: "trader_beta_demo",
      sourceChannel: "@beta_futures",
      mappingStatus: "active",
      recentSignals: [
        "ETHUSDT breakout long",
      ],
      parseStats: {
        success: 7,
        failed: 0,
      },
      anomalies: [],
      tradeIds: ["chain_2033"],
    },
    {
      id: "trader_gamma",
      instanceId: "gamma_live",
      name: "Trader Gamma",
      profile: "trader_gamma_swing",
      sourceChannel: "@gamma_macro",
      mappingStatus: "degraded",
      recentSignals: [
        "BTCUSDT weekly swing update",
      ],
      parseStats: {
        success: 12,
        failed: 2,
      },
      anomalies: [
        "Exchange sync stalled after signal execution",
      ],
      tradeIds: ["chain_2034"],
    },
    {
      id: "trader_sigma",
      instanceId: "delta_demo",
      name: "Trader Sigma",
      profile: "trader_sigma_trial",
      sourceChannel: "@sigma_lab",
      mappingStatus: "ready",
      recentSignals: [],
      parseStats: {
        success: 0,
        failed: 0,
      },
      anomalies: [],
      tradeIds: [],
    },
    {
      id: "trader_omega",
      instanceId: "omega_live",
      name: "Trader Omega",
      profile: "trader_omega_breakout",
      sourceChannel: "@omega_alerts",
      mappingStatus: "deployed",
      recentSignals: [],
      parseStats: {
        success: 0,
        failed: 0,
      },
      anomalies: [],
      tradeIds: [],
    },
  ],
  trades: [
    {
      id: "chain_2031",
      instanceId: "alpha_live",
      traderId: "trader_alpha",
      symbol: "BTCUSDT",
      side: "LONG",
      state: "partially_realized",
      account: "BYBIT-LIVE-A1",
      pnl: "+218.40 USDT",
      quantity: "0.042 BTC",
      exposure: "2824 USDT",
      lifecycle: [
        "Signal parsed",
        "Entry placed",
        "Entry filled",
        "TP1 filled",
        "Stop moved to breakeven",
      ],
      orders: [
        "Limit entry #884201",
        "TP1 reduce-only #884265",
        "Stop #884270",
      ],
      fills: [
        "Entry fill 0.042 @ 67185",
        "TP1 fill 0.020 @ 67610",
      ],
      warnings: [],
    },
    {
      id: "chain_2032",
      instanceId: "alpha_live",
      traderId: "trader_delta",
      symbol: "ETHUSDT",
      side: "SHORT",
      state: "open",
      account: "BYBIT-LIVE-A1",
      pnl: "+41.20 USDT",
      quantity: "1.90 ETH",
      exposure: "6650 USDT",
      lifecycle: [
        "Signal parsed",
        "Market entry executed",
        "Stop attached",
      ],
      orders: [
        "Market entry #884401",
        "Stop #884409",
      ],
      fills: [
        "Entry fill 1.90 @ 3498.2",
      ],
      warnings: [],
    },
    {
      id: "chain_2033",
      instanceId: "beta_demo",
      traderId: "trader_beta",
      symbol: "ETHUSDT",
      side: "LONG",
      state: "open",
      account: "BYBIT-DEMO-B1",
      pnl: "+24.40 USDT",
      quantity: "0.80 ETH",
      exposure: "2805 USDT",
      lifecycle: [
        "Signal parsed",
        "Entry placed",
        "Entry filled",
      ],
      orders: [
        "Limit entry #772100",
        "TP ladder #772110",
      ],
      fills: [
        "Entry fill 0.80 @ 3506.4",
      ],
      warnings: [],
    },
    {
      id: "chain_2034",
      instanceId: "gamma_live",
      traderId: "trader_gamma",
      symbol: "SOLUSDT",
      side: "LONG",
      state: "sync_error",
      account: "BYBIT-LIVE-G1",
      pnl: "-61.80 USDT",
      quantity: "72 SOL",
      exposure: "961 USDT",
      lifecycle: [
        "Signal parsed",
        "Entry placed",
        "Entry filled",
        "Exchange sync timeout",
      ],
      orders: [
        "Entry #661041",
        "Stop #661052",
      ],
      fills: [
        "Entry fill 72 @ 13.35",
      ],
      warnings: [
        "Execution gateway disconnected before reconciliation",
      ],
    },
  ],
  alerts: [
    {
      severity: "critical",
      instanceId: "gamma_live",
      message: "Execution gateway disconnected",
      timestamp: "09:31",
    },
    {
      severity: "warning",
      instanceId: "alpha_live",
      message: "Execution latency above target",
      timestamp: "09:25",
    },
    {
      severity: "warning",
      instanceId: "omega_live",
      message: "Instance deployed but not active",
      timestamp: "09:14",
    },
  ],
  rollouts: [
    "09:12 omega_live deployed",
    "09:07 gamma_live restart failed",
    "09:05 alpha_live canary passed",
    "09:00 beta_demo canary running",
  ],
};
```

- [ ] **Step 2: Create the shared rendering helpers**

Write `docs/Raggionamento/DASHBOARD_CENTRALE/assets/app.js`:

```javascript
function getData() {
  return window.dashboardData;
}

function getParam(name) {
  const url = new URL(window.location.href);
  return url.searchParams.get(name);
}

function findInstance(id) {
  return getData().instances.find((item) => item.id === id);
}

function findTrader(id) {
  return getData().traders.find((item) => item.id === id);
}

function findTrade(id) {
  return getData().trades.find((item) => item.id === id);
}

function tradersForInstance(instanceId) {
  return getData().traders.filter((item) => item.instanceId === instanceId);
}

function tradesForInstance(instanceId) {
  return getData().trades.filter((item) => item.instanceId === instanceId);
}

function tradesForTrader(traderId) {
  return getData().trades.filter((item) => item.traderId === traderId);
}

function statusClass(value) {
  return `status status-${String(value).toLowerCase().replace(/_/g, "-")}`;
}

function severityClass(value) {
  return `severity severity-${String(value).toLowerCase()}`;
}

function systemClass(value) {
  return `system system-${String(value).toLowerCase()}`;
}

function renderBadge(label, className) {
  return `<span class="${className}">${label}</span>`;
}

function renderList(items) {
  if (!items || items.length === 0) {
    return "<li class=\"muted\">None</li>";
  }
  return items.map((item) => `<li>${item}</li>`).join("");
}

function pageShell(title, subtitle, body) {
  return `
    <header class="page-header">
      <div>
        <p class="eyebrow">TeleSignalBot Control Plane</p>
        <h1>${title}</h1>
        <p class="subtitle">${subtitle}</p>
      </div>
      <nav class="top-nav">
        <a href="./index.html">Fleet</a>
      </nav>
    </header>
    ${body}
  `;
}

function notFound(label) {
  return `
    <section class="panel">
      <h2>${label} not found</h2>
      <p class="muted">The selected record does not exist in the demo dataset.</p>
      <p><a href="./index.html">Return to fleet view</a></p>
    </section>
  `;
}
```

- [ ] **Step 3: Commit the shared foundation**

```bash
git add docs/Raggionamento/DASHBOARD_CENTRALE/assets/data.js docs/Raggionamento/DASHBOARD_CENTRALE/assets/app.js
git commit -m "feat: add dashboard prototype shared demo dataset"
```

---

### Task 2: Create Shared Styles And Fleet Page

**Files:**
- Create: `docs/Raggionamento/DASHBOARD_CENTRALE/assets/styles.css`
- Create: `docs/Raggionamento/DASHBOARD_CENTRALE/index.html`

- [ ] **Step 1: Create the shared CSS**

Write `docs/Raggionamento/DASHBOARD_CENTRALE/assets/styles.css`:

```css
:root {
  --bg: #f3f6f8;
  --panel: #ffffff;
  --panel-alt: #f8fafb;
  --border: #d8e0e6;
  --text: #13212b;
  --muted: #60707d;
  --accent: #0c6a84;
  --ok: #1f7a4f;
  --warn: #b7791f;
  --crit: #b83232;
  --info: #2b6cb0;
  --shadow: 0 10px 25px rgba(19, 33, 43, 0.08);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: "Segoe UI", Tahoma, sans-serif;
  background: linear-gradient(180deg, #edf3f6 0%, #f7fafb 100%);
  color: var(--text);
}

a {
  color: var(--accent);
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

.app-shell {
  max-width: 1360px;
  margin: 0 auto;
  padding: 24px;
}

.page-header,
.hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.eyebrow {
  margin: 0 0 6px;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
}

h1, h2, h3, h4, p {
  margin-top: 0;
}

.subtitle,
.muted {
  color: var(--muted);
}

.summary-grid,
.metric-grid,
.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
}

.layout-two {
  display: grid;
  grid-template-columns: 2.2fr 1fr;
  gap: 18px;
}

.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: var(--shadow);
  padding: 18px;
}

.panel.alt {
  background: var(--panel-alt);
}

.stat-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
}

.stat-label {
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
}

.toolbar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.filter-button {
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--text);
  border-radius: 999px;
  padding: 8px 12px;
  cursor: pointer;
}

.filter-button.active {
  background: #dff1f6;
  border-color: #a9d2dd;
}

.instance-table {
  width: 100%;
  border-collapse: collapse;
}

.instance-table th,
.instance-table td {
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: top;
  padding: 12px 10px;
}

.instance-table th {
  font-size: 12px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.status,
.severity,
.system {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 600;
}

.status-active,
.system-healthy,
.severity-info {
  background: #e6f4ee;
  color: var(--ok);
}

.status-ready,
.status-deployed,
.system-degraded,
.severity-warning {
  background: #fff4df;
  color: var(--warn);
}

.status-error,
.system-down,
.severity-critical {
  background: #fde8e8;
  color: var(--crit);
}

.status-draft,
.system-unknown,
.system-stopped {
  background: #e7eef4;
  color: var(--info);
}

.stack {
  display: grid;
  gap: 16px;
}

.list-clean {
  margin: 0;
  padding-left: 18px;
}

.breadcrumbs {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
  font-size: 14px;
}

.key-value {
  display: grid;
  gap: 10px;
}

.key-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 8px;
}

.section-title {
  margin-bottom: 12px;
}

@media (max-width: 980px) {
  .layout-two {
    grid-template-columns: 1fr;
  }

  .instance-table,
  .instance-table thead,
  .instance-table tbody,
  .instance-table th,
  .instance-table td,
  .instance-table tr {
    display: block;
  }

  .instance-table thead {
    display: none;
  }

  .instance-table tr {
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--panel);
    margin-bottom: 12px;
    padding: 8px;
  }

  .instance-table td {
    border-bottom: none;
    padding: 8px 10px;
  }
}
```

- [ ] **Step 2: Create the fleet page**

Write `docs/Raggionamento/DASHBOARD_CENTRALE/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Centrale - Fleet</title>
    <link rel="stylesheet" href="./assets/styles.css">
  </head>
  <body>
    <div class="app-shell" id="app"></div>
    <script src="./assets/data.js"></script>
    <script src="./assets/app.js"></script>
    <script>
      const root = document.getElementById("app");
      const data = getData();
      const filter = getParam("filter") || "all";
      const visibleInstances = data.instances.filter((item) => {
        if (filter === "live") return item.type === "LIVE";
        if (filter === "demo") return item.type === "DEMO";
        if (filter === "alerting") return item.alertSeverity !== "info";
        return true;
      });

      root.innerHTML = pageShell(
        "Fleet Overview",
        "Central control-plane snapshot across multi-instance runtime, rollout, and alert state.",
        `
          <section class="summary-grid">
            <div class="stat-card"><div class="stat-label">Instances</div><div class="stat-value">${data.fleetSummary.totalInstances}</div></div>
            <div class="stat-card"><div class="stat-label">Live</div><div class="stat-value">${data.fleetSummary.liveInstances}</div></div>
            <div class="stat-card"><div class="stat-label">Demo</div><div class="stat-value">${data.fleetSummary.demoInstances}</div></div>
            <div class="stat-card"><div class="stat-label">Open Alerts</div><div class="stat-value">${data.fleetSummary.openAlerts}</div></div>
          </section>

          <section class="layout-two" style="margin-top:18px;">
            <div class="panel">
              <div class="toolbar">
                <a class="filter-button ${filter === "all" ? "active" : ""}" href="./index.html?filter=all">All</a>
                <a class="filter-button ${filter === "live" ? "active" : ""}" href="./index.html?filter=live">Live</a>
                <a class="filter-button ${filter === "demo" ? "active" : ""}" href="./index.html?filter=demo">Demo</a>
                <a class="filter-button ${filter === "alerting" ? "active" : ""}" href="./index.html?filter=alerting">Alerting</a>
              </div>
              <table class="instance-table">
                <thead>
                  <tr>
                    <th>Instance</th>
                    <th>Status</th>
                    <th>Server</th>
                    <th>Traders</th>
                    <th>Heartbeat</th>
                    <th>Activity</th>
                    <th>Orders</th>
                    <th>PnL</th>
                    <th>Alert</th>
                  </tr>
                </thead>
                <tbody>
                  ${visibleInstances.map((item) => `
                    <tr>
                      <td>
                        <a href="./instance.html?id=${item.id}"><strong>${item.name}</strong></a><br>
                        <span class="muted">${item.type} · ${item.revision}</span>
                      </td>
                      <td>${renderBadge(item.status, statusClass(item.status))}</td>
                      <td>${item.server}</td>
                      <td>${item.traderIds.join(", ")}</td>
                      <td>${item.heartbeat}</td>
                      <td>${item.lastEvent}</td>
                      <td>${item.openPositions} pos · ${item.activeOrders} ord</td>
                      <td>${item.pnl}</td>
                      <td>${renderBadge(item.alertSeverity, severityClass(item.alertSeverity))}</td>
                    </tr>
                  `).join("")}
                </tbody>
              </table>
            </div>

            <div class="stack">
              <section class="panel alt">
                <h2 class="section-title">Recent Rollout</h2>
                <ul class="list-clean">${renderList(data.rollouts)}</ul>
              </section>
              <section class="panel alt">
                <h2 class="section-title">Recent Alerts</h2>
                <ul class="list-clean">
                  ${data.alerts.map((alert) => `<li><strong>${alert.timestamp}</strong> · ${alert.instanceId} · ${alert.message}</li>`).join("")}
                </ul>
              </section>
              <section class="panel alt">
                <h2 class="section-title">Operational Notes</h2>
                <ul class="list-clean">
                  <li>Canary path is running through <strong>beta_demo</strong>.</li>
                  <li><strong>gamma_live</strong> requires execution-gateway inspection.</li>
                  <li><strong>omega_live</strong> is deployed but not started yet.</li>
                </ul>
              </section>
            </div>
          </section>
        `
      );
    </script>
  </body>
</html>
```

- [ ] **Step 3: Open the fleet page locally**

Run:

```bash
start docs\Raggionamento\DASHBOARD_CENTRALE\index.html
```

Expected:
- the browser opens locally
- the fleet view shows 5 demo instances
- filter links switch between all/live/demo/alerting

- [ ] **Step 4: Commit the fleet view**

```bash
git add docs/Raggionamento/DASHBOARD_CENTRALE/assets/styles.css docs/Raggionamento/DASHBOARD_CENTRALE/index.html
git commit -m "feat: add dashboard prototype fleet view"
```

---

### Task 3: Build Instance Drill-Down Page

**Files:**
- Create: `docs/Raggionamento/DASHBOARD_CENTRALE/instance.html`

- [ ] **Step 1: Create the instance detail page**

Write `docs/Raggionamento/DASHBOARD_CENTRALE/instance.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Centrale - Instance</title>
    <link rel="stylesheet" href="./assets/styles.css">
  </head>
  <body>
    <div class="app-shell" id="app"></div>
    <script src="./assets/data.js"></script>
    <script src="./assets/app.js"></script>
    <script>
      const root = document.getElementById("app");
      const instance = findInstance(getParam("id"));

      if (!instance) {
        root.innerHTML = notFound("Instance");
      } else {
        const relatedTraders = tradersForInstance(instance.id);
        const relatedTrades = tradesForInstance(instance.id);

        root.innerHTML = pageShell(
          instance.name,
          `${instance.type} instance on ${instance.server} · revision ${instance.revision}`,
          `
            <div class="breadcrumbs">
              <a href="./index.html">Fleet</a>
              <span>/</span>
              <span>${instance.name}</span>
            </div>

            <section class="metric-grid">
              <div class="stat-card"><div class="stat-label">Status</div><div>${renderBadge(instance.status, statusClass(instance.status))}</div></div>
              <div class="stat-card"><div class="stat-label">Heartbeat</div><div>${instance.heartbeat}</div></div>
              <div class="stat-card"><div class="stat-label">Open Positions</div><div class="stat-value">${instance.openPositions}</div></div>
              <div class="stat-card"><div class="stat-label">Active Orders</div><div class="stat-value">${instance.activeOrders}</div></div>
            </section>

            <section class="layout-two" style="margin-top:18px;">
              <div class="stack">
                <section class="panel">
                  <h2 class="section-title">Subsystem Health</h2>
                  <div class="detail-grid">
                    <div>${renderBadge("telegram", systemClass(instance.subsystems.telegram))} <span class="muted">${instance.subsystems.telegram}</span></div>
                    <div>${renderBadge("parser", systemClass(instance.subsystems.parser))} <span class="muted">${instance.subsystems.parser}</span></div>
                    <div>${renderBadge("lifecycle", systemClass(instance.subsystems.lifecycle))} <span class="muted">${instance.subsystems.lifecycle}</span></div>
                    <div>${renderBadge("execution", systemClass(instance.subsystems.execution))} <span class="muted">${instance.subsystems.execution}</span></div>
                  </div>
                </section>

                <section class="panel">
                  <h2 class="section-title">Associated Traders</h2>
                  <ul class="list-clean">
                    ${relatedTraders.map((trader) => `<li><a href="./trader.html?id=${trader.id}"><strong>${trader.name}</strong></a> · ${trader.sourceChannel}</li>`).join("")}
                  </ul>
                </section>

                <section class="panel">
                  <h2 class="section-title">Recent Trades</h2>
                  <ul class="list-clean">
                    ${relatedTrades.map((trade) => `<li><a href="./trade.html?id=${trade.id}"><strong>${trade.symbol}</strong></a> · ${trade.state} · ${trade.pnl}</li>`).join("") || "<li class=\"muted\">No trades</li>"}
                  </ul>
                </section>
              </div>

              <div class="stack">
                <section class="panel alt">
                  <h2 class="section-title">Operational Summary</h2>
                  <div class="key-value">
                    <div class="key-row"><span>Last event</span><strong>${instance.lastEvent}</strong></div>
                    <div class="key-row"><span>Last deploy</span><strong>${instance.operations.lastDeploy}</strong></div>
                    <div class="key-row"><span>Last rollout</span><strong>${instance.operations.lastRollout}</strong></div>
                    <div class="key-row"><span>Last fill</span><strong>${instance.operations.lastFill}</strong></div>
                    <div class="key-row"><span>PnL</span><strong>${instance.pnl}</strong></div>
                  </div>
                </section>

                <section class="panel alt">
                  <h2 class="section-title">Open Alerts</h2>
                  <ul class="list-clean">${renderList(instance.alerts)}</ul>
                </section>
              </div>
            </section>
          `
        );
      }
    </script>
  </body>
</html>
```

- [ ] **Step 2: Open an instance page**

Run:

```bash
start docs\Raggionamento\DASHBOARD_CENTRALE\instance.html?id=alpha_live
```

Expected:
- the page opens for `alpha_live`
- associated traders and recent trades are clickable
- subsystem badges reflect healthy/degraded/down states

- [ ] **Step 3: Commit the instance drill-down**

```bash
git add docs/Raggionamento/DASHBOARD_CENTRALE/instance.html
git commit -m "feat: add dashboard prototype instance detail page"
```

---

### Task 4: Build Trader And Trade Drill-Down Pages

**Files:**
- Create: `docs/Raggionamento/DASHBOARD_CENTRALE/trader.html`
- Create: `docs/Raggionamento/DASHBOARD_CENTRALE/trade.html`

- [ ] **Step 1: Create the trader detail page**

Write `docs/Raggionamento/DASHBOARD_CENTRALE/trader.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Centrale - Trader</title>
    <link rel="stylesheet" href="./assets/styles.css">
  </head>
  <body>
    <div class="app-shell" id="app"></div>
    <script src="./assets/data.js"></script>
    <script src="./assets/app.js"></script>
    <script>
      const root = document.getElementById("app");
      const trader = findTrader(getParam("id"));

      if (!trader) {
        root.innerHTML = notFound("Trader");
      } else {
        const instance = findInstance(trader.instanceId);
        const relatedTrades = tradesForTrader(trader.id);

        root.innerHTML = pageShell(
          trader.name,
          `${trader.profile} · ${trader.sourceChannel}`,
          `
            <div class="breadcrumbs">
              <a href="./index.html">Fleet</a>
              <span>/</span>
              <a href="./instance.html?id=${instance.id}">${instance.name}</a>
              <span>/</span>
              <span>${trader.name}</span>
            </div>

            <section class="layout-two">
              <div class="stack">
                <section class="panel">
                  <h2 class="section-title">Trader Snapshot</h2>
                  <div class="key-value">
                    <div class="key-row"><span>Mapping status</span><strong>${trader.mappingStatus}</strong></div>
                    <div class="key-row"><span>Source channel</span><strong>${trader.sourceChannel}</strong></div>
                    <div class="key-row"><span>Parse success</span><strong>${trader.parseStats.success}</strong></div>
                    <div class="key-row"><span>Parse failed</span><strong>${trader.parseStats.failed}</strong></div>
                  </div>
                </section>

                <section class="panel">
                  <h2 class="section-title">Recent Signals</h2>
                  <ul class="list-clean">${renderList(trader.recentSignals)}</ul>
                </section>

                <section class="panel">
                  <h2 class="section-title">Recent Trade Chains</h2>
                  <ul class="list-clean">
                    ${relatedTrades.map((trade) => `<li><a href="./trade.html?id=${trade.id}">${trade.id}</a> · ${trade.symbol} · ${trade.state}</li>`).join("") || "<li class=\"muted\">No trades</li>"}
                  </ul>
                </section>
              </div>

              <div class="stack">
                <section class="panel alt">
                  <h2 class="section-title">Anomalies</h2>
                  <ul class="list-clean">${renderList(trader.anomalies)}</ul>
                </section>
                <section class="panel alt">
                  <h2 class="section-title">Parent Instance</h2>
                  <p><a href="./instance.html?id=${instance.id}"><strong>${instance.name}</strong></a></p>
                  <p class="muted">${instance.server} · ${instance.revision}</p>
                </section>
              </div>
            </section>
          `
        );
      }
    </script>
  </body>
</html>
```

- [ ] **Step 2: Create the trade detail page**

Write `docs/Raggionamento/DASHBOARD_CENTRALE/trade.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Centrale - Trade</title>
    <link rel="stylesheet" href="./assets/styles.css">
  </head>
  <body>
    <div class="app-shell" id="app"></div>
    <script src="./assets/data.js"></script>
    <script src="./assets/app.js"></script>
    <script>
      const root = document.getElementById("app");
      const trade = findTrade(getParam("id"));

      if (!trade) {
        root.innerHTML = notFound("Trade");
      } else {
        const instance = findInstance(trade.instanceId);
        const trader = findTrader(trade.traderId);

        root.innerHTML = pageShell(
          trade.id,
          `${trade.symbol} ${trade.side} · ${trade.state}`,
          `
            <div class="breadcrumbs">
              <a href="./index.html">Fleet</a>
              <span>/</span>
              <a href="./instance.html?id=${instance.id}">${instance.name}</a>
              <span>/</span>
              <a href="./trader.html?id=${trader.id}">${trader.name}</a>
              <span>/</span>
              <span>${trade.id}</span>
            </div>

            <section class="metric-grid">
              <div class="stat-card"><div class="stat-label">PnL</div><div class="stat-value">${trade.pnl}</div></div>
              <div class="stat-card"><div class="stat-label">Quantity</div><div>${trade.quantity}</div></div>
              <div class="stat-card"><div class="stat-label">Exposure</div><div>${trade.exposure}</div></div>
              <div class="stat-card"><div class="stat-label">Account</div><div>${trade.account}</div></div>
            </section>

            <section class="layout-two" style="margin-top:18px;">
              <div class="stack">
                <section class="panel">
                  <h2 class="section-title">Lifecycle Timeline</h2>
                  <ul class="list-clean">${renderList(trade.lifecycle)}</ul>
                </section>
                <section class="panel">
                  <h2 class="section-title">Orders</h2>
                  <ul class="list-clean">${renderList(trade.orders)}</ul>
                </section>
                <section class="panel">
                  <h2 class="section-title">Fills</h2>
                  <ul class="list-clean">${renderList(trade.fills)}</ul>
                </section>
              </div>

              <div class="stack">
                <section class="panel alt">
                  <h2 class="section-title">Linked Context</h2>
                  <div class="key-value">
                    <div class="key-row"><span>Instance</span><strong><a href="./instance.html?id=${instance.id}">${instance.name}</a></strong></div>
                    <div class="key-row"><span>Trader</span><strong><a href="./trader.html?id=${trader.id}">${trader.name}</a></strong></div>
                    <div class="key-row"><span>State</span><strong>${trade.state}</strong></div>
                  </div>
                </section>
                <section class="panel alt">
                  <h2 class="section-title">Warnings</h2>
                  <ul class="list-clean">${renderList(trade.warnings)}</ul>
                </section>
              </div>
            </section>
          `
        );
      }
    </script>
  </body>
</html>
```

- [ ] **Step 3: Open trader and trade pages**

Run:

```bash
start docs\Raggionamento\DASHBOARD_CENTRALE\trader.html?id=trader_alpha
start docs\Raggionamento\DASHBOARD_CENTRALE\trade.html?id=chain_2031
```

Expected:
- trader page shows recent signals, parse stats, anomalies, and related trades
- trade page shows lifecycle timeline, orders, fills, and linked instance/trader context

- [ ] **Step 4: Commit trader and trade pages**

```bash
git add docs/Raggionamento/DASHBOARD_CENTRALE/trader.html docs/Raggionamento/DASHBOARD_CENTRALE/trade.html
git commit -m "feat: add dashboard prototype trader and trade pages"
```

---

### Task 5: Final Validation And Spec Alignment

**Files:**
- Modify if needed: any of the prototype files above
- Reference: `docs/Raggionamento/DASHBOARD_CENTRALE/2026-06-30-dashboard-centrale-html-prototype-design.md`

- [ ] **Step 1: Walk the full drill-down path manually**

Run:

```bash
start docs\Raggionamento\DASHBOARD_CENTRALE\index.html
```

Manual checks:
- open `alpha_live` from the fleet page
- open `Trader Alpha` from the instance page
- open `chain_2031` from the trader page
- use breadcrumb links to navigate back

Expected:
- all four pages open without server tooling
- drill-down is coherent
- IDs and linked records match the shared dataset

- [ ] **Step 2: Verify the prototype against the spec**

Check these concrete items against `docs/Raggionamento/DASHBOARD_CENTRALE/2026-06-30-dashboard-centrale-html-prototype-design.md`:

- fleet page shows overview, instances, rollout, alerts, notes
- instance page shows identity, subsystem health, operational summary, trading summary, traders, alerts, recent trades
- trader page shows source, parse stats, anomalies, recent trade chains
- trade page shows chain identity, lifecycle, orders, fills, linked context
- navigation follows `Fleet -> Istanza -> Trader -> Trade`

Expected:
- no section from the spec is visibly missing

- [ ] **Step 3: Fix any gaps with minimal edits**

If a page is missing a required section, patch only the relevant file. Use this edit pattern:

```html
<section class="panel">
  <h2 class="section-title">Recent Trades</h2>
  <ul class="list-clean">
    ${relatedTrades.map((trade) => `<li><a href="./trade.html?id=${trade.id}">${trade.id}</a></li>`).join("")}
  </ul>
</section>
```

Expected:
- any spec drift is corrected without restructuring the whole prototype

- [ ] **Step 4: Commit the validated prototype**

```bash
git add docs/Raggionamento/DASHBOARD_CENTRALE/index.html docs/Raggionamento/DASHBOARD_CENTRALE/instance.html docs/Raggionamento/DASHBOARD_CENTRALE/trader.html docs/Raggionamento/DASHBOARD_CENTRALE/trade.html docs/Raggionamento/DASHBOARD_CENTRALE/assets/styles.css docs/Raggionamento/DASHBOARD_CENTRALE/assets/app.js docs/Raggionamento/DASHBOARD_CENTRALE/assets/data.js
git commit -m "feat: add central dashboard html prototype"
```

---

## Self-Review

### Spec coverage

- file structure: covered by Tasks 1-4
- fleet page: covered by Task 2
- instance page: covered by Task 3
- trader and trade pages: covered by Task 4
- static local navigation and validation: covered by Task 5

### Placeholder scan

- no `TODO`, `TBD`, or deferred implementation notes remain in the task steps
- every code-edit step contains exact file content or an exact patch pattern
- every validation step contains a command and expected outcome

### Type consistency

- instance IDs are reused consistently: `alpha_live`, `beta_demo`, `gamma_live`, `delta_demo`, `omega_live`
- trader IDs are reused consistently: `trader_alpha`, `trader_delta`, `trader_beta`, `trader_gamma`, `trader_sigma`, `trader_omega`
- trade IDs are reused consistently: `chain_2031`, `chain_2032`, `chain_2033`, `chain_2034`

