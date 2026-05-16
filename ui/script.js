let state = {
  running: false,
  server_state: 'offline',
  project_key: '',
  task: { running: false }
};

let statusBusy = false;
let logsBusy = false;
let taskBusy = false;
let statusTimer = null;
let wizardMode = 'host';
let wizardPinnedOpen = false;
let backendOffline = false;
const toastSeen = new Set();

function setWizardMode(mode) {
  wizardMode = mode === 'join' ? 'join' : 'host';
  document.getElementById('w-tab-host')?.classList.toggle('on', wizardMode === 'host');
  document.getElementById('w-tab-join')?.classList.toggle('on', wizardMode === 'join');
  document.getElementById('w-join-box')?.classList.toggle('hidden', wizardMode !== 'join');
}

function updateSetupUI(d) {
  const complete = !!d?.setup_complete;
  const wiz = document.getElementById('wizard');
  const app = document.getElementById('app-main');
  if (!wiz || !app) return;

  const backBtn = document.getElementById('w-back-dash');
  if (backBtn) backBtn.classList.toggle('hidden', !complete);

  if (wizardPinnedOpen) {
    wiz.classList.remove('hidden');
    app.classList.add('hidden');
    return;
  }

  if (complete) {
    wiz.classList.add('hidden');
    app.classList.remove('hidden');
  } else {
    wiz.classList.remove('hidden');
    app.classList.add('hidden');
    const wu = document.getElementById('w-user');
    if (wu && !wu.value.trim() && d?.user) wu.value = d.user;
  }

  const banner = document.getElementById('ready-banner');
  if (banner) {
    const steps = Array.isArray(d?.setup_next_steps) ? d.setup_next_steps : [];
    if (complete && steps.length) {
      banner.classList.remove('hidden');
      banner.innerHTML = '<b>Agla step:</b> ' + steps.map((s) => `<span>${s}</span>`).join(' · ');
    } else {
      banner.classList.add('hidden');
      banner.innerHTML = '';
    }
  }
}

