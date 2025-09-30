function renderAccounts(items){
  const root=document.getElementById('account_list'); root.innerHTML='';
  if(!items||!items.length){root.innerHTML='<div class="small">NO ACCOUNT ADDED</div>';return;}
  for(const acc of items){
    const el=document.createElement('div'); el.className='list-item';
    el.innerHTML=`<div><div class="name">${acc.name}</div><div class="small">Balance: $${(acc.futures_balance||0).toFixed(2)} • ${acc.testnet?'Testnet':'Mainnet'}</div></div>
    <div class="row"><label class="switch"><input type="checkbox" ${acc.active?'checked':''} data-id="${acc.id}" class="acc-toggle"><span class="dot"></span></label>
    <button class="btn btn-danger acc-del" data-id="${acc.id}"><i class="fas fa-trash-alt"></i></button></div>`;
    root.appendChild(el);
  }
  
  // FIX: After toggle, refresh the balances from the server to get the latest status
  root.querySelectorAll('.acc-toggle').forEach(x=>x.addEventListener('change',async ev=>{
    const id=ev.target.getAttribute('data-id'); 
    await fetch(`/accounts/toggle/${id}`,{method:'POST'});
    
    // Refresh account list to reflect the latest balance/status
    const r=await fetch('/accounts/update_balances',{method:'POST'}); 
    const d=await r.json(); 
    if(!d.error) renderAccounts(d.accounts);
  }));
  
  root.querySelectorAll('.acc-del').forEach(x=>x.addEventListener('click',async ev=>{
    const id=ev.target.closest('button').getAttribute('data-id'); if(!confirm('Delete this account?'))return;
    const r=await fetch(`/accounts/delete/${id}`,{method:'POST'}); const d=await r.json(); renderAccounts(d.accounts);
  }));
}

// FIX for Bug 3: New function to refresh all balances.
async function refreshAllBalances() {
    const btn = document.getElementById('btn_refresh_balances');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Updating...';

    const r = await fetch('/accounts/update_balances', { method: 'POST' });
    const d = await r.json().catch(() => ({ error: 'Failed to parse response' }));

    if (d.error) {
        alert('Error refreshing balances: ' + d.error);
    } else {
        renderAccounts(d.accounts);
    }
    
    btn.disabled = false;
    btn.textContent = originalText;
}

document.getElementById('btn_save').addEventListener('click', async () => {
    // Get elements by ID
    const acc_name_input = document.getElementById('acc_name');
    const acc_exchange_input = document.getElementById('acc_exchange');
    const acc_api_key_input = document.getElementById('acc_api_key');
    const acc_api_secret_input = document.getElementById('acc_api_secret');
    const acc_testnet_input = document.getElementById('acc_testnet');

    const body = {
        name: acc_name_input.value.trim(),
        exchange: acc_exchange_input.value,
        api_key: acc_api_key_input.value.trim(),
        api_secret: acc_api_secret_input.value.trim(),
        testnet: acc_testnet_input.checked ? 1 : 0
    };

    if (!body.name || !body.api_key || !body.api_secret) {
        alert('Please fill name, key and secret');
        return;
    }

    const r = await fetch('/accounts/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    const txt = await r.text();
    let d;
    try {
        d = JSON.parse(txt);
    } catch (e) {
        alert('Failed to save: ' + txt);
        return;
    }

    if (d.error) {
        alert('Error: ' + d.error);
        return;
    }

    // Clear the form and re-render the list
    acc_name_input.value = '';
    acc_api_key_input.value = '';
    acc_api_secret_input.value = '';
    renderAccounts(d.accounts);
});

// Initial rendering call from the data passed by the template
renderAccounts(window.__ACCOUNTS__ || []);

// Attach event listener for the new refresh button (Bug 3 Fix)
document.getElementById('btn_refresh_balances')?.addEventListener('click', refreshAllBalances);