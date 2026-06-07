// ============================================================
// Tarjay Auto-Checkout — Background Service Worker
// Handles messages from content scripts and orchestrates checkout
// ============================================================

// Listen for SKU detections from Discord content script
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'sku-detected') {
    handleSkuDetected(msg.sku, msg.channelId);
  }
  if (msg.type === 'checkout-complete') {
    addLog('success', `Checkout complete: ${msg.sku}`);
  }
  if (msg.type === 'checkout-failed') {
    addLog('error', `Checkout failed: ${msg.sku} — ${msg.reason}`);
  }
  if (msg.type === 'oos-detected') {
    handleOOS(msg.sku, sender.tab?.id);
  }
});

async function handleSkuDetected(sku, channelId) {
  const data = await chrome.storage.local.get(['tarjayState']);
  const state = data.tarjayState;
  if (!state) return;

  // Check if auto-checkout is enabled
  if (!state.behavior.autoCheckout) return;

  // Check if any enabled profile exists
  const activeProfile = state.profiles.find(p => p.enabled);
  if (!activeProfile) {
    addLog('error', `SKU ${sku} detected but no active profile`);
    return;
  }

  // Verify this SKU is in our monitored list for this channel
  const channel = state.channels.find(ch => ch.channelId === channelId);
  if (!channel || !channel.skus.includes(sku)) return;

  addLog('info', `SKU ${sku} detected in #${channel.nickname} — opening Target`);

  // Build Target product URL from TCIN
  const targetUrl = `https://www.target.com/p/-/A-${sku}`;

  // Open new tab with the Target product page
  const tab = await chrome.tabs.create({ url: targetUrl, active: false });

  // Store checkout intent for the content script to pick up
  await chrome.storage.local.set({
    [`checkout_${tab.id}`]: {
      sku,
      profile: activeProfile,
      behavior: state.behavior,
      timestamp: Date.now(),
    }
  });
}

async function handleOOS(sku, tabId) {
  const data = await chrome.storage.local.get(['tarjayState']);
  const state = data.tarjayState;
  if (!state) return;

  addLog('error', `SKU ${sku} — Out of Stock`);

  if (state.behavior.closeOOS && tabId) {
    chrome.tabs.remove(tabId);
    addLog('info', `Closed OOS tab for ${sku}`);
  }
}

function addLog(level, msg) {
  const time = new Date().toLocaleTimeString();
  const logEntry = { level, msg, time };

  // Send to popup if open
  chrome.runtime.sendMessage({ type: 'log', data: logEntry }).catch(() => {});

  // Also persist
  chrome.storage.local.get(['tarjayState'], (data) => {
    if (data.tarjayState) {
      data.tarjayState.logs = data.tarjayState.logs || [];
      data.tarjayState.logs.push(logEntry);
      if (data.tarjayState.logs.length > 50) data.tarjayState.logs.shift();
      chrome.storage.local.set({ tarjayState: data.tarjayState });
    }
  });
}
