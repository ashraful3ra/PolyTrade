// PolyTrade/static/js/dashboard.js

(() => {
  if (window.__dashboardInitLoaded) return;
  window.__dashboardInitLoaded = true;

  let symbolsCache = [];
  let selectedCoins = new Map();
  let runningTrades = new Map();
  let pollingInterval = null; // New polling mechanism
  let tsInstance = null; 

  function el(tag, attrs = {}) {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    return e;
  }

  function showError(msg, selector = '#trade-setup-error') {
    const errorDiv = document.querySelector(selector);
    if (!errorDiv) return;
    errorDiv.textContent = msg;
    errorDiv.style.display = 'block';
    setTimeout(() => (errorDiv.style.display = 'none'), 5000);
  }

  function addCoin(symbol) {
    if (selectedCoins.has(symbol)) return;
    const coinData = { symbol, leverage: 10, margin: 100, price: 'Loading...' };
    selectedCoins.set(symbol, coinData);
    renderCoinSettings();
    fetch(`/api/price?symbol=${symbol}`)
      .then((r) => r.json())
      .then((d) => {
        if (d && d.price) {
          coinData.price = parseFloat(d.price);
          renderCoinSettings();
        }
      })
      .catch(() => {});
  }

  function removeCoin(symbol) {
    selectedCoins.delete(symbol);
    if (tsInstance) {
      try { tsInstance.removeItem(symbol, true); } catch {}
    }
    renderCoinSettings();
  }

  function updateTotalCost() {
      let totalCost = 0;
      selectedCoins.forEach((c) => {
        if (c.leverage > 0) totalCost += c.margin / c.leverage;
      });
      const totalEl = document.getElementById('total_est_cost');
      if (totalEl) totalEl.textContent = `${totalCost.toFixed(2)} USDT`;
  }

  function renderCoinSettings() {
    const container = document.getElementById('coin_settings_container');
    if (!container) return;
    container.innerHTML = '';

    if (selectedCoins.size === 0) {
      container.innerHTML = '<p class="small" style="text-align: center;">No coins selected yet.</p>';
    } else {
      selectedCoins.forEach((coin) => {
        const estCost = coin.leverage > 0 ? (coin.margin / coin.leverage).toFixed(2) : '0.00';
        const card = el('div', { class: 'bot-card' });
        card.innerHTML = `
          <div class="bot-head">
              <span class="bot-title" style="font-size: 1em;">${coin.symbol}</span>
              <button class="btn-tiny-danger" data-symbol="${coin.symbol}">&times;</button>
          </div>
          <div class="small" style="margin-bottom: 12px;">Price: <b style="color: var(--text);">${typeof coin.price === 'number' ? coin.price.toFixed(5) : coin.price}</b></div>
          
          <div class="row" style="gap: 8px;">
            <div style="flex:1;">
                <label class="small">Leverage</label>
                <input type="number" class="input input-small coin-setting-input" value="${coin.leverage}" data-symbol="${coin.symbol}" data-key="leverage" min="1" max="150">
            </div>
            <div style="flex:1;">
                <label class="small">Margin (USDT)</label>
                <input type="number" class="input input-small coin-setting-input" value="${coin.margin}" data-symbol="${coin.symbol}" data-key="margin">
            </div>
          </div>

          <div class="small mt">Est. Cost: <b id="est-cost-${coin.symbol}" style="color: var(--primary);">${estCost} USDT</b></div>`;
        container.appendChild(card);
      });
    }

    updateTotalCost();
    const countEl = document.getElementById('coin_count_display');
    if (countEl) countEl.textContent = selectedCoins.size;
  }
  
  // --- NEW POLLING LOGIC ---
  async function fetchAndUpdateTrades(accountId) {
    const s = document.getElementById('ws_status');
    const container = document.getElementById('running_trades_container');
    
    if (s) { s.className = 'status-ok'; s.textContent = 'Live (Polling)'; } // Status indicator set to live during fetch

    try {
        const r = await fetch(`/api/trades/fetch_roi/${accountId}`);
        const data = await r.json();

        if (r.ok && data.ok) {
            runningTrades.clear();
            if (data.trades) {
                data.trades.forEach((trade) => {
                    // Trades now include ROI and mark_price directly from the backend
                    runningTrades.set(trade.symbol, trade);
                });
            }
            renderRunningTrades();
        } else {
            showError(data.error || 'Failed to fetch trades via API.', '#running-trade-error');
            if (container) {
                container.innerHTML = '<p class="small" style="text-align: center; color: var(--danger);">API Polling Failed. Select Account.</p>';
            }
        }
    } catch (e) {
        showError('Network Error during API Polling: ' + e.message, '#running-trade-error');
        if (s) { s.className = 'status-warn'; s.textContent = 'Offline'; }
    }
  }

  function startPolling(accountId) {
    stopPolling();
    // Fetch once immediately
    fetchAndUpdateTrades(accountId);
    // Start polling every 1 second (1000ms) - for faster updates
    pollingInterval = setInterval(() => fetchAndUpdateTrades(accountId), 1000); 
  }

  function stopPolling() {
    if (pollingInterval) {
      clearInterval(pollingInterval);
      pollingInterval = null;
    }
    const s = document.getElementById('ws_status');
    if (s) { s.className = 'status-off'; s.textContent = 'Stopped'; }
    runningTrades.clear();
    renderRunningTrades();
  }


  function getTradeRowHTML(trade) {
    const roiClass = trade.roi >= 0 ? 'roi-pos' : 'roi-neg';
    return `
      <tr id="trade-row-${trade.symbol}">
        <td><input type="checkbox" class="trade-checkbox" data-symbol="${trade.symbol}" data-side="${trade.side}"></td>
        <td><b>${trade.symbol}</b></td>
        <td>${trade.entry_price.toFixed(4)}</td>
        <td><b class="${roiClass}" id="roi-${trade.symbol}">${trade.roi.toFixed(2)}%</b></td>
        <td>${trade.side}</td>
        <td><button class="btn-tiny-danger close-single-trade" data-symbol="${trade.symbol}" data-side="${trade.side}">&times; Close</button></td>
      </tr>`;
  }

  function renderRunningTrades() {
    const container = document.getElementById('running_trades_container');
    if (!container) return;

    const roiFilterEl = document.getElementById('roi_search_input');
    const roiFilter = roiFilterEl ? parseFloat(roiFilterEl.value) : null;
    let matchCount = 0;

    const tradesToRender = Array.from(runningTrades.values()).filter((trade) => {
      const passes = (roiFilter === null || Number.isNaN(roiFilter)) ? true : trade.roi >= roiFilter;
      if (passes) matchCount++;
      return passes;
    });

    const matchEl = document.getElementById('roi_match_count');
    if (matchEl) matchEl.textContent = matchCount;

    if (tradesToRender.length > 0) {
      container.innerHTML = `
        <table class="trade-table">
          <thead>
            <tr>
              <th><input type="checkbox" id="select_all_trades"></th>
              <th>Coin</th><th>Entry Price</th><th>ROI%</th><th>Side</th><th>Action</th>
            </tr>
          </thead>
          <tbody>${tradesToRender.map(getTradeRowHTML).join('')}</tbody>
        </table>`;
      const selectAll = document.getElementById('select_all_trades');
      if (selectAll) {
        selectAll.addEventListener('change', (e) => {
          document.querySelectorAll('.trade-checkbox').forEach((cb) => (cb.checked = e.target.checked));
        });
      }
      container.addEventListener('click', (e) => {
        if (e.target.classList.contains('close-single-trade')) {
          const { symbol, side } = e.target.dataset;
          if (confirm(`Close ${symbol} ${side} trade?`)) closeTrades([{ symbol, side }]);
        }
      });
    } else {
      container.innerHTML = '<p class="small" style="text-align: center;">No running trades found for the selected criteria.</p>';
    }
  }

  function updateTradeRow(trade) {
    const roiEl = document.getElementById(`roi-${trade.symbol}`);
    if (roiEl) {
      roiEl.textContent = `${trade.roi.toFixed(2)}%`
      roiEl.className = trade.roi >= 0 ? 'roi-pos' : 'roi-neg';
    }
  }

  async function closeTrades(tradesToClose) {
    const accountSelect = document.getElementById('bot_account');
    const account_id = accountSelect ? accountSelect.value : null;
    if (!account_id || tradesToClose.length === 0) return;
    const r = await fetch('/api/trades/close', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id, trades: tradesToClose }),
    });
    const res = await r.json().catch(() => ({}));
    alert(res.message || 'Request sent.');
    // Trigger an immediate fetch after action
    fetchAndUpdateTrades(account_id);
  }

  function closeSelectedTrades() {
    const accountId = document.getElementById('bot_account')?.value;
    if (!accountId) return;
    const trades = Array.from(document.querySelectorAll('.trade-checkbox:checked'))
      .map((cb) => ({ symbol: cb.dataset.symbol, side: cb.dataset.side }));
    if (trades.length > 0 && confirm(`Close ${trades.length} selected trades?`)) closeTrades(trades);
  }

  function closeAllTrades() {
    const accountId = document.getElementById('bot_account')?.value;
    if (!accountId) return;
    const trades = Array.from(document.querySelectorAll('.trade-checkbox'))
      .map((cb) => ({ symbol: cb.dataset.symbol, side: cb.dataset.side }));
    if (trades.length > 0 && confirm(`Close all ${trades.length} listed trades?`)) closeTrades(trades);
  }

  async function saveTemplate() {
    const nameEl = document.getElementById('bot_name');
    const name = nameEl ? nameEl.value.trim() : '';
    if (!name || selectedCoins.size === 0) return showError('Template Name and at least one Coin are required.');
    const settings = {
      bot_name: name,
      side: document.getElementById('trade_side')?.value,
      margin_mode: document.getElementById('margin_mode')?.value,
      coins: Array.from(selectedCoins.values()),
    };
    await fetch('/api/templates/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, settings }),
    }).catch(e => {
        console.error("Error saving template:", e);
        showError("Failed to save template.");
    });
    loadTemplates();
  }

  async function loadTemplates() {
    const r = await fetch('/api/templates/list').catch(e => {
        console.error("Error loading templates:", e);
        showError("Failed to load templates.");
        return null;
    });
    const data = r ? await r.json().catch(() => ({})) : {};
    const container = document.getElementById('tpl_list');
    if (!container) return;
    container.innerHTML = '';
    if (data.items) {
      data.items.forEach((tpl) => {
        const item = el('div', { class: 'list-item' });
        item.innerHTML = `
          <div>
            <div class="name">${tpl.name}</div>
            <div class="small">${new Date(tpl.created_at * 1000).toLocaleString()}</div>
          </div>
          <div class="row">
            <button class="btn" data-id="${tpl.id}" data-action="edit"><i class="fas fa-edit"></i></button>
            <button class="btn btn-danger" data-id="${tpl.id}" data-action="delete"><i class="fas fa-trash-alt"></i></button>
          </div>`;
        container.appendChild(item);
      });
    }
  }

  async function handleTemplateAction(e) {
    const button = e.target.closest('button');
    if (!button) return;
    const { id, action } = button.dataset;

    if (action === 'delete') {
      if (confirm('Delete this template?')) {
        await fetch(`/api/templates/delete/${id}`, { method: 'POST' }).catch(e => {
            console.error("Error deleting template:", e);
            showError("Failed to delete template.");
        });
        loadTemplates();
      }
    } else if (action === 'edit') {
      const r = await fetch(`/api/templates/get/${id}`).catch(e => {
        console.error("Error fetching template:", e);
        showError("Failed to load template settings.");
        return null;
      });
      if (!r) return;
      const settings = await r.json().catch(() => null);
      if (!settings) return;

      const nameEl = document.getElementById('bot_name');
      const sideEl = document.getElementById('trade_side');
      const mmEl = document.getElementById('margin_mode');
      if (nameEl) nameEl.value = settings.bot_name || '';
      if (sideEl) sideEl.value = settings.side || '';
      if (mmEl) mmEl.value = settings.margin_mode || '';

      selectedCoins.clear();
      if (tsInstance) {
        try { tsInstance.clear(); } catch {}
      }
      (settings.coins || []).forEach((coin) => {
        selectedCoins.set(coin.symbol, coin);
        if (tsInstance) {
          try { tsInstance.addItem(coin.symbol, true); } catch {}
        }
      });
      renderCoinSettings();
    }
  }
  
  async function submitTrade() {
    const botNameEl = document.getElementById('bot_name');
    const accountEl = document.getElementById('bot_account');
    const tradeSideEl = document.getElementById('trade_side');
    const marginModeEl = document.getElementById('margin_mode');
    
    const payload = {
        bot_name: botNameEl ? botNameEl.value.trim() : '',
        account_id: accountEl ? accountEl.value : '',
        coins: []
    };
    
    const side = tradeSideEl ? tradeSideEl.value : 'LONG';
    const margin_mode = marginModeEl ? marginModeEl.value : 'ISOLATED';

    if (!payload.bot_name || !payload.account_id || selectedCoins.size === 0) {
        return showError('Bot Name, Account, and at least one Coin are required.');
    }

    selectedCoins.forEach(coin => {
        payload.coins.push({ 
            symbol: coin.symbol, 
            side: side, 
            leverage: coin.leverage, 
            margin: coin.margin, 
            margin_mode: margin_mode 
        });
    });

    const btn = document.getElementById('btn_submit_trade');
    if(btn) {
      btn.disabled = true;
      btn.textContent = 'Submitting...';
    }

    try {
        const r = await fetch('/api/trades/submit', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify(payload) 
        });
        const res = await r.json();
        if (r.ok) {
            alert(res.message || 'Trade submitted successfully!');
            selectedCoins.clear();
            if(tsInstance) tsInstance.clear();
            renderCoinSettings();
            // Trigger an immediate fetch after success
            fetchAndUpdateTrades(payload.account_id);

        } else {
            showError(res.error || 'An unknown error occurred.');
        }
    } catch(e) { 
        showError('A network error occurred: ' + e.message); 
    } finally { 
        if(btn) {
          btn.disabled = false;
          btn.textContent = 'Submit Trade';
        }
    }
  }

  async function initDashboard() {
    try {
      const r = await fetch('/api/futures/symbols');
      const d = await r.json();
      if (d && d.symbols) {
        symbolsCache = d.symbols.map((s) => ({ value: s, text: s }));
      }
    } catch {}

    const coinSearch = document.querySelector('#coin_search_input');
    if (coinSearch && window.TomSelect && !tsInstance) {
      tsInstance = new TomSelect(coinSearch, {
        options: symbolsCache,
        create: false,
        onItemAdd: (value) => {
          addCoin(value);
          tsInstance.setTextboxValue('');
        },
        onItemRemove: (value) => removeCoin(value),
      });
    }

    const settingsContainer = document.getElementById('coin_settings_container');
    if (settingsContainer) {
      settingsContainer.addEventListener('click', (e) => {
        if (e.target.classList.contains('btn-tiny-danger')) removeCoin(e.target.dataset.symbol);
      });
      settingsContainer.addEventListener('input', (e) => {
        if (e.target.classList.contains('coin-setting-input')) {
            const { symbol, key } = e.target.dataset;
            const value = parseFloat(e.target.value);
            const cleanValue = isNaN(value) ? 0 : value;

            const coinData = selectedCoins.get(symbol);
            if (coinData) {
                coinData[key] = cleanValue;
            }

            const estCostEl = document.getElementById(`est-cost-${symbol}`);
            if (estCostEl && coinData) {
                const estCost = coinData.leverage > 0 ? (coinData.margin / coinData.leverage).toFixed(2) : '0.00';
                estCostEl.textContent = `${estCost} USDT`;
            }

            updateTotalCost();
        }
      });
    }

    document.getElementById('btn_submit_trade')?.addEventListener('click', submitTrade);
    document.getElementById('bot_account')?.addEventListener('change', (e) => {
      const accountId = e.target.value;
      if (accountId) {
        startPolling(accountId);
      } else {
        stopPolling();
      }
    });

    document.getElementById('roi_search_input')?.addEventListener('input', renderRunningTrades);
    document.getElementById('btn_close_selected')?.addEventListener('click', closeSelectedTrades);
    document.getElementById('btn_close_all')?.addEventListener('click', closeAllTrades);

    // Restore the collapsible functionality
    const collapsible = document.querySelector('.collapsible');
    if (collapsible) {
      collapsible.addEventListener('click', function () {
        this.classList.toggle('active');
        const next = this.nextElementSibling;
        if (next) next.style.display = next.style.display === 'block' ? 'none' : 'block';
      });
    }

    loadTemplates();
    document.getElementById('btn_save_template')?.addEventListener('click', saveTemplate);
    document.getElementById('tpl_list')?.addEventListener('click', handleTemplateAction);
  }

  // Expose only the initializer
  window.initDashboard = initDashboard;

  // Optional auto-init if developer sets data-auto-init on this script tag
  if (document.currentScript && document.currentScript.hasAttribute('data-auto-init')) {
    if (document.readyState !== 'loading') initDashboard();
    else document.addEventListener('DOMContentLoaded', initDashboard, { once: true });
  }
})();