// ============================================================
// Tarjay Auto-Checkout — Discord Content Script
// Monitors Discord messages for SKU/TCIN numbers
// ============================================================

(function () {
  'use strict';

  let monitoredSkus = new Set();
  let monitoredChannelId = null;
  let processedMessages = new Set();

  // Extract the current channel ID from the URL
  function getCurrentChannelId() {
    // URL format: https://discord.com/channels/{guildId}/{channelId}
    const match = window.location.pathname.match(/\/channels\/\d+\/(\d+)/);
    return match ? match[1] : null;
  }

  // Load monitored SKUs from storage for the current channel
  async function loadConfig() {
    const channelId = getCurrentChannelId();
    if (!channelId) return;

    monitoredChannelId = channelId;
    const data = await chrome.storage.local.get(['tarjayState']);
    const state = data.tarjayState;
    if (!state) return;

    const channel = state.channels.find(ch => ch.channelId === channelId);
    if (channel) {
      monitoredSkus = new Set(channel.skus);
      console.log(`[Tarjay] Monitoring channel ${channel.nickname} for ${monitoredSkus.size} SKUs`);
    } else {
      monitoredSkus = new Set();
    }
  }

  // Extract potential TCINs/SKUs from message text
  function extractTcins(text) {
    const tcins = new Set();

    // Match A-XXXXXXXX pattern (Target URL format)
    const urlPattern = /A-(\d{7,9})/gi;
    let match;
    while ((match = urlPattern.exec(text)) !== null) {
      tcins.add(match[1]);
    }

    // Match standalone 7-9 digit numbers that could be TCINs
    const numPattern = /\b(\d{7,9})\b/g;
    while ((match = numPattern.exec(text)) !== null) {
      tcins.add(match[1]);
    }

    return tcins;
  }

  // Process a message element for SKU matches
  function processMessage(msgElement) {
    // Generate a unique key for this message to avoid double-processing
    const msgId = msgElement.id || msgElement.dataset.listItemId || msgElement.textContent.slice(0, 50);
    if (processedMessages.has(msgId)) return;
    processedMessages.add(msgId);

    const text = msgElement.textContent || '';
    const foundTcins = extractTcins(text);

    for (const tcin of foundTcins) {
      if (monitoredSkus.has(tcin)) {
        console.log(`[Tarjay] 🎯 SKU MATCH: ${tcin}`);
        chrome.runtime.sendMessage({
          type: 'sku-detected',
          sku: tcin,
          channelId: monitoredChannelId,
        });
      }
    }
  }

  // Observe new messages being added to the chat
  function startObserver() {
    // Discord renders messages in a scrollable container
    const chatSelectors = [
      '[class*="messagesWrapper"]',
      '[data-list-id="chat-messages"]',
      'ol[data-list-id="chat-messages"]',
      '[class*="scrollerInner"]',
    ];

    let chatContainer = null;
    for (const selector of chatSelectors) {
      chatContainer = document.querySelector(selector);
      if (chatContainer) break;
    }

    if (!chatContainer) {
      // Retry after a delay — Discord may not have rendered yet
      setTimeout(startObserver, 2000);
      return;
    }

    console.log('[Tarjay] Discord chat observer started');

    // Process existing messages
    const messages = chatContainer.querySelectorAll('[id^="chat-messages-"]');
    messages.forEach(processMessage);

    // Observe new messages
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          if (node.nodeType === Node.ELEMENT_NODE) {
            // Check if it's a message or contains messages
            if (node.id?.startsWith('chat-messages-')) {
              processMessage(node);
            }
            const childMessages = node.querySelectorAll?.('[id^="chat-messages-"]');
            if (childMessages) {
              childMessages.forEach(processMessage);
            }
          }
        }
      }
    });

    observer.observe(chatContainer, {
      childList: true,
      subtree: true,
    });
  }

  // Watch for URL changes (channel switches in Discord SPA)
  let lastUrl = window.location.href;
  function watchUrlChanges() {
    const currentUrl = window.location.href;
    if (currentUrl !== lastUrl) {
      lastUrl = currentUrl;
      processedMessages.clear();
      loadConfig().then(startObserver);
    }
    setTimeout(watchUrlChanges, 1000);
  }

  // Listen for storage changes (config updates from popup)
  chrome.storage.onChanged.addListener((changes) => {
    if (changes.tarjayState) {
      loadConfig();
    }
  });

  // Initialize
  loadConfig().then(() => {
    startObserver();
    watchUrlChanges();
  });
})();