async function runQuickSetup() {
  const btn = document.getElementById('w-go');
  const st = document.getElementById('w-status');
  const name = (document.getElementById('w-user')?.value || '').trim();
  if (!name) {
    toast('Apna naam likho', true);
    return;
  }
  if (btn) btn.disabled = true;
  if (st) st.textContent = 'Detecting server folder, creating Server ID, configuring sync...';

  const body = {
    user: name,
    mode: wizardMode,
    friend_invite: wizardMode === 'join' ? (document.getElementById('w-invite')?.value || '').trim() : '',
  };

  try {
    const d = await post('/setup/quick', body);
    if (st) {
      const lines = [d.msg || ''];
      if (Array.isArray(d.next_steps)) lines.push('', ...d.next_steps.map((s, i) => `${i + 1}. ${s}`));
      st.textContent = lines.filter(Boolean).join('\n');
    }
    if (d.ok && d.setup_complete) {
      wizardPinnedOpen = false;
      toastOnce('setup-ok', 'Setup ho gaya!');
      setTimeout(() => {
        pollStatus();
        loadInvite();
      }, 400);
    } else {
      toast(d.msg || 'Setup failed', true);
    }
  } catch (e) {
    if (st) st.textContent = 'Setup failed — backend offline?';
    toast('Setup failed', true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function showWizardAgain() {
  wizardPinnedOpen = true;
  document.getElementById('wizard')?.classList.remove('hidden');
  document.getElementById('app-main')?.classList.add('hidden');
}

function closeWizardToDashboard() {
  wizardPinnedOpen = false;
  pollStatus();
}

async function copyServerId() {
  const id = state.server_id || document.getElementById('hero-server-id')?.textContent || '';
  if (!id || id === '—') {
    toast('Server ID not ready', true);
    return;
  }
  try {
    await navigator.clipboard.writeText(id);
    toast('Server ID copied');
  } catch (e) {
    toast('Copy failed', true);
  }
}

let toastHideTimer = null;

function toast(msg, isErr = false) {
  const t = document.getElementById('toast');
  if (!t) return;
  const text = String(msg || '').trim();
  if (!text) return;
  if (toastHideTimer) clearTimeout(toastHideTimer);
  t.textContent = text;
  t.className = 'toast show' + (isErr ? ' err' : '');
  toastHideTimer = setTimeout(() => {
    t.className = 'toast';
    toastHideTimer = null;
  }, 3200);
}

function toastOnce(key, msg, isErr = false) {
  const k = String(key || msg || '');
  if (!k || toastSeen.has(k)) return;
  toastSeen.add(k);
  toast(msg, isErr);
}

function getKey() {
  return (document.getElementById('f-key').value || '').trim();
}

function withAuthHeaders(extra = {}) {
  const h = { 'Content-Type': 'application/json', ...extra };
  const key = getKey();
  if (key) h['X-MC-Project-Key'] = key;
  return h;
}

function fmtUptime(sec) {
  const s = Math.max(0, Number(sec || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = Math.floor(s % 60);
  if (h > 0) return `${h}h ${m}m ${r}s`;
  if (m > 0) return `${m}m ${r}s`;
  return `${r}s`;
}

function setFill(id, v) {
  const n = Math.max(0, Math.min(100, Math.round(Number(v || 0))));
  const e = document.getElementById(id);
  if (e) e.style.width = n + '%';
  return n;
}

function applyStatus(d) {
  state = d || state;
  document.title = `${d.project_name || 'Minecraft Server'} - Host Manager`;
  document.getElementById('title').textContent = d.project_name || 'Minecraft Server';

  const badge = document.getElementById('badge');
  if (d.task && d.task.running) {
    badge.className = 'badge warn';
    badge.textContent = (d.task.action || 'Working').toUpperCase();
  } else if (d.running) {
    badge.className = 'badge ok';
    badge.textContent = 'Server Online';
  } else {
    badge.className = 'badge';
    badge.textContent = 'Offline';
  }

  document.getElementById('s-state').textContent = d.server_state || (d.running ? 'running' : 'offline');
  document.getElementById('s-state').className = 'v ' + ((d.running && !d.task?.running) ? 'ok' : (d.task?.running ? 'warn' : 'err'));
  document.getElementById('s-host').textContent = d.lock?.host || d.user || '-';
  document.getElementById('s-addr').textContent = d.running ? `${d.local_ip}:25565` : '---';
  const syncRaw = String(d.syncthing_status || 'stopped').toLowerCase();
  let syncLabel = syncRaw.toUpperCase();
  if (d.sync_isolated) {
    if (syncRaw === 'missing') syncLabel = 'NOT LINKED';
    else if (syncRaw === 'running') syncLabel = 'NO PEERS';
    else syncLabel = 'NOT READY';
    document.getElementById('s-sync').className = 'v err';
  } else if (syncRaw === 'connected') {
    syncLabel = 'CONNECTED';
    document.getElementById('s-sync').className = 'v ok';
  } else {
    document.getElementById('s-sync').className = 'v warn';
  }
  document.getElementById('s-sync').textContent = syncLabel;
  document.getElementById('s-players').textContent = `${d.players_count || 0}`;

  const sf = document.getElementById('s-friends');
  if (sf) {
    const on = Number(d.members_online || 0);
    const tot = Number(d.members_total || 0);
    sf.textContent = tot ? `${on} online` : '—';
    sf.className = 'v ' + (on > 0 ? 'ok' : '');
  }

  const hero = document.getElementById('hero-server-id');
  if (hero) hero.textContent = d.server_id || '—';
  const sidHidden = document.getElementById('f-server-id');
  if (sidHidden) sidHidden.value = d.server_id || '';

  updateSetupUI(d);

  const cpu = setFill('m-cpu', d.server_cpu_pct || 0);
  const mem = setFill('m-mem', d.server_mem_pct || 0);
  const cpuV = document.getElementById('m-cpu-v');
  const memV = document.getElementById('m-mem-v');
  if (cpuV) cpuV.textContent = `${cpu}%`;
  if (memV) memV.textContent = `${mem}%`;

  const hint = document.getElementById('start-hint');
  const blockReason = String(d.start_block_reason || '').trim();
  if (hint) {
    if (!d.running && blockReason) {
      hint.textContent = blockReason;
      hint.className = 'start-hint show';
    } else {
      hint.textContent = '';
      hint.className = 'start-hint hidden';
    }
  }

  const main = document.getElementById('btn-main');
  if (d.task && d.task.running) {
    main.disabled = true;
    main.className = '';
    main.textContent = '⏳ WORKING...';
  } else if (d.running) {
    main.disabled = false;
    main.className = 'btn-danger';
    main.textContent = '⏹ STOP';
  } else if (d.can_start === false) {
    main.disabled = true;
    main.className = '';
    main.textContent = '🔒 START LOCKED';
  } else {
    main.disabled = false;
    main.className = 'btn-good';
    main.textContent = '⚡ START SERVER';
  }

  renderMembers(d);
  renderSyncthing(d);

  const fu = document.getElementById('f-user');
  if (fu && !fu.value.trim()) fu.value = d.user || '';
  const fp = document.getElementById('f-project');
  if (fp) fp.value = d.project_name || 'Minecraft Server';
  const fs = document.getElementById('f-server');
  if (fs && !fs.value.trim()) fs.value = d.server_dir || '';
  const fsh = document.getElementById('f-shared');
  if (fsh) fsh.value = d.shared_dir || '';
  const fj = document.getElementById('f-jar');
  if (fj && !fj.value.trim()) fj.value = d.server_jar || 'server.jar';
  const fr = document.getElementById('f-ram');
  if (fr) fr.value = d.ram || '4G';
  const fm = document.getElementById('f-max');
  if (fm) fm.value = String(d.max_players || 20);
  const fw = document.getElementById('f-whitelist');
  if (fw) fw.checked = !!d.whitelist_enabled;
  const fk = document.getElementById('f-key');
  if (fk && !fk.value.trim()) fk.value = d.project_key || '';

  if (d.server_id && d.server_id_on_disk && !d.server_id_synced) {
    const mismatch = `Server ID mismatch: disk has ${d.server_id_on_disk}, settings have ${d.server_id}.`;
    if (hint && !d.running) {
      hint.textContent = mismatch;
      hint.className = 'start-hint show';
    }
    toastOnce('sid-mismatch', mismatch, true);
  }

  if (d.last_error) {
    toastOnce('last-err:' + d.last_error, d.last_error, true);
  }
}

function renderMembers(d) {
  const sum = document.getElementById('members-summary');
  const list = document.getElementById('members-list');
  if (!sum || !list) return;
  const sid = d.server_id || '—';
  const online = Number(d.members_online || 0);
  const total = Number(d.members_total || 0);
  sum.textContent = `Server ID ${sid}: ${online} online / ${total} friend${total === 1 ? '' : 's'}`;
  const rows = Array.isArray(d.members) ? d.members : [];
  if (!rows.length) {
    list.innerHTML = '<div class="small">No friends yet. Share Server ID + connect Syncthing peers.</div>';
    return;
  }
  list.innerHTML = rows.map((m) => {
    const dot = m.hosting ? '🟢' : (m.online ? '🟡' : '⚫');
    const tag = m.hosting ? 'hosting' : (m.online ? 'online' : 'offline');
    return `<div class="member-row ${m.online ? 'on' : ''}">
      <span><b>${dot} ${m.user || 'Unknown'}</b></span>
      <span class="small">${m.hostname || ''} · ${tag}</span>
    </div>`;
  }).join('');
}

async function detectServer() {
  const hint = document.getElementById('detect-hint');
  if (!hint) return;
  hint.textContent = 'Searching...';
  try {
    const r = await fetch('/setup/detect');
    const d = await r.json();
    const list = Array.isArray(d.candidates) ? d.candidates : [];
    if (!list.length) {
      hint.textContent = 'No server folder found. Enter path manually, then Save.';
      return;
    }
    document.getElementById('f-server').value = list[0].path || '';
    if (list[0].jar) document.getElementById('f-jar').value = list[0].jar;
    hint.textContent = `Found: ${list[0].label || list[0].path}`;
    if (list.length > 1) hint.textContent += ` (+${list.length - 1} more)`;
  } catch (e) {
    hint.textContent = 'Detect failed.';
  }
}

function scheduleStatus() {
  if (statusTimer) clearTimeout(statusTimer);
  let delay = 1100;
  if (wizardPinnedOpen) delay = 2200;
  else if (state.task?.running) delay = 320;
  else if (state.running) delay = 650;
  if (document.hidden) delay = Math.max(delay, 2200);
  statusTimer = setTimeout(pollStatus, delay);
}

async function pollStatus() {
  if (statusBusy) return;
  statusBusy = true;
  try {
    const r = await fetch('/status');
    if (!r.ok) throw new Error('bad status');
    const d = await r.json();
    if (backendOffline) {
      backendOffline = false;
      toastOnce('backend-ok', 'Connected again');
    }
    applyStatus(d);
  } catch (e) {
    if (!backendOffline) {
      backendOffline = true;
      toastOnce('backend-off', 'Backend offline — is the app running?', true);
    }
  } finally {
    statusBusy = false;
    scheduleStatus();
  }
}

async function pollLogs() {
  if (logsBusy) return;
  if (!state.running && !state.task?.running) return;
  logsBusy = true;
  try {
    const r = await fetch('/logs');
    const d = await r.json();
    const el = document.getElementById('console');
    const atBottom = Math.abs(el.scrollHeight - el.scrollTop - el.clientHeight) < 30;
    el.textContent = (d.logs || []).join('\n') || 'Waiting for server...';
    if (atBottom) el.scrollTop = el.scrollHeight;
  } catch (e) {}
  finally { logsBusy = false; }
}

async function pollTask() {
  if (taskBusy) return;
  if (!state.task?.running) return;
  taskBusy = true;
  try {
    const r = await fetch('/task');
    const t = await r.json();
    if (!t.running && t.error) toastOnce('task-err:' + t.error, t.error, true);
  } catch (e) {}
  finally { taskBusy = false; }
}

async function loadBackups() {
  try {
    const r = await fetch('/backup/list');
    const d = await r.json();
    const list = Array.isArray(d.backups) ? d.backups : [];
    const wrap = document.getElementById('backup-list');
    if (!list.length) {
      wrap.innerHTML = '<div class="small">No backups yet.</div>';
      return;
    }
    wrap.innerHTML = list.map(b => `
      <div class="backup-item">
        <div>
          <div><b>${b.time}</b></div>
          <div class="small">${b.name} (${b.size_mb} MB)</div>
        </div>
        <a class="btn-link" href="/backup/get?name=${encodeURIComponent(b.name)}" download="${b.name}">Download</a>
      </div>
    `).join('');
  } catch (e) {}
}

async function post(url, body = {}) {
  const r = await fetch(url, {
    method: 'POST',
    headers: withAuthHeaders(),
    body: JSON.stringify({ ...body, project_key: getKey() })
  });
  return r.json();
}

async function toggleHost() {
  if (state.task?.running) return;
  const action = state.running ? 'stop' : 'start';
  if (action === 'stop') {
    const ok = confirm('Stop server safely? (save + backup + sync)');
    if (!ok) return;
  } else if (state.can_start === false) {
    toast(state.start_block_reason || 'Cannot start right now.', true);
    return;
  } else if (state.sync_isolated) {
    const ok = confirm(
      'Syncthing is not linked to friends yet.\n\n' +
      'If you start now, this PC uses its own copy of files (not shared).\n\n' +
      'Continue only for solo testing?'
    );
    if (!ok) return;
  }
  try {
    const body = action === 'stop' ? { confirm_remote_stop: true } : {};
    if (action === 'start' && state.sync_isolated) body.ack_isolated_risk = true;
    const d = await post('/host/' + action, body);
    toast(d.msg || (d.ok ? 'OK' : 'Failed'), !d.ok);
  } catch (e) {
    toast('Request failed', true);
  }
  setTimeout(() => pollStatus(), 220);
}

async function restartHost() {
  if (state.task?.running) return;
  if (!confirm('Restart server safely?')) return;
  try {
    const d = await post('/host/restart', {});
    toast(d.msg || (d.ok ? 'Restarting...' : 'Failed'), !d.ok);
  } catch (e) {
    toast('Request failed', true);
  }
  setTimeout(() => pollStatus(), 220);
}

async function backupNow() {
  if (state.task?.running) return;
  try {
    const d = await post('/backup/now', {});
    toast(d.msg || (d.ok ? 'Backup started' : 'Failed'), !d.ok);
  } catch (e) {
    toast('Backup request failed', true);
  }
  setTimeout(() => loadBackups(), 1200);
}

async function syncNow() {
  try {
    const d = await post('/sync/now', {});
    toast(d.msg || (d.ok ? 'Sync triggered' : 'Sync failed'), !d.ok);
  } catch (e) {
    toast('Sync request failed', true);
  }
}

async function sendCmd() {
  const input = document.getElementById('cmd');
  const cmd = (input.value || '').trim();
  if (!cmd) return;
  input.value = '';
  try {
    const d = await post('/command', { cmd });
    if (!d.ok) toast(d.msg || 'Command failed', true);
  } catch (e) {
    toast('Command request failed', true);
  }
}

async function saveSettings() {
  const serverId = (document.getElementById('f-server-id').value || '').trim().toUpperCase();
  const body = {
    user: document.getElementById('f-user').value,
    project_name: document.getElementById('f-project').value,
    server_id: serverId,
    server_dir: document.getElementById('f-server').value,
    server_jar: document.getElementById('f-jar').value,
    ram: document.getElementById('f-ram').value,
    max_players: parseInt(document.getElementById('f-max').value || '20', 10),
    whitelist_enabled: !!document.getElementById('f-whitelist').checked,
  };
  try {
    const d = await post('/config/save', body);
    if (d.ok) {
      if (d.server_id) document.getElementById('f-server-id').value = d.server_id;
      if (d.shared_dir) document.getElementById('f-shared').value = d.shared_dir;
      if (d.server_dir) document.getElementById('f-server').value = d.server_dir;
    }
    const msg = d.ok ? (d.syncthing_msg ? `Saved. ${d.syncthing_msg}` : 'Settings saved') : (d.msg || 'Save failed');
    toast(msg, !d.ok);
  } catch (e) {
    toast('Save failed', true);
  }
  setTimeout(() => pollStatus(), 250);
}

async function forceClear() {
  if (!confirm('Force clear lock? Use only when host is definitely offline.')) return;
  try {
    const d = await post('/host/force', {});
    toast(d.msg || (d.ok ? 'Lock cleared' : 'Failed'), !d.ok);
  } catch (e) {
    toast('Request failed', true);
  }
  setTimeout(() => pollStatus(), 250);
}

async function openFolder(target) {
  try {
    const r = await fetch('/open-folder?target=' + encodeURIComponent(target));
    const d = await r.json();
    if (!d.ok) toast(d.msg || 'Open failed', true);
  } catch (e) {
    toast('Open failed', true);
  }
}

async function downloadServerFiles() {
  try {
    const r = await fetch('/server/download');
    if (!r.ok) {
      const txt = await r.text();
      toast(txt || `Download failed (${r.status})`, true);
      return;
    }
    const blob = await r.blob();
    const cd = r.headers.get('content-disposition') || '';
    let name = 'server_files.zip';
    const m = cd.match(/filename="?([^";]+)"?/i);
    if (m && m[1]) name = m[1];
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1200);
  } catch (e) {
    toast('Download failed', true);
  }
}

let inviteCache = { invite: '', device_id: '' };

function renderSyncthing(d) {
  const devEl = document.getElementById('st-device-id');
  const peersEl = document.getElementById('st-peers');
  if (!devEl) return;

  const did = d.syncthing_device_id || '';
  devEl.textContent = did || 'Syncthing not running';
  inviteCache.device_id = did;

  const peers = Array.isArray(d.syncthing_peers) ? d.syncthing_peers : [];
  if (peersEl) {
    if (!peers.length) {
      peersEl.textContent = 'Syncthing peers: none added yet';
    } else {
      peersEl.textContent = 'Syncthing peers: ' + peers.map((p) => {
        const mark = p.connected ? '🟢' : '⚫';
        return `${mark} ${p.name || p.device_id?.slice(0, 7)}`;
      }).join(' · ');
    }
  }
}

async function loadInvite() {
  const qr = document.getElementById('st-qr');
  try {
    const r = await fetch('/syncthing/invite');
    const d = await r.json();
    if (!d.ok) {
      if (qr) qr.removeAttribute('src');
      return;
    }
    inviteCache.invite = d.invite || '';
    inviteCache.device_id = d.device_id || inviteCache.device_id;
    const devEl = document.getElementById('st-device-id');
    if (devEl && d.device_id) devEl.textContent = d.device_id;
    if (qr && d.qr_url) qr.src = d.qr_url;
  } catch (e) {
    if (qr) qr.removeAttribute('src');
  }
}

async function copySyncthingDevice() {
  const text = inviteCache.device_id || document.getElementById('st-device-id')?.textContent || '';
  if (!text || text.includes('not running')) {
    toast('Syncthing device ID not available', true);
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast('Device ID copied');
  } catch (e) {
    toast('Copy failed', true);
  }
}

async function copyInvite() {
  if (!inviteCache.invite) await loadInvite();
  if (!inviteCache.invite) {
    toast('Invite not ready — start Syncthing first', true);
    return;
  }
  try {
    await navigator.clipboard.writeText(inviteCache.invite);
    toast('Invite copied (share with friends)');
  } catch (e) {
    toast('Copy failed', true);
  }
}

async function applyInvite() {
  const raw = (document.getElementById('f-invite')?.value || '').trim();
  if (!raw) {
    toast('Paste invite JSON first', true);
    return;
  }
  try {
    const d = await post('/syncthing/apply-invite', { invite: raw });
    toast(d.msg || (d.ok ? 'Friend added' : 'Failed'), !d.ok);
    if (d.ok && d.server_id) {
      document.getElementById('f-server-id').value = d.server_id;
    }
    document.getElementById('f-invite').value = '';
    setTimeout(() => { pollStatus(); loadInvite(); }, 300);
  } catch (e) {
    toast('Add friend failed', true);
  }
}

function openSyncthing() {
  window.open('http://127.0.0.1:8384/', '_blank');
}

setInterval(pollLogs, 1500);
setInterval(pollTask, 650);
setInterval(loadBackups, 11000);
setInterval(loadInvite, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) pollStatus(); });

setWizardMode('host');
pollStatus();
loadBackups();
loadInvite();
