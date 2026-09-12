const csrf = document.querySelector('meta[name=csrf-token]').content;
const list = document.getElementById('servers');
const logSel = document.getElementById('logSel');
const logs = document.getElementById('logs');
let servers = [];

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: { 'X-CSRF-Token': csrf, ...(opts.headers || {}) },
  });
  if (r.status === 401) { location.href = '/login'; throw new Error('unauth'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function fmtUptime(s) {
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  if (h) return `${h}h ${m % 60}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

async function refresh() {
  try {
    servers = await api('/api/servers');
  } catch (e) { return; }
  list.innerHTML = '';
  const prevSel = logSel.value;
  logSel.innerHTML = '';
  if (!servers.length) list.innerHTML = '<p>No servers yet. Add one: <code>add-mcserver &lt;dir&gt; --command ... --port 25565</code></p>';
  for (const s of servers) {
    const o = document.createElement('option');
    o.value = s.id; o.textContent = s.name;
    logSel.appendChild(o);
    const d = document.createElement('div');
    d.className = 'card';
    const running = ['starting', 'online', 'stopping'].includes(s.status);
    d.innerHTML = `
      <div class="row"><span class="dot ${s.status}"></span><strong>${s.name}</strong>
      <span>${s.status}${s.status === 'online' ? ' · up ' + fmtUptime(s.uptime) : ''}</span>
      <span>· port <code>${s.mc_port}</code></span></div>
      ${s.domain ? `<div class="domain">e4mc: <code>${s.domain}</code> <button data-copy="${s.domain}">Copy</button></div>`
        : `<div class="domain">e4mc: <em>${running ? 'waiting for domain…' : '—'}</em></div>`}
      ${s.e4mc_error ? `<p class="err">${s.e4mc_error}</p>` : ''}
      <div class="row" style="margin-top:8px">
        <button data-act="start" data-id="${s.id}" ${running ? 'disabled' : ''}>Start</button>
        <button data-act="stop" data-id="${s.id}" class="stop" ${running ? '' : 'disabled'}>Stop</button>
        <button data-act="regenerate" data-id="${s.id}" ${running ? '' : 'disabled'}>refresh address</button>
      </div>`;
    list.appendChild(d);
  }
  if (prevSel) logSel.value = prevSel;
  updateConsoleBtn();
  list.querySelectorAll('button[data-act]').forEach(b => b.onclick = async () => {
    if (b.dataset.act === 'regenerate' && !confirm('Get a new e4mc address? The old one will stop working and players must reconnect.')) return;
    b.disabled = true;
    try { await api(`/api/servers/${b.dataset.id}/${b.dataset.act}`, { method: 'POST' }); }
    catch (e) { alert(e.message); }
    refresh();
  });
  list.querySelectorAll('button[data-copy]').forEach(b => b.onclick = () => navigator.clipboard.writeText(b.dataset.copy));
}

async function loadLogs() {
  const id = logSel.value;
  if (!id) return;
  try {
    const j = await api(`/api/servers/${id}/logs`);
    logs.textContent = j.logs || '(no logs yet)';
    logs.scrollTop = logs.scrollHeight;
  } catch (e) { logs.textContent = 'error: ' + e.message; }
}

document.getElementById('logBtn').onclick = loadLogs;

function updateConsoleBtn() {
  const sel = servers.find(s => s.id === logSel.value);
  document.getElementById('consoleSend').disabled = !(sel && ['starting', 'online'].includes(sel.status));
}
logSel.onchange = () => { loadLogs(); updateConsoleBtn(); };

async function sendConsole() {
  const id = logSel.value;
  if (!id) return;
  const box = document.getElementById('consoleIn');
  const cmd = box.value;
  if (!cmd.trim()) return;
  box.value = '';
  try {
    await api(`/api/servers/${id}/console`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd }),
    });
  } catch (e) { alert(e.message); }
  loadLogs();
}
document.getElementById('consoleSend').onclick = sendConsole;
document.getElementById('consoleIn').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendConsole();
});
refresh();
setInterval(refresh, 3000);
setInterval(() => { if (logSel.value) loadLogs(); }, 5000);
