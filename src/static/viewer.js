function $(id) { return document.getElementById(id); }

const state = {
  cameraId: null,
  date: null,
  clips: [],
  zoomPxPerHour: 240,
  playheadIso: null,
  activeClipId: null,
};

function fmtTs(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toISOString().replace('T', ' ').slice(0, 19) + 'Z';
}

async function apiGetJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const text = await resp.text();
    const err = new Error(`${resp.status} ${resp.statusText}: ${text}`);
    err.status = resp.status;
    err.bodyText = text;
    throw err;
  }
  return resp.json();
}

function setCameraStatus(message, kind) {
  const statusEl = $('cameraListStatus');
  if (!statusEl) return;
  statusEl.textContent = message || '';
  statusEl.classList.toggle('cameraListStatus--error', kind === 'error');
}

function setEmptyStateVisible(visible) {
  const el = $('emptyState');
  if (!el) return;
  el.style.display = visible ? 'grid' : 'none';
}

function setSelectedCamera(cameraId) {
  state.cameraId = cameraId;
  state.activeClipId = null;

  for (const el of document.querySelectorAll('.cameraItem')) {
    el.classList.toggle('cameraItem--active', el.dataset.cameraId === cameraId);
  }
}

function timelineWidthPx() {
  return Math.max(24 * state.zoomPxPerHour, 24 * 80);
}

function _dayStartMs() {
  return new Date(`${state.date}T00:00:00.000Z`).getTime();
}

function timelineXFor(iso) {
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return 0;
  const start = _dayStartMs();
  const end = start + 24 * 60 * 60 * 1000;
  const frac = (t - start) / Math.max(1, end - start);
  return Math.max(0, Math.min(1, frac));
}

function isoForFrac(frac) {
  const start = _dayStartMs();
  const t = start + (Math.max(0, Math.min(1, frac)) * 24 * 60 * 60 * 1000);
  return new Date(t).toISOString();
}

function fracForClientXIn(el, clientX) {
  const rect = el.getBoundingClientRect();
  const x = clientX - rect.left;
  const frac = x / Math.max(1, rect.width);
  return Math.max(0, Math.min(1, frac));
}

function chooseTickMinutes() {
  // Dynamic tick density based on zoom.
  const pxPerHour = state.zoomPxPerHour;
  if (pxPerHour >= 800) return { tick: 5, label: 60 };
  if (pxPerHour >= 360) return { tick: 10, label: 60 };
  if (pxPerHour >= 200) return { tick: 15, label: 120 };
  return { tick: 30, label: 180 };
}

function renderTimelineScale() {
  const scale = $('timelineScale');
  if (!scale) return;
  scale.innerHTML = '';

  const widthPx = timelineWidthPx();
  scale.style.width = `${widthPx}px`;

  const { tick, label } = chooseTickMinutes();
  const totalMinutes = 24 * 60;

  for (let m = 0; m <= totalMinutes; m += tick) {
    const x = (m / totalMinutes) * widthPx;

    const isHour = m % 60 === 0;
    const isLabel = m % label === 0;

    const tickEl = document.createElement('div');
    tickEl.className = 'timelineTick' + (isHour ? ' timelineTick--major' : '');
    tickEl.style.left = `${x}px`;
    scale.appendChild(tickEl);

    if (isLabel) {
      const h = Math.floor(m / 60);
      const labelEl = document.createElement('div');
      labelEl.className = 'timelineTickLabel';
      labelEl.style.left = `${x}px`;
      labelEl.textContent = `${String(h).padStart(2, '0')}:00`;
      scale.appendChild(labelEl);
    }
  }
}

