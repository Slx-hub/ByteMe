'use strict';

/* PicToBytes editor.
 *
 * The viewport holds two stacked layers over the same 800x480 frame:
 *   - a canvas drawing the source image with the current pan/zoom, filtered in
 *     CSS so grading feels instant while you drag;
 *   - an <img> holding the dithered render from the server, which fades in once
 *     it arrives and is hidden again the moment you touch anything.
 * Because both layers are the same frame, switching between them is a clean A/B.
 */

const FRAME_W = 800;
const FRAME_H = 480;
const PREVIEW_DEBOUNCE = 260;

const state = {
  images: [],
  palette: [],
  defaults: {},
  config: {},
  selected: null,
  settings: null,
  sourceImage: null,
  view: 'result',
  previewToken: 0,
  previewBusy: false,
  previewQueued: false,
  saveTimer: null,
  previewTimer: null,
  cards: new Map(),
};

const el = (id) => document.getElementById(id);
const canvas = el('canvas');
const ctx = canvas.getContext('2d');

/* ----------------------------------------------------------------- utils */

function toast(message, kind) {
  const node = document.createElement('div');
  node.className = 'toast' + (kind ? ' ' + kind : '');
  node.textContent = message;
  el('toasts').appendChild(node);
  setTimeout(() => node.remove(), kind === 'error' ? 6000 : 2800);
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).error || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

const postJSON = (path, body) => api(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});

function busy(on) {
  el('busy').hidden = !on;
}

function current() {
  return state.images.find((image) => image.name === state.selected) || null;
}

/* --------------------------------------------------------------- gallery */

function thumbUrl(image) {
  return image.rendered
    ? `/api/image/${encodeURIComponent(image.name)}/render?v=${image.rendered_at || 0}`
    : `/api/image/${encodeURIComponent(image.name)}/source`;
}

function renderGallery() {
  const needle = el('filter').value.trim().toLowerCase();
  const grid = el('grid');
  const visible = state.images.filter((i) => !needle || i.name.toLowerCase().includes(needle));
  const seen = new Set();

  // Cards are reused across renders. Rebuilding them would re-request every
  // thumbnail, which matters because settings are saved on every slider move.
  for (const image of visible) {
    seen.add(image.name);
    let card = state.cards.get(image.name);

    if (!card) {
      card = buildCard(image);
      state.cards.set(image.name, card);
    }
    updateCard(card, image);
    grid.appendChild(card.root);       // appendChild also reorders existing nodes
  }

  for (const [name, card] of state.cards) {
    if (!seen.has(name)) { card.root.remove(); state.cards.delete(name); }
  }

  const stale = state.images.filter((i) => i.stale).length;
  const deployed = state.images.filter((i) => i.deployed).length;
  el('counts').textContent =
    `${state.images.length} images · ${stale} need rendering · ${deployed} deployed`;
}

function buildCard(image) {
  const root = document.createElement('div');
  root.className = 'card';
  root.title = image.name;
  root.onclick = () => select(image.name);

  const img = document.createElement('img');
  img.loading = 'lazy';
  img.draggable = false;
  img.alt = image.name;
  img.onerror = () => { img.style.visibility = 'hidden'; };
  img.onload = () => { img.style.visibility = 'visible'; };

  const dots = document.createElement('div');
  dots.className = 'card-dots';

  const name = document.createElement('div');
  name.className = 'card-name';
  name.textContent = image.name;

  root.append(img, dots, name);
  return { root, img, dots, src: null };
}

function updateCard(card, image) {
  card.root.classList.toggle('selected', image.name === state.selected);

  const src = thumbUrl(image);
  if (src !== card.src) { card.src = src; card.img.src = src; }

  card.dots.innerHTML = '';
  if (image.stale) card.dots.appendChild(dot('stale', 'Needs rendering'));
  if (image.deploy_outdated) card.dots.appendChild(dot('outdated', 'Deployed copy is older'));
  else if (image.deployed) card.dots.appendChild(dot('deployed', 'Deployed to GLaDOS'));
}

function dot(kind, title) {
  const node = document.createElement('span');
  node.className = 'dot ' + kind;
  node.title = title;
  return node;
}

