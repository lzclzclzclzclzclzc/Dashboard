const fs = require("fs");
const config = require("../lib/config");
const { sendJson, localDate, roundMoney, maskKey } = require("../lib/util");

function readBaseline() {
  if (!fs.existsSync(config.BASELINE_FILE)) return { date: null, baseline_at: null, balances: {} };
  try {
    return JSON.parse(fs.readFileSync(config.BASELINE_FILE, "utf8"));
  } catch {
    return { date: null, baseline_at: null, balances: {} };
  }
}

function writeBaseline(baseline) {
  fs.mkdirSync(config.DATA_DIR, { recursive: true });
  fs.writeFileSync(config.BASELINE_FILE, JSON.stringify(baseline, null, 2));
}

function balanceToMap(infos) {
  const map = {};
  for (const item of infos) {
    map[item.currency] = Number(item.total_balance || 0);
  }
  return map;
}

function getDeepSeekDaily(balance) {
  if (!balance.ok || !balance.data) {
    return { date: localDate(), baseline_at: null, items: [] };
  }
  const today = localDate();
  const current = balanceToMap(balance.data.balance_infos || []);
  let baseline = readBaseline();
  if (baseline.date !== today) {
    baseline = { date: today, baseline_at: new Date().toISOString(), balances: current };
    writeBaseline(baseline);
  }
  const currencies = Array.from(new Set([...Object.keys(baseline.balances || {}), ...Object.keys(current)]));
  return {
    date: baseline.date,
    baseline_at: baseline.baseline_at,
    items: currencies.map((currency) => {
      const initial = Number(baseline.balances?.[currency] || 0);
      const now = Number(current[currency] || 0);
      return { currency, initial: roundMoney(initial), current: roundMoney(now), used: roundMoney(initial - now) };
    }),
  };
}

async function getDeepSeekBalance() {
  if (!config.DEEPSEEK_API_KEY) {
    return { ok: false, status: 0, message: "DEEPSEEK_API_KEY is not configured.", data: null };
  }
  try {
    const response = await fetch("https://api.deepseek.com/user/balance", {
      headers: { Accept: "application/json", Authorization: `Bearer ${config.DEEPSEEK_API_KEY}` },
    });
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
    return { ok: response.ok, status: response.status, message: response.ok ? "ok" : `DeepSeek returned ${response.status}`, data };
  } catch (error) {
    return { ok: false, status: 0, message: error.message, data: null };
  }
}

async function getDeepSeekSnapshot() {
  const balance = await getDeepSeekBalance();
  return {
    at: new Date().toISOString(),
    key_mask: maskKey(config.DEEPSEEK_API_KEY),
    balance,
    daily: getDeepSeekDaily(balance),
  };
}

async function handler(req, res) {
  sendJson(res, 200, await getDeepSeekSnapshot());
}

module.exports = { handler, getDeepSeekSnapshot };
