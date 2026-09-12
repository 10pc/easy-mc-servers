const csrf = document.querySelector('meta[name=csrf-token]').content;
const list = document.getElementById('servers');
const logs = document.getElementById('logs');
const consoleTitle = document.getElementById('consoleTitle');
const consoleServer = document.getElementById('consoleServer');
let servers = [];
let selectedId = null;
let ztOpen = new Set();

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
  const m = Math.floor(s / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d) return `${d}d ${h % 24}h`;
  if (h) return `${h}h ${m % 60}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

function selected() {
  return servers.find(s => s.id === selectedId) || null;
}

async function refresh() {
  try {
    servers = await api('/api/servers');
  } catch (e) { return; }
  if (!servers.length) {
    list.innerHTML = '<p>no servers yet. add one: <code>add-mcserver &lt;dir&gt; --command ... --port 25565</code></p>';
    selectedId = null;
  } else {
    if (!servers.find(s => s.id === selectedId)) selectedId = servers[0].id;
  }
  list.innerHTML = '';
  for (const s of servers) {
    const running = ['starting', 'online', 'stopping'].includes(s.status);
    const d = document.createElement('div');
    d.className = 'card server' + (s.id === selectedId ? ' selected' : '');
    d.innerHTML = `
      <div class="row"><span class="dot ${s.status}"></span><strong>${s.name}</strong>
      <span>${s.status}${s.status === 'online' ? ' · up ' + fmtUptime(s.uptime) : ''}</span>
      <span>· port <code>${s.mc_port}</code></span></div>
      ${s.domain ? `<div class="domain">e4mc: <code>${s.domain}</code> <button data-copy="${s.domain}">copy</button></div>`
        : `<div class="domain">e4mc: <em>${running ? 'waiting for domain…' : '—'}</em></div>`}
      <div class="row" style="margin-top:8px"><button data-zt="${s.id}">zerotier</button></div>
      <div data-ztpanel="${s.id}" style="display:${ztOpen.has(s.id) ? 'block' : 'none'}">
        <div class="domain">network: <code>${s.zt_net}</code> <button data-copy="${s.zt_net}">copy</button></div>
        <div class="domain">address: <code>${s.zt_ip}:${s.mc_port}</code> <button data-copy="${s.zt_ip}:${s.mc_port}">copy</button></div>
        <div class="domain"><a href="https://www.zerotier.com/download/" target="_blank" rel="noopener">download zerotier</a></div>
        <img src="/static/tutorial.webp" alt="join new network screenshot" style="max-width:100%;margin-top:8px;border:2px inset #ffffff">
        <div><em>message the admin your zerotier address to get authorized</em></div>
      </div>
      ${s.e4mc_error ? `<p class="err">${s.e4mc_error}</p>` : ''}
      <div class="row" style="margin-top:8px">
        <button data-act="start" data-id="${s.id}" ${running ? 'disabled' : ''}>start</button>
        <button data-act="stop" data-id="${s.id}" class="stop" ${running ? '' : 'disabled'}>stop</button>
        <button data-act="regenerate" data-id="${s.id}" ${running ? '' : 'disabled'}>refresh address</button>
      </div>`;
    d.onclick = () => { selectedId = s.id; refresh(); loadLogs(); };
    list.appendChild(d);
  }
  list.querySelectorAll('button[data-act]').forEach(b => b.onclick = async (ev) => {
    ev.stopPropagation();
    if (b.dataset.act === 'regenerate' && !confirm('get a new e4mc address? the old one will stop working and players must reconnect.')) return;
    b.disabled = true;
    try { await api(`/api/servers/${b.dataset.id}/${b.dataset.act}`, { method: 'POST' }); }
    catch (e) { alert(e.message); }
    refresh();
  });
  list.querySelectorAll('button[data-copy]').forEach(b => b.onclick = (ev) => {
    ev.stopPropagation();
    navigator.clipboard.writeText(b.dataset.copy);
  });
  list.querySelectorAll('button[data-zt]').forEach(b => b.onclick = (ev) => {
    ev.stopPropagation();
    if (ztOpen.has(b.dataset.zt)) ztOpen.delete(b.dataset.zt);
    else ztOpen.add(b.dataset.zt);
    refresh();
  });
  const sel = selected();
  consoleTitle.textContent = sel ? `console — ${sel.name}` : 'console';
  consoleServer.textContent = sel ? `targeting port ${sel.mc_port}` : '';
  updateConsoleBtn();
}

function updateConsoleBtn() {
  const sel = selected();
  document.getElementById('consoleSend').disabled = !(sel && ['starting', 'online'].includes(sel.status));
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function logClass(line) {
  if (/\[cmd\]/.test(line)) return 'lg-cmd';
  if (/\[sys\]/.test(line)) return 'lg-sys';
  if (/\b(ERROR|FATAL|SEVERE)\b|(Exception|Traceback)|error:/i.test(line)) return 'lg-err';
  if (/\b(WARN|WARNING)\b/i.test(line)) return 'lg-warn';
  return '';
}

function renderLogs(text) {
  return text.split('\n').map(line => {
    const cls = logClass(line);
    const esc = escapeHtml(line);
    return cls ? `<span class="${cls}">${esc}</span>` : esc;
  }).join('\n');
}

function shouldStick(el, pad = 40) {
  return el.scrollTop + el.clientHeight >= el.scrollHeight - pad;
}

async function loadLogs() {
  if (!selectedId) { logs.textContent = 'select a server…'; return; }
  try {
    const j = await api(`/api/servers/${selectedId}/logs`);
    const stick = shouldStick(logs);
    logs.innerHTML = renderLogs(j.logs || '(no logs yet)');
    if (stick) logs.scrollTop = logs.scrollHeight;
  } catch (e) { logs.textContent = 'error: ' + e.message; }
}

async function sendConsole() {
  if (!selectedId) return;
  const box = document.getElementById('consoleIn');
  const cmd = box.value;
  if (!cmd.trim()) return;
  box.value = '';
  try {
    await api(`/api/servers/${selectedId}/console`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd }),
    });
  } catch (e) { alert(e.message); }
  loadLogs();
}

document.getElementById('logBtn').onclick = loadLogs;
document.getElementById('consoleSend').onclick = sendConsole;
document.getElementById('consoleIn').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendConsole();
});

// ---- account: change own password (all users) ----
document.getElementById('changePassBtn').onclick = async () => {
  const cur = document.getElementById('curPass').value;
  const p1 = document.getElementById('newPass1').value;
  const p2 = document.getElementById('newPass2').value;
  if (p1 !== p2) { alert('new passwords do not match'); return; }
  try {
    await api('/api/account/password', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: cur, new_password: p1 }),
    });
    document.getElementById('curPass').value = '';
    document.getElementById('newPass1').value = '';
    document.getElementById('newPass2').value = '';
    alert('password changed');
  } catch (e) { alert(e.message); }
};

// ---- admin: users & grants (only rendered for admins) ----
async function adminRefresh() {
  const wrap = document.getElementById('userList');
  if (!wrap) return;
  let data;
  try { data = await api('/api/admin/users'); }
  catch (e) { wrap.innerHTML = '<p class="err">' + e.message + '</p>'; return; }
  wrap.innerHTML = '';
  const srvOpts = data.servers.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
  for (const u of data.users) {
    const d = document.createElement('div');
    d.className = 'card';
    d.innerHTML = `
      <div class="row"><strong>${u.username}</strong>${u.is_admin ? '<span>· admin (sees all servers)</span>' : ''}
      <button data-deluser="${u.id}">delete</button>
      <button data-resetpw="${u.id}">reset password</button></div>
      ${u.is_admin ? '' : `
      <div class="domain">${u.servers.length ? u.servers.map(s => `<span><code>${s.name}</code> <button data-revoke="${u.id}:${s.id}">revoke</button></span>`).join(' ') : '<em>no servers</em>'}</div>
      <div class="row" style="margin-top:8px"><select data-grantsel="${u.id}">${srvOpts}</select>
      <button data-grant="${u.id}">grant</button></div>`}`;
    wrap.appendChild(d);
  }
  wrap.querySelectorAll('button[data-deluser]').forEach(b => b.onclick = async () => {
    if (!confirm('delete this user?')) return;
    try { await api(`/api/admin/users/${b.dataset.deluser}`, { method: 'DELETE' }); }
    catch (e) { alert(e.message); }
    adminRefresh();
  });
  wrap.querySelectorAll('button[data-resetpw]').forEach(b => b.onclick = async () => {
    const pw = prompt('new password (min 8 chars):');
    if (!pw) return;
    try {
      await api(`/api/admin/users/${b.dataset.resetpw}/password`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: pw }),
      });
      alert('password updated');
    } catch (e) { alert(e.message); }
  });
  wrap.querySelectorAll('button[data-grant]').forEach(b => b.onclick = async () => {
    const sel = wrap.querySelector(`select[data-grantsel="${b.dataset.grant}"]`);
    try {
      await api('/api/admin/grants', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: b.dataset.grant, server_id: sel.value }),
      });
    } catch (e) { alert(e.message); }
    adminRefresh();
  });
  wrap.querySelectorAll('button[data-revoke]').forEach(b => b.onclick = async () => {
    const [uid, sid] = b.dataset.revoke.split(':');
    try {
      await api('/api/admin/grants', {
        method: 'DELETE', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid, server_id: sid }),
      });
    } catch (e) { alert(e.message); }
    adminRefresh();
  });
}
const addBtn = document.getElementById('addUserBtn');
if (addBtn) {
  addBtn.onclick = async () => {
    const username = document.getElementById('newUser').value;
    const password = document.getElementById('newPass').value;
    const is_admin = document.getElementById('newAdmin').checked;
    try {
      await api('/api/admin/users', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, is_admin }),
      });
      document.getElementById('newUser').value = '';
      document.getElementById('newPass').value = '';
      document.getElementById('newAdmin').checked = false;
    } catch (e) { alert(e.message); }
    adminRefresh();
  };
  adminRefresh();
}

refresh();
setInterval(refresh, 3000);
setInterval(() => { if (selectedId) loadLogs(); }, 5000);

// ---- header system monitor ----
function setSys(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
function setBar(id, pct) {
  const el = document.getElementById(id);
  if (el) el.style.width = (pct == null ? 0 : Math.min(100, Math.max(0, pct))) + '%';
}
async function refreshSys() {
  let s;
  try { s = await api('/api/system'); }
  catch (e) { return; }
  setSys('sysCpu', s.cpu_percent == null ? '…' : `${s.cpu_percent}%`);
  setBar('barCpu', s.cpu_percent);
  setSys('sysMem', `${s.mem_used_mb}/${s.mem_total_mb}mb`);
  setBar('barMem', s.mem_percent);
  setSys('sysDisk', `${s.disk_used_gb}/${s.disk_total_gb}gb`);
  setBar('barDisk', s.disk_percent);
  setSys('sysUp', fmtUptime(s.uptime));
}
refreshSys();
setInterval(refreshSys, 5000);
