// ============================================================
// Tarjay Auto-Checkout — Popup UI Logic
// ============================================================

let state = {
  profiles: [],
  behavior: {
    autoCheckout: true,
    closeOOS: false,
    forceShipping: true,
    autoLogin: true,
  },
  channels: [],
  logs: [],
};

// ---- Storage ----

async function loadState() {
  const data = await chrome.storage.local.get(['tarjayState']);
  if (data.tarjayState) {
    state = { ...state, ...data.tarjayState };
  }
  render();
}

async function saveState() {
  await chrome.storage.local.set({ tarjayState: state });
}

// ---- Render ----

function render() {
  renderProfiles();
  renderBehavior();
  renderChannels();
  renderLogs();
}

function renderProfiles() {
  const list = document.getElementById('profiles-list');
  if (state.profiles.length === 0) {
    list.innerHTML = '<p style="color:#888;font-size:11px;">No profiles yet.</p>';
    return;
  }
  list.innerHTML = state.profiles.map((p, i) => `
    <div class="profile-item">
      <div class="profile-info">
        <label class="toggle-switch">
          <input type="checkbox" ${p.enabled ? 'checked' : ''} data-profile-toggle="${i}">
          <span class="toggle-slider"></span>
        </label>
        <div>
          <div class="profile-name">${escHtml(p.name)}</div>
          <div class="profile-email">${escHtml(p.email)}</div>
        </div>
      </div>
      <button class="btn btn-sm btn-danger" data-delete-profile="${i}">✕</button>
    </div>
  `).join('');

  list.querySelectorAll('[data-profile-toggle]').forEach(el => {
    el.addEventListener('change', (e) => {
      const idx = parseInt(e.target.dataset.profileToggle);
      state.profiles[idx].enabled = e.target.checked;
      saveState();
    });
  });

  list.querySelectorAll('[data-delete-profile]').forEach(el => {
    el.addEventListener('click', (e) => {
      const idx = parseInt(e.target.dataset.deleteProfile);
      state.profiles.splice(idx, 1);
      saveState();
      render();
    });
  });
}

function renderBehavior() {
  document.getElementById('auto-checkout').checked = state.behavior.autoCheckout;
  document.getElementById('close-oos').checked = state.behavior.closeOOS;
  document.getElementById('force-shipping').checked = state.behavior.forceShipping;
  document.getElementById('auto-login').checked = state.behavior.autoLogin;
}

function renderChannels() {
  const list = document.getElementById('channels-list');
  if (state.channels.length === 0) {
    list.innerHTML = '<p style="color:#888;font-size:11px;">No channels monitored.</p>';
    return;
  }
  list.innerHTML = state.channels.map((ch, i) => `
    <div class="channel-item">
      <div class="channel-header">
        <div>
          <span class="channel-name">${escHtml(ch.nickname)}</span>
          <span class="channel-id">${ch.channelId}</span>
        </div>
        <div>
          <button class="btn btn-sm btn-primary" data-add-skus="${i}">+ SKUs</button>
          <button class="btn btn-sm btn-danger" data-delete-channel="${i}">✕</button>
        </div>
      </div>
      <div class="channel-skus">
        ${ch.skus.map((sku, si) => `
          <span class="sku-tag">
            ${escHtml(sku)}
            <span class="sku-remove" data-remove-sku="${i}-${si}">×</span>
          </span>
        `).join('')}
        ${ch.skus.length === 0 ? '<span style="color:#888;font-size:11px;">No SKUs added</span>' : ''}
      </div>
    </div>
  `).join('');

  list.querySelectorAll('[data-delete-channel]').forEach(el => {
    el.addEventListener('click', (e) => {
      const idx = parseInt(e.target.dataset.deleteChannel);
      state.channels.splice(idx, 1);
      saveState();
      render();
    });
  });

  list.querySelectorAll('[data-add-skus]').forEach(el => {
    el.addEventListener('click', (e) => {
      const idx = parseInt(e.target.dataset.addSkus);
      openSkusModal(idx);
    });
  });

  list.querySelectorAll('[data-remove-sku]').forEach(el => {
    el.addEventListener('click', (e) => {
      const [chIdx, skuIdx] = e.target.dataset.removeSku.split('-').map(Number);
      state.channels[chIdx].skus.splice(skuIdx, 1);
      saveState();
      render();
    });
  });
}

