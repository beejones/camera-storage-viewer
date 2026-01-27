function $(id) { return document.getElementById(id); }

const state = {
  cameraId: null,
  date: null,
  token: null,
  clips: [],
};

function authHeaders() {
  const headers = {};
  if (state.token) {
    headers['Authorization'] = `Bearer ${state.token}`;
  }
  return headers;
}

async function apiGetJson(url) {
  const resp = await fetch(url, { headers: authHeaders() });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`);
  }
  return resp.json();
}

function fmtTs(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toISOString().replace('T', ' ').replace('Z', 'Z');
  } catch {
    return String(iso);
  }
}

function setEmptyStateVisible(visible) {
  $('emptyState').style.display = visible ? 'grid' : 'none';
}

function setSelectedCamera(cameraId) {
  state.cameraId = cameraId;
  for (const el of document.querySelectorAll('.cameraItem')) {
    el.classList.toggle('cameraItem--active', el.dataset.cameraId === cameraId);
  }
}

function timelineXFor(clipStartIso) {
  // Map the clip start time to [0..1] within the selected day (UTC).
  const start = new Date(`${state.date}T00:00:00.000Z`).getTime();
  const end = new Date(`${state.date}T23:59:59.999Z`).getTime();
  const t = new Date(clipStartIso).getTime();
  const frac = (t - start) / (end - start);
  return Math.max(0, Math.min(1, frac));
}

function renderTimeline() {
  const bar = $('timelineBar');
  bar.innerHTML = '';

  const width = bar.clientWidth;
  const minBlockPx = 18;

  for (const clip of state.clips) {
    const x = timelineXFor(clip.start_time);

    const block = document.createElement('div');
    block.className = 'clipBlock';
    block.style.left = `${Math.floor(x * width)}px`;
    block.style.width = `${minBlockPx}px`;
    block.dataset.clipId = clip.clip_id;
    block.dataset.startTime = clip.start_time;

    block.addEventListener('click', () => {
      const player = $('player');
      player.src = `/media/${clip.clip_id}`;
      player.play().catch(() => {});
      setEmptyStateVisible(false);
    });

    block.addEventListener('mousemove', (e) => showTooltip(e, clip));
    block.addEventListener('mouseleave', hideTooltip);

    bar.appendChild(block);
  }
}

function showTooltip(evt, clip) {
  const tt = $('tooltip');
  const ttTime = $('tooltipTime');
  const ttImg = $('tooltipImg');

  tt.hidden = false;
  ttTime.textContent = fmtTs(clip.start_time);

  // Lazy-load thumbnail only when visible.
  if (clip.has_thumbnail) {
    const url = `/api/clips/${clip.clip_id}/thumbnail?size=small`;
    if (ttImg.dataset.src !== url) {
      ttImg.dataset.src = url;
      ttImg.src = url;
    }
  } else {
    ttImg.removeAttribute('src');
    ttImg.dataset.src = '';
  }

  const x = Math.min(window.innerWidth - 240, evt.clientX + 14);
  const y = Math.min(window.innerHeight - 200, evt.clientY + 14);
  tt.style.left = `${x}px`;
  tt.style.top = `${y}px`;
}

function hideTooltip() {
  $('tooltip').hidden = true;
}

async function loadCameras() {
  const list = $('cameraList');
  list.innerHTML = '';

  const cameras = await apiGetJson('/api/cameras');

  for (const cam of cameras) {
    const item = document.createElement('div');
    item.className = 'cameraItem';
    item.dataset.cameraId = cam.camera_id;

    const left = document.createElement('div');
    left.className = 'cameraItem__left';

    const name = document.createElement('div');
    name.className = 'cameraItem__name';
    name.textContent = cam.display_name || cam.camera_id;

    const meta = document.createElement('div');
    meta.className = 'cameraItem__meta';
    meta.textContent = cam.last_upload_at ? `last: ${fmtTs(cam.last_upload_at)}` : 'no uploads';

    left.appendChild(name);
    left.appendChild(meta);

    const dot = document.createElement('div');
    dot.className = 'statusDot' + (cam.last_upload_at ? ' statusDot--good' : '');

    item.appendChild(left);
    item.appendChild(dot);

    item.addEventListener('click', async () => {
      setSelectedCamera(cam.camera_id);
      $('cameraTitle').textContent = cam.display_name || cam.camera_id;
      $('cameraMeta').textContent = cam.last_upload_at ? `Last upload: ${fmtTs(cam.last_upload_at)}` : '';
      await loadClips();
    });

    list.appendChild(item);
  }
}

async function loadClips() {
  const cameraId = state.cameraId;
  if (!cameraId || !state.date) {
    state.clips = [];
    renderTimeline();
    return;
  }

  const url = `/api/cameras/${encodeURIComponent(cameraId)}/clips?date=${encodeURIComponent(state.date)}`;
  const clips = await apiGetJson(url);

  state.clips = clips;
  renderTimeline();

  if (clips.length === 0) {
    setEmptyStateVisible(true);
  } else {
    setEmptyStateVisible(false);
  }
}

function init() {
  const todayUtc = new Date().toISOString().slice(0, 10);

  state.date = todayUtc;
  $('dateInput').value = todayUtc;

  const savedToken = localStorage.getItem('viewer_token') || '';
  state.token = savedToken;
  $('tokenInput').value = savedToken;

  $('tokenInput').addEventListener('input', (e) => {
    state.token = e.target.value;
    localStorage.setItem('viewer_token', state.token);
    // refresh when token changes
    loadCameras().then(loadClips).catch(console.error);
  });

  $('dateInput').addEventListener('change', (e) => {
    state.date = e.target.value;
    loadClips().catch(console.error);
  });

  $('refreshBtn').addEventListener('click', () => {
    loadCameras().then(loadClips).catch(console.error);
  });

  window.addEventListener('resize', () => renderTimeline());

  loadCameras().catch(console.error);
}

document.addEventListener('DOMContentLoaded', init);