/* ---------------------------------------------------------------- canvas */

function coverScale(image) {
  return Math.max(FRAME_W / image.naturalWidth, FRAME_H / image.naturalHeight);
}

function clampCenter() {
  const image = state.sourceImage;
  if (!image) return;
  const scale = coverScale(image) * state.settings.zoom;
  const halfX = FRAME_W / 2 / (image.naturalWidth * scale);
  const halfY = FRAME_H / 2 / (image.naturalHeight * scale);
  state.settings.cx = Math.min(Math.max(state.settings.cx, halfX), 1 - halfX);
  state.settings.cy = Math.min(Math.max(state.settings.cy, halfY), 1 - halfY);
}

function drawCanvas() {
  ctx.clearRect(0, 0, FRAME_W, FRAME_H);
  const image = state.sourceImage;
  if (!image) return;

  const settings = state.settings;
  const scale = coverScale(image) * settings.zoom;
  const width = image.naturalWidth * scale;
  const height = image.naturalHeight * scale;

  ctx.filter = `brightness(${settings.brightness}) contrast(${settings.contrast}) ` +
               `saturate(${settings.saturation})`;
  ctx.drawImage(image,
    FRAME_W / 2 - settings.cx * width,
    FRAME_H / 2 - settings.cy * height,
    width, height);
  ctx.filter = 'none';
}

/* --------------------------------------------------------------- preview */

function invalidatePreview() {
  el('preview').classList.remove('shown');
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(requestPreview, PREVIEW_DEBOUNCE);
}

