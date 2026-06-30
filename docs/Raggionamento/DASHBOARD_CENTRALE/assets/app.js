function getData() {
  const source =
    typeof window !== "undefined" && window.dashboardData
      ? window.dashboardData
      : {};

  return {
    ...source,
    instances: Array.isArray(source.instances) ? source.instances : [],
    traders: Array.isArray(source.traders) ? source.traders : [],
    trades: Array.isArray(source.trades) ? source.trades : [],
    alerts: Array.isArray(source.alerts) ? source.alerts : [],
    rollouts: Array.isArray(source.rollouts) ? source.rollouts : [],
  };
}

function getParam(name) {
  if (typeof window === "undefined" || !window.location?.href) {
    return null;
  }

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

function uniqueStrings(values) {
  const seen = new Set();
  const result = [];

  for (const value of Array.isArray(values) ? values : []) {
    if (typeof value !== "string") {
      continue;
    }

    const normalized = value.trim();
    if (!normalized || seen.has(normalized)) {
      continue;
    }

    seen.add(normalized);
    result.push(normalized);
  }

  return result;
}

function allAlertEntries() {
  return getData().alerts
    .map((item) => ({
      severity:
        typeof item?.severity === "string" && item.severity.trim()
          ? item.severity.trim().toLowerCase()
          : "warning",
      instanceId:
        typeof item?.instanceId === "string" && item.instanceId.trim()
          ? item.instanceId.trim()
          : "unassigned",
      message:
        typeof item?.message === "string"
          ? item.message.trim()
          : "",
      timestamp:
        typeof item?.timestamp === "string" && item.timestamp.trim()
          ? item.timestamp.trim()
          : "Unknown time",
    }))
    .filter((item) => item.message);
}

function alertEntriesForInstance(instanceId) {
  return allAlertEntries().filter((item) => item.instanceId === instanceId);
}

function messagesForInstance(instanceId) {
  return alertEntriesForInstance(instanceId).map((item) => item.message);
}

function highestSeverityForInstance(instanceId) {
  const rank = { info: 1, warning: 2, critical: 3 };
  let highest = "info";

  for (const entry of alertEntriesForInstance(instanceId)) {
    const severity = rank[entry.severity] ? entry.severity : "warning";
    if (rank[severity] > rank[highest]) {
      highest = severity;
    }
  }

  return highest;
}

function findInstanceForTrader(traderId) {
  const trader = findTrader(traderId);
  if (!trader || typeof trader.instanceId !== "string") {
    return null;
  }

  return findInstance(trader.instanceId.trim());
}

function findInstanceForTrade(tradeId) {
  const trade = findTrade(tradeId);
  if (!trade || typeof trade.traderId !== "string") {
    return null;
  }

  return findInstanceForTrader(trade.traderId.trim());
}

function tradersForInstance(instanceId) {
  return getData().traders.filter((item) => item?.instanceId === instanceId);
}

function tradesForInstance(instanceId) {
  const traderIds = new Set(tradersForInstance(instanceId).map((item) => item.id));
  return getData().trades.filter((item) => traderIds.has(item?.traderId));
}

function tradesForTrader(traderId) {
  return getData().trades.filter((item) => item?.traderId === traderId);
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

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function sanitizeClassName(value) {
  return String(value)
    .replace(/[^a-zA-Z0-9 _-]/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function renderBadge(label, className) {
  return `<span class="${sanitizeClassName(className)}">${escapeHtml(label)}</span>`;
}

function renderList(items) {
  if (!items || items.length === 0) {
    return '<li class="muted">None</li>';
  }
  return items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function pageShell(title, subtitle, body) {
  return `
    <header class="page-header">
      <div>
        <p class="eyebrow">TeleSignalBot Control Plane</p>
        <h1>${escapeHtml(title)}</h1>
        <p class="subtitle">${escapeHtml(subtitle)}</p>
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
      <h2>${escapeHtml(label)} not found</h2>
      <p class="muted">The selected record does not exist in the demo dataset.</p>
      <p><a href="./index.html">Return to fleet view</a></p>
    </section>
  `;
}
