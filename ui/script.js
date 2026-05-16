let state = {
  running: false,
  server_state: 'offline',
  project_key: '',
  task: { running: false },
  can_start: true,
  start_block_reason: '',
  world_conflict: false,
  world_conflict_msg: '',
  suggested_host_ip: '',
  sync_isolated: false
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

function openInvitePanel() {
  document.getElementById('panel-invite')?.classList.remove('hidden');
  document.getElementById('panel-join')?.classList.add('hidden');
  loadInvite();
}

function openJoinPanel() {
  document.getElementById('panel-join')?.classList.remove('hidden');
  document.getElementById('panel-invite')?.classList.add('hidden');
}

function resolveUserName() {
  return (
    (document.getElementById('f-user')?.value || '').trim() ||
    (document.getElementById('w-user')?.value || '').trim() ||
    (state.user || '').trim()
  );
}

async function joinFriend() {
  const raw = (document.getElementById('join-input')?.value || '').trim();
  if (!raw) {
    toast('Server ID likho', true);
    return;
  }
  const btn = document.querySelector('#panel-join .btn-wizard');
  if (btn) btn.disabled = true;
  try {
    const d = await post('/server/join', { invite: raw, user: resolveUserName() });
    toast(d.msg || (d.ok ? 'Joined!' : 'Failed'), !d.ok);
    if (d.ok) {
      wizardPinnedOpen = false;
      document.getElementById('join-input').value = '';
      document.getElementById('panel-join')?.classList.add('hidden');
      if (!raw.includes(':') && raw.length <= 12) {
        toastOnce('join-sync-hint', 'Group join OK. World sync ke liye host ka full invite (Copy invite) dubara paste karo.', false);
      }
      if (d.server_id) {
        state.server_id = d.server_id;
        const hero = document.getElementById('hero-server-id');
        if (hero) hero.textContent = d.server_id;
        const fk = document.getElementById('f-key');
        if (d.project_key && fk) fk.value = d.project_key;
      }
      if (d.invite_code) inviteCache.invite_code = d.invite_code;
      pollStatus();
      loadInvite();
    }
  } catch (e) {
    toast('Join failed — app chal rahi hai?', true);
  } finally {
    if (btn) btn.disabled = false;
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
  if (wizardMode === 'join' && !(document.getElementById('w-invite')?.value || '').trim()) {
    toast('Friend ka Server ID likho', true);
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
      toastOnce('setup-ok', wizardMode === 'join' ? 'Group join ho gaya!' : 'Server ban gaya!');
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
  } else if (d.any_running) {
    badge.className = 'badge ok';
    badge.textContent = 'Server Online';
  } else {
    badge.className = 'badge';
    badge.textContent = 'Offline';
  }

  document.getElementById('s-state').textContent = d.server_state || (d.running ? 'running' : 'offline');
  document.getElementById('s-state').className = 'v ' + ((d.running && !d.task?.running) ? 'ok' : (d.task?.running ? 'warn' : 'err'));
  document.getElementById('s-host').textContent = d.lock?.host || d.remote_host || d.user || '-';
  document.getElementById('s-addr').textContent = d.running ? `${d.local_ip}:25565` : (d.remote_host ? 'Remote Host' : '---');
  document.getElementById('s-players').textContent = `${d.players_count || 0}`;
  const sg = document.getElementById('s-group');
  if (sg) sg.textContent = d.server_id || '—';

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
  const inviteLine = document.getElementById('invite-code-line');
  if (inviteLine) {
    const code = d.invite_code || (d.server_id ? `MCHOST:${d.server_id}` : '');
    inviteLine.textContent = code || '—';
    if (code) inviteCache.invite_code = code;
  }

  updateSetupUI(d);
  renderDeps(d.deps);

  const cpu = setFill('m-cpu', d.server_cpu_pct || 0);
  const mem = setFill('m-mem', d.server_cpu_pct ? d.server_mem_pct : 0); // Hide metrics if not local host
  const cpuV = document.getElementById('m-cpu-v');
  const memV = document.getElementById('m-mem-v');
  if (cpuV) cpuV.textContent = d.running ? `${cpu}%` : '0%';
  if (memV) memV.textContent = d.running ? `${mem}%` : '0%';

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
    main.textContent = '🔒 ' + (d.remote_host ? d.remote_host.toUpperCase() + ' IS HOSTING' : 'START LOCKED');
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
  const ffb = document.getElementById('f-firebase');
  if (ffb && !ffb.value.trim()) ffb.value = d.firebase_url || '';

  if (d.server_id && d.server_id_on_disk && !d.server_id_synced) {
    toastOnce('sid-mismatch', `Syncing to Server ID ${d.server_id}… save or join again if needed.`, true);
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
      <span class="small">${m.hostname || ''} · ${tag}${m.ip ? ' · ' + m.ip : ''}</span>
    </div>`;
  }).join('');

  const lanInput = document.getElementById('lan-host-ip');
  const hostRow = rows.find((m) => m.hosting && m.ip);
  const suggested = String(d.suggested_host_ip || '').trim();
  if (lanInput) {
    if (hostRow?.ip && !lanInput.value.trim()) lanInput.value = hostRow.ip;
    else if (suggested && !lanInput.value.trim()) lanInput.value = suggested;
  }

  const switchBtn = document.querySelector('.btn-switch-host');
  const switchHint = document.getElementById('switch-host-hint');
  if (switchBtn) {
    switchBtn.disabled = !!(d.running || d.task?.running);
  }
  if (switchHint) {
    const remote = String(d.remote_host || '').trim();
    if (!d.running && remote) {
      switchHint.textContent = `${remote} abhi host kar raha hai — STOP ke baad “Yahan host karo” dabao.`;
      switchHint.className = 'small switch-hint show';
    } else if (!d.running && d.sync_isolated && d.auto_world_before_start) {
      switchHint.textContent = 'Auto: friend ka IP + world sync, phir yahan server start.';
      switchHint.className = 'small switch-hint show';
    } else {
      switchHint.textContent = '';
      switchHint.className = 'small switch-hint hidden';
    }
  }

  const strictEl = document.getElementById('f-strict-sync');
  if (strictEl) strictEl.checked = !!d.strict_sync_gate;
  const autoWorldEl = document.getElementById('f-auto-world');
  if (autoWorldEl) autoWorldEl.checked = d.auto_world_before_start !== false;
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

function hostStartBody(extra = {}) {
  const hostIp = (document.getElementById('lan-host-ip')?.value || state.suggested_host_ip || '').trim();
  return {
    host_ip: hostIp,
    auto_pull: document.getElementById('f-auto-world')?.checked !== false,
    ...extra
  };
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
  } else if (state.world_conflict) {
    const ok = confirm(state.world_conflict_msg || 'Local world alag hai — overwrite OK?');
    if (!ok) return;
  }
  try {
    let body = action === 'stop' ? { confirm_remote_stop: true } : hostStartBody();
    if (action === 'start' && state.world_conflict) body.ack_world_overwrite = true;
    if (action === 'start' && state.sync_isolated && !state.start_block_reason?.includes('hosting')) {
      body.ack_isolated_risk = true;
    }
    const d = await post('/host/' + action, body);
    toast(d.msg || (d.ok ? 'OK' : 'Failed'), !d.ok);
  } catch (e) {
    toast('Request failed', true);
  }
  setTimeout(() => pollStatus(), 220);
}

async function switchHostHere() {
  if (state.task?.running || state.running) {
    toast('Pehle apna server band karo', true);
    return;
  }
  const hostIp = (document.getElementById('lan-host-ip')?.value || state.suggested_host_ip || '').trim();
  let body = { host_ip: hostIp, auto_start: true };
  if (state.world_conflict) {
    const ok = confirm(state.world_conflict_msg || 'World overwrite OK?');
    if (!ok) return;
    body.ack_world_overwrite = true;
  }
  const remote = String(state.start_block_reason || '').includes('hosting');
  if (remote) {
    const ok2 = confirm('Doosra PC abhi host kar sakta hai — unse STOP karwao. Phir yahan switch hoga. Continue?');
    if (!ok2) return;
  }
  toast('Switch host — world sync + start...');
  try {
    const d = await post('/host/switch', body);
    toast(d.msg || (d.ok ? 'Started' : 'Failed'), !d.ok);
  } catch (e) {
    toast('Switch failed — same network / host STOP?', true);
  }
  setTimeout(() => pollStatus(), 280);
}

async function pullWorldAuto() {
  if (state.running || state.task?.running) {
    toast('Pehle apna server STOP karo', true);
    return;
  }
  const hostIp = (document.getElementById('lan-host-ip')?.value || state.suggested_host_ip || '').trim();
  toast('Auto world sync...');
  try {
    const d = await post('/sync/world/auto', { host_ip: hostIp, wait_host_stop: true });
    toast(d.msg || (d.ok ? 'Sync started' : 'Failed'), !d.ok);
  } catch (e) {
    toast('Auto sync fail', true);
  }
  setTimeout(() => pollStatus(), 300);
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
    whitelist_enabled: !!document.getElementById('f-whitelist')?.checked,
    strict_sync_gate: !!document.getElementById('f-strict-sync')?.checked,
    auto_world_before_start: !!document.getElementById('f-auto-world')?.checked,
    http_lock_enabled: true,
    firebase_url: (document.getElementById('f-firebase')?.value || '').trim(),
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

let inviteCache = { invite: '', invite_code: '', device_id: '' };

let depsPollGen = 0;

async function installDeps() {
  const el = document.getElementById('deps-banner');
  if (el) {
    el.classList.remove('hidden');
    el.textContent = '⏳ Syncthing install ho raha hai (1-2 min)...';
  }
  try {
    const d = await post('/deps/install', {});
    toast(d.msg || 'Install shuru...', false);
    pollDepsUntilReady(++depsPollGen);
  } catch (e) {
    toast('App connect nahi — host_manager chal raha hai?', true);
  }
}

function pollDepsUntilReady(gen) {
  let n = 0;
  const tick = async () => {
    if (gen !== depsPollGen) return;
    n += 1;
    try {
      const r = await fetch('/deps/status');
      const d = await r.json();
      renderDeps(d);
      if (d.syncthing_running) {
        toast('Syncthing ready ✓');
        pollStatus();
        loadInvite();
        return;
      }
      if (!d.installing && d.last_error && n > 2) {
        toast('Syncthing fail — LAN world download use karo (neeche)', true);
        return;
      }
    } catch (e) {}
    if (n < 60) setTimeout(tick, 2000);
    else toast('Syncthing slow — LAN download try karo', true);
  };
  setTimeout(tick, 1500);
}

function renderDeps(deps) {
  const el = document.getElementById('deps-banner');
  if (!el || !deps) return;
  if (deps.installing) {
    el.classList.remove('hidden');
    el.textContent = '⏳ Dependencies install ho rahi hain (Syncthing / Java)...';
    return;
  }
  const missing = [];
  if (!deps.syncthing_running) missing.push('Syncthing');
  if (!deps.java_installed) missing.push('Java');
  if (!deps.requests_ok) missing.push('requests');
  if (!missing.length) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }
  el.classList.remove('hidden');
  const err = (deps.last_error || '').trim();
  const hint = err ? ` ${err}` : '';
  el.innerHTML =
    `⚠ Missing: ${missing.join(', ')}.${hint} ` +
    `<button type="button" class="btn-linkish" onclick="installDeps()">Ab install karo</button>`;
}

async function pullWorldLan() {
  if (state.running || state.task?.running) {
    toast('Pehle apna server STOP karo', true);
    return;
  }
  const hostIp = (document.getElementById('lan-host-ip')?.value || '').trim();
  if (!hostIp) {
    toast('Host ka IP likho (Fedora wale PC ka)', true);
    return;
  }
  const sid = state.server_id || document.getElementById('hero-server-id')?.textContent || '';
  if (!sid || sid === '—') {
    toast('Pehle Join friend se host ka Server ID set karo', true);
    return;
  }
  toast('World download ho rahi hai...');
  try {
    const d = await post('/sync/world/lan/pull', { host_ip: hostIp, server_id: sid });
    toast(d.msg || (d.ok ? 'World aa gayi!' : 'Failed'), !d.ok);
    if (d.ok) pollStatus();
  } catch (e) {
    toast('Download fail — same WiFi? Host STOP?', true);
  }
}

function renderSyncthing(d) {
  const devEl = document.getElementById('st-device-id');
  const peersEl = document.getElementById('st-peers');
  if (!devEl) return;

  const did = d.syncthing_device_id || '';
  devEl.textContent = did || 'Syncthing not running';
  inviteCache.device_id = did;

  const peers = Array.isArray(d.syncthing_peers) ? d.syncthing_peers : [];
  if (peersEl) {
    const syncRaw = String(d.syncthing_status || '').toLowerCase();
    const deps = d.deps || {};
    if (!deps.syncthing_running) {
      peersEl.textContent = 'File sync: Syncthing off — neeche "Host se world download" (LAN) use karo';
    } else if (syncRaw === 'connected') {
      peersEl.textContent = 'File sync: connected ✓';
    } else if (!peers.length) {
      peersEl.textContent = 'File sync: host ka invite Join mein paste karo (MCHOST:...)';
    } else {
      peersEl.textContent = 'File sync: ' + peers.filter((p) => p.connected).length + ' peer(s) online';
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
    inviteCache.invite_code = d.invite_code || d.invite || '';
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
  if (!inviteCache.invite_code) {
    await loadInvite();
    if (!inviteCache.invite_code && state.invite_code) {
      inviteCache.invite_code = state.invite_code;
    }
  }
  const text = inviteCache.invite_code || inviteCache.invite;
  if (!text) {
    toast('Pehle Create/Join karo — Server ID chahiye', true);
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast('Invite copied — friend Join friend se paste kare');
  } catch (e) {
    toast('Copy failed', true);
  }
}

async function applyInvite() {
  const raw = (document.getElementById('join-input')?.value || '').trim();
  if (!raw) {
    toast('Server ID likho', true);
    return;
  }
  await joinFriend();
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