function renderLogs() {
  const log = document.getElementById('status-log');
  if (state.logs.length === 0) {
    log.innerHTML = '<div class="log-entry info">Ready — monitoring inactive</div>';
    return;
  }
  log.innerHTML = state.logs.slice(-20).reverse().map(l =>
    `<div class="log-entry ${l.level}">${l.time} — ${escHtml(l.msg)}</div>`
  ).join('');
}

// ---- Event Handlers ----

document.getElementById('add-profile-btn').addEventListener('click', () => {
  document.getElementById('profile-modal').classList.remove('hidden');
});

document.getElementById('cancel-profile-btn').addEventListener('click', () => {
  document.getElementById('profile-modal').classList.add('hidden');
});

document.getElementById('save-profile-btn').addEventListener('click', () => {
  const name = document.getElementById('profile-name').value.trim();
  const email = document.getElementById('profile-email').value.trim();
  const password = document.getElementById('profile-password').value;

  if (!name || !email || !password) return;

  state.profiles.push({ name, email, password, enabled: true });
  saveState();
  render();

  document.getElementById('profile-name').value = '';
  document.getElementById('profile-email').value = '';
  document.getElementById('profile-password').value = '';
  document.getElementById('profile-modal').classList.add('hidden');
});

document.getElementById('add-channel-btn').addEventListener('click', () => {
  const channelId = document.getElementById('channel-id-input').value.trim();
  const nickname = document.getElementById('channel-name-input').value.trim() || channelId;

  if (!channelId) return;

  state.channels.push({ channelId, nickname, skus: [] });
  saveState();
  render();

  document.getElementById('channel-id-input').value = '';
  document.getElementById('channel-name-input').value = '';
});

// Behavior checkboxes
['auto-checkout', 'close-oos', 'force-shipping', 'auto-login'].forEach(id => {
  document.getElementById(id).addEventListener('change', (e) => {
    const key = {
      'auto-checkout': 'autoCheckout',
      'close-oos': 'closeOOS',
      'force-shipping': 'forceShipping',
      'auto-login': 'autoLogin',
    }[id];
    state.behavior[key] = e.target.checked;
    saveState();
  });
});

// SKUs Modal
let currentSkuChannelIdx = null;

function openSkusModal(channelIdx) {
  currentSkuChannelIdx = channelIdx;
  document.getElementById('skus-textarea').value = '';
  document.getElementById('skus-modal').classList.remove('hidden');
}

document.getElementById('cancel-skus-btn').addEventListener('click', () => {
  document.getElementById('skus-modal').classList.add('hidden');
});

document.getElementById('save-skus-btn').addEventListener('click', () => {
  const text = document.getElementById('skus-textarea').value;
  const skus = text.split('\n')
    .map(s => s.trim().replace(/\D/g, ''))
    .filter(s => s.length > 0);

  if (currentSkuChannelIdx !== null && skus.length > 0) {
    const existing = state.channels[currentSkuChannelIdx].skus;
    const unique = skus.filter(s => !existing.includes(s));
    state.channels[currentSkuChannelIdx].skus.push(...unique);
    saveState();
    render();
  }
  document.getElementById('skus-modal').classList.add('hidden');
});

// ---- Utility ----

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ---- Init ----
loadState();

// Listen for log updates from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'log') {
    state.logs.push(msg.data);
    if (state.logs.length > 50) state.logs.shift();
    saveState();
    renderLogs();
  }
});