function renderTimeline() {
  const scroll = $('timelineScroll');
  const inner = $('timelineInner');
  const track = $('timelineTrack');
  const film = $('timelineFilmstrip');
  if (!scroll || !inner || !track || !film) return;

  renderTimelineScale();

  track.innerHTML = '';
  film.innerHTML = '';

  const widthPx = timelineWidthPx();
  inner.style.width = `${widthPx}px`;
  track.style.width = `${widthPx}px`;
  film.style.width = `${widthPx}px`;

  const clips = [...(state.clips || [])].sort((a, b) => new Date(a.start_time) - new Date(b.start_time));

  const playClip = (clip) => {
    const player = $('player');
    state.activeClipId = clip.clip_id;
    state.playheadIso = clip.start_time;
    player.src = `/media/${clip.clip_id}`;
    player.play().catch(() => {});
    setEmptyStateVisible(false);
    renderTimeline();
  };

  const downloadClip = (clip) => {
    const a = document.createElement('a');
    a.href = `/media/${clip.clip_id}`;
    a.download = `${clip.camera_id || 'camera'}_${clip.clip_id}.mp4`;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  // Segments on the track.
  for (const clip of clips) {
    const frac = timelineXFor(clip.start_time);
    const left = frac * widthPx;
    const dur = (clip.duration_seconds && Number.isFinite(clip.duration_seconds)) ? clip.duration_seconds : 60;
    const w = Math.max(6, Math.min(120, (dur / 3600) * state.zoomPxPerHour));

    const seg = document.createElement('div');
    seg.className = 'timelineSegment';
    seg.style.left = `${left}px`;
    seg.style.width = `${w}px`;
    seg.dataset.clipId = clip.clip_id;

    seg.addEventListener('click', () => playClip(clip));
    seg.addEventListener('mousemove', (e) => showTooltip(e, clip));
    seg.addEventListener('mouseleave', hideTooltip);
    seg.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      downloadClip(clip);
    });

    track.appendChild(seg);
  }

  // Playhead.
  const playhead = document.createElement('div');
  playhead.className = 'timelinePlayhead';
  const playheadFrac = state.playheadIso ? timelineXFor(state.playheadIso) : 0;
  playhead.style.left = `${playheadFrac * widthPx}px`;
  track.appendChild(playhead);

  // Click/drag to scrub.
  let dragging = false;
  const setPlayheadFromClientX = (clientX) => {
    const frac = fracForClientXIn(track, clientX);
    state.playheadIso = isoForFrac(frac);
    playhead.style.left = `${frac * widthPx}px`;
  };

  track.onpointerdown = (e) => {
    dragging = true;
    track.setPointerCapture(e.pointerId);
    setPlayheadFromClientX(e.clientX);
  };
  track.onpointermove = (e) => {
    if (!dragging) return;
    setPlayheadFromClientX(e.clientX);
  };
  track.onpointerup = (e) => {
    if (!dragging) return;
    dragging = false;
    track.releasePointerCapture(e.pointerId);

    // Snap to closest clip at/before playhead.
    const t = new Date(state.playheadIso).getTime();
    let best = null;
    for (const c of clips) {
      const ct = new Date(c.start_time).getTime();
      if (ct <= t) best = c;
      else break;
    }
    if (!best && clips.length) best = clips[0];
    if (best) playClip(best);
  };

  // Filmstrip thumbnails, positioned on the same time scale as the track.
  // We do a simple lane packing so thumbnails don't overlap.
  const itemWidthPx = 130;
  const itemGapPx = 10;
  const laneHeightPx = 96;
  const laneLastRight = [];

  for (const clip of clips) {
    const frac = timelineXFor(clip.start_time);
    let leftPx = Math.round(frac * widthPx);
    leftPx = Math.max(0, Math.min(leftPx, Math.max(0, widthPx - itemWidthPx)));
    const rightPx = leftPx + itemWidthPx;

    let lane = 0;
    while (lane < laneLastRight.length) {
      if (leftPx >= laneLastRight[lane] + itemGapPx) break;
      lane += 1;
    }
    if (lane === laneLastRight.length) laneLastRight.push(-Infinity);
    laneLastRight[lane] = Math.max(laneLastRight[lane], rightPx);

    const item = document.createElement('div');
    item.className = 'filmItem' + (clip.clip_id === state.activeClipId ? ' filmItem--active' : '');
    item.dataset.clipId = clip.clip_id;
    item.style.left = `${leftPx}px`;
    item.style.top = `${lane * laneHeightPx}px`;

    const thumb = document.createElement('div');
    thumb.className = 'filmItem__thumb';
    if (clip.has_thumbnail) {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.alt = '';
      img.src = `/api/clips/${clip.clip_id}/thumbnail?size=small`;
      img.onerror = () => {
        try { img.remove(); } catch (_) {}
      };
      thumb.appendChild(img);
    }

    const meta = document.createElement('div');
    meta.className = 'filmItem__meta';
    const t = new Date(clip.start_time);
    meta.textContent = `${String(t.getUTCHours()).padStart(2, '0')}:${String(t.getUTCMinutes()).padStart(2, '0')}:${String(t.getUTCSeconds()).padStart(2, '0')}`;

    item.appendChild(thumb);
    item.appendChild(meta);

    item.addEventListener('click', () => playClip(clip));
    item.addEventListener('mousemove', (e) => showTooltip(e, clip));
    item.addEventListener('mouseleave', hideTooltip);
    item.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      downloadClip(clip);
    });

    film.appendChild(item);
  }

  const lanes = Math.max(1, laneLastRight.length);
  film.style.height = `${lanes * laneHeightPx}px`;
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
  setCameraStatus('Loading cameras…', 'info');

  let cameras;
  try {
    cameras = await apiGetJson('/api/cameras');
  } catch (e) {
    setCameraStatus(`Failed to load cameras: ${e && e.message ? e.message : String(e)}`, 'error');
    throw e;
  }

  if (!Array.isArray(cameras) || cameras.length === 0) {
    setCameraStatus('No cameras found yet.', 'info');
    return;
  }

  setCameraStatus('', 'info');

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

  $('dateInput').addEventListener('change', (e) => {
    state.date = e.target.value;
    loadClips().catch(console.error);
  });

  $('refreshBtn').addEventListener('click', () => {
    loadCameras().then(loadClips).catch(console.error);
  });

  const zoom = $('timelineZoom');
  const scaleSelect = $('timelineScaleSelect');
  if (zoom) {
    zoom.value = String(state.zoomPxPerHour);
    zoom.addEventListener('input', (e) => {
      state.zoomPxPerHour = Number(e.target.value) || 240;
      if (scaleSelect) {
        scaleSelect.value = String(state.zoomPxPerHour);
      }
      renderTimeline();
    });
  }

  if (scaleSelect) {
    scaleSelect.value = String(state.zoomPxPerHour);
    scaleSelect.addEventListener('change', (e) => {
      state.zoomPxPerHour = Number(e.target.value) || 240;
      if (zoom) {
        zoom.value = String(state.zoomPxPerHour);
      }
      renderTimeline();
    });
  }

  window.addEventListener('resize', () => renderTimeline());

  loadCameras().catch(console.error);
}

document.addEventListener('DOMContentLoaded', init);