async function requestPreview() {
  if (!state.selected) return;
  if (state.previewBusy) { state.previewQueued = true; return; }

  state.previewBusy = true;
  const token = ++state.previewToken;
  busy(true);

  try {
    const response = await fetch(`/api/image/${encodeURIComponent(state.selected)}/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings: state.settings }),
    });
    if (!response.ok) throw new Error(await response.text());

    const histogram = response.headers.get('X-Histogram');
    const blob = await response.blob();
    if (token !== state.previewToken) return;

    const preview = el('preview');
    const url = URL.createObjectURL(blob);
    preview.onload = () => {
      URL.revokeObjectURL(url);
      if (token === state.previewToken) applyView();
    };
    preview.src = url;
    if (histogram) drawHistogram(histogram.split(',').map(Number));
  } catch (error) {
    if (token === state.previewToken) toast('Preview failed: ' + error.message, 'error');
  } finally {
    state.previewBusy = false;
    busy(false);
    if (state.previewQueued) { state.previewQueued = false; requestPreview(); }
  }
}

function drawHistogram(fractions) {
  const bar = el('histogram');
  bar.innerHTML = '';
  fractions.forEach((fraction, index) => {
    const colour = state.palette[index];
    const cell = document.createElement('div');
    cell.style.flexGrow = String(Math.max(fraction, 0.004));
    cell.style.background = colour.hex;
    cell.style.color = index === 1 || index === 5 ? '#222' : '#eee';
    cell.title = `${colour.name}: ${(fraction * 100).toFixed(1)}%`;
    if (fraction > 0.05) cell.textContent = `${Math.round(fraction * 100)}%`;
    bar.appendChild(cell);
  });
}

/* ------------------------------------------------------------ view modes */

function applyView() {
  const preview = el('preview');
  const ready = Boolean(preview.src);
  preview.classList.toggle('clipped', state.view === 'split');
  preview.classList.toggle('shown', ready && state.view !== 'frame');
  el('split-handle').hidden = state.view !== 'split';
  document.querySelectorAll('.seg').forEach((node) => {
    node.classList.toggle('active', node.dataset.view === state.view);
  });
}

/* -------------------------------------------------------------- settings */

function syncControls() {
  const settings = state.settings;
  document.querySelectorAll('[data-key]').forEach((input) => {
    const key = input.dataset.key;
    if (!(key in settings)) return;
    if (input.type === 'checkbox') {
      input.checked = Boolean(settings[key]);
    } else {
      input.value = settings[key];
      const output = input.parentElement.querySelector('output');
      if (output) output.textContent = formatValue(key, settings[key]);
    }
  });
}

function formatValue(key, value) {
  if (key === 'green_reduce' || key === 'sharpen') return `${Math.round(value * 100)}%`;
  if (key === 'zoom') return `${Number(value).toFixed(2)}×`;
  return Number(value).toFixed(2);
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    if (!state.selected) return;
    try {
      const result = await postJSON(
        `/api/image/${encodeURIComponent(state.selected)}/settings`, state.settings);
      mergeStatus(result.status);
    } catch (error) {
      toast('Could not save settings: ' + error.message, 'error');
    }
  }, 400);
}

function onSettingChanged() {
  clampCenter();
  drawCanvas();
  syncControls();
  invalidatePreview();
  scheduleSave();
}

/* -------------------------------------------------------------- selection */

async function select(name) {
  state.selected = name;
  const image = current();
  if (!image) return;

  state.settings = { ...state.defaults, ...image.settings };
  el('empty').hidden = true;
  el('workspace').hidden = false;
  el('title').textContent = name;
  el('legacy-note').hidden = !image.legacy;
  el('viewport').classList.toggle('locked', image.legacy);

  document.querySelectorAll('.controls input').forEach((input) => {
    input.disabled = Boolean(image.legacy);
  });
  el('btn-rotate').disabled = Boolean(image.legacy);

  el('preview').classList.remove('shown');
  el('preview').removeAttribute('src');
  ctx.clearRect(0, 0, FRAME_W, FRAME_H);

  renderGallery();
  renderBadges();
  renderMeta();
  syncControls();

  const source = new Image();
  source.onload = () => {
    if (state.selected !== name) return;
    state.sourceImage = source;
    clampCenter();
    drawCanvas();
    requestPreview();
  };
  source.onerror = () => toast('Could not load ' + name, 'error');
  source.src = `/api/image/${encodeURIComponent(name)}/source`;
}

function renderBadges() {
  const image = current();
  const badges = el('badges');
  badges.innerHTML = '';
  if (!image) return;

  const add = (text, kind) => {
    const node = document.createElement('span');
    node.className = 'badge' + (kind ? ' ' + kind : '');
    node.textContent = text;
    badges.appendChild(node);
  };

  if (image.legacy) add('legacy');
  if (image.stale) add('needs render', 'warn');
  else add('rendered', 'ok');
  if (image.deploy_outdated) add('deploy outdated', 'bad');
  else if (image.deployed) add('deployed', 'ok');
}

function renderMeta() {
  const image = current();
  const meta = el('meta');
  meta.innerHTML = '';
  if (!image) return;

  const rows = [
    ['Source', image.source],
    ['Size', image.source_size ? image.source_size.join(' × ') : '—'],
    ['Rendered', image.rendered_at ? new Date(image.rendered_at * 1000).toLocaleString() : 'never'],
    ['Payload', image.has_glds ? '192000 bytes' : 'not generated'],
  ];
  for (const [term, definition] of rows) {
    meta.appendChild(Object.assign(document.createElement('dt'), { textContent: term }));
    meta.appendChild(Object.assign(document.createElement('dd'), { textContent: definition }));
  }
}

function mergeStatus(status) {
  const index = state.images.findIndex((image) => image.name === status.name);
  if (index >= 0) state.images[index] = status;
  renderGallery();
  if (status.name === state.selected) { renderBadges(); renderMeta(); }
}

/* ------------------------------------------------------- canvas gestures */

function viewportScale() {
  return FRAME_W / el('viewport').getBoundingClientRect().width;
}

function setupGestures() {
  const viewport = el('viewport');
  let dragging = false;
  let last = null;

  const editable = () => {
    const image = current();
    return image && !image.legacy && state.sourceImage;
  };

  // Without this the browser starts its own image/text drag a few pixels in
  // and the pointermove stream stops arriving.
  viewport.addEventListener('dragstart', (event) => event.preventDefault());

  viewport.addEventListener('pointerdown', (event) => {
    if (!editable()) return;
    event.preventDefault();
    dragging = true;
    last = { x: event.clientX, y: event.clientY };
    viewport.classList.add('dragging');
    viewport.setPointerCapture(event.pointerId);
    el('preview').classList.remove('shown');
    clearTimeout(state.previewTimer);
  });

  viewport.addEventListener('pointermove', (event) => {
    if (state.view === 'split' && !dragging) {
      const rect = viewport.getBoundingClientRect();
      const ratio = (event.clientX - rect.left) / rect.width * 100;
      viewport.style.setProperty('--split', `${Math.min(Math.max(ratio, 0), 100)}%`);
      return;
    }
    if (!dragging) return;

    const image = state.sourceImage;
    const toFrame = viewportScale();            // CSS px -> frame px
    const scale = coverScale(image) * state.settings.zoom;

    state.settings.cx -= (event.clientX - last.x) * toFrame / (image.naturalWidth * scale);
    state.settings.cy -= (event.clientY - last.y) * toFrame / (image.naturalHeight * scale);
    last = { x: event.clientX, y: event.clientY };

    clampCenter();
    drawCanvas();
  });

  const stop = (event) => {
    if (!dragging) return;
    dragging = false;
    viewport.classList.remove('dragging');
    try { viewport.releasePointerCapture(event.pointerId); } catch (_) {}
    onSettingChanged();
  };
  viewport.addEventListener('pointerup', stop);
  viewport.addEventListener('pointercancel', stop);

  viewport.addEventListener('wheel', (event) => {
    if (!editable()) return;
    event.preventDefault();

    const image = state.sourceImage;
    const rect = viewport.getBoundingClientRect();
    const toFrame = FRAME_W / rect.width;
    const px = (event.clientX - rect.left) * toFrame;
    const py = (event.clientY - rect.top) * toFrame;

    const before = coverScale(image) * state.settings.zoom;
    // The normalised source point currently under the cursor.
    const anchorX = state.settings.cx + (px - FRAME_W / 2) / (image.naturalWidth * before);
    const anchorY = state.settings.cy + (py - FRAME_H / 2) / (image.naturalHeight * before);

    const factor = Math.exp(-event.deltaY * 0.0015);
    state.settings.zoom = Math.min(Math.max(state.settings.zoom * factor, 1), 5);

    const after = coverScale(image) * state.settings.zoom;
    state.settings.cx = anchorX - (px - FRAME_W / 2) / (image.naturalWidth * after);
    state.settings.cy = anchorY - (py - FRAME_H / 2) / (image.naturalHeight * after);

    el('preview').classList.remove('shown');
    onSettingChanged();
  }, { passive: false });
}

/* --------------------------------------------------------------- actions */

function wireControls() {
  document.querySelectorAll('[data-key]').forEach((input) => {
    const key = input.dataset.key;
    const handler = () => {
      state.settings[key] = input.type === 'checkbox' ? input.checked : Number(input.value);
      onSettingChanged();
    };
    input.addEventListener('input', handler);
    input.addEventListener('change', handler);
  });

  document.querySelectorAll('.seg').forEach((node) => {
    node.onclick = () => { state.view = node.dataset.view; applyView(); };
  });

  el('filter').addEventListener('input', renderGallery);

  el('btn-rotate').onclick = () => {
    state.settings.rotate = (state.settings.rotate + 90) % 360;
    state.sourceImage = null;
    // Rotation changes the source dimensions, so reload it from the server.
    const name = state.selected;
    const source = new Image();
    source.onload = () => {
      if (state.selected !== name) return;
      state.sourceImage = source;
      state.settings.cx = 0.5;
      state.settings.cy = 0.5;
      onSettingChanged();
    };
    postJSON(`/api/image/${encodeURIComponent(name)}/settings`, state.settings)
      .then(() => { source.src = `/api/image/${encodeURIComponent(name)}/source?r=${Date.now()}`; })
      .catch((error) => toast(error.message, 'error'));
  };

  document.querySelector('[data-reset="tone"]').onclick = () => {
    Object.assign(state.settings, {
      brightness: state.defaults.brightness,
      contrast: state.defaults.contrast,
      saturation: state.defaults.saturation,
      sharpen: state.defaults.sharpen,
    });
    onSettingChanged();
  };

  el('btn-reset-all').onclick = () => {
    state.settings = { ...state.defaults };
    onSettingChanged();
  };

  const compare = el('btn-compare');
  const showSource = (on) => {
    el('preview').classList.toggle('shown', !on && Boolean(el('preview').src) && state.view !== 'frame');
  };
  compare.addEventListener('pointerdown', () => showSource(true));
  window.addEventListener('pointerup', () => showSource(false));
  compare.addEventListener('keydown', (e) => { if (e.key === ' ') showSource(true); });
  compare.addEventListener('keyup', () => showSource(false));

  el('btn-render').onclick = withBusy(async (button) => {
    button.textContent = 'Rendering…';
    const status = await postJSON(`/api/image/${encodeURIComponent(state.selected)}/render`);
    mergeStatus(status);
    toast(`${status.name} rendered`, 'ok');
  });

  el('btn-deploy').onclick = withBusy(async () => {
    const image = current();
    if (image.stale) await postJSON(`/api/image/${encodeURIComponent(image.name)}/render`);
    const status = await postJSON(`/api/image/${encodeURIComponent(image.name)}/deploy`);
    mergeStatus(status);
    toast(`${status.name} deployed`, 'ok');
  });

  el('btn-send').onclick = withBusy(async (button) => {
    button.textContent = 'Sending…';
    const result = await postJSON(`/api/image/${encodeURIComponent(state.selected)}/send`,
      { live: true, settings: state.settings });
    toast(`Frame responded ${result.status} (${result.bytes} bytes)`, 'ok');
  });

  el('btn-delete').onclick = withBusy(async () => {
    const name = state.selected;
    if (!confirm(`Delete ${name}, its render, its payload and the source file?`)) return;
    await api(`/api/image/${encodeURIComponent(name)}`, { method: 'DELETE' });
    state.selected = null;
    el('workspace').hidden = true;
    el('empty').hidden = false;
    await refresh();
    toast(`${name} deleted`, 'ok');
  });

  el('btn-scan').onclick = withBusy(async () => {
    const result = await postJSON('/api/scan');
    state.images = result.images;
    renderGallery();
    toast(result.added.length ? `Found ${result.added.length} new: ${result.added.join(', ')}`
                              : 'No new images', 'ok');
  });

  el('btn-render-all').onclick = withBusy(async (button) => {
    const pending = state.images.filter((image) => image.stale).length;
    if (!pending) { toast('Everything is up to date', 'ok'); return; }
    button.textContent = `Rendering ${pending}…`;
    const result = await postJSON('/api/render_all', { only_stale: true });
    state.images = result.images;
    renderGallery();
    renderBadges();
    toast(`Rendered ${result.rendered.length}` +
          (result.failed.length ? `, ${result.failed.length} failed` : ''),
          result.failed.length ? 'error' : 'ok');
  });

  el('btn-deploy-all').onclick = withBusy(async () => {
    const result = await postJSON('/api/deploy_all');
    state.images = result.images;
    renderGallery();
    toast(`Deployed ${result.deployed.length} to GLaDOS`, 'ok');
  });

  el('btn-clear').onclick = withBusy(async () => {
    const result = await postJSON('/api/device/clear', { color: 1 });
    toast(`Frame responded ${result.status}`, 'ok');
  });
}

function withBusy(handler) {
  return async function (event) {
    const button = event.currentTarget;
    const label = button.textContent;
    button.disabled = true;
    try {
      await handler(button);
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  };
}

/* ------------------------------------------------------------------ boot */

async function refresh() {
  const data = await api('/api/library');
  state.images = data.images;
  state.palette = data.palette;
  state.defaults = data.defaults;
  state.config = data.config;

  el('device-label').textContent = data.config.device_url;
  el('empty-src').textContent = data.config.source_dir + '/';
  renderGallery();
}

(async function main() {
  wireControls();
  setupGestures();
  applyView();
  try {
    await refresh();
    if (state.images.length) select(state.images[0].name);
  } catch (error) {
    toast('Could not reach the server: ' + error.message, 'error');
  }
})();
