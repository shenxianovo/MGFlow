// ── IR Data (injected by compiler via window.__MG_IR_DATA__) ──
const IR = window.__MG_IR_DATA__;

// ── Player Engine ──
const container = document.getElementById('mg-container');
const subtitleEl = document.getElementById('mg-subtitle');
const progressBar = document.getElementById('mg-progress-bar');
const timeDisplay = document.getElementById('mg-time');
const btnPlay = document.getElementById('btn-play');

const totalDuration = IR.total_duration || 30;
let currentTime = 0;
let playing = true;
let lastTimestamp = null;
let activeSceneIdx = -1;
let animationStates = new Map();

// ── Scene bar ──
const sceneBar = document.getElementById('mg-scene-bar');
IR.scenes.forEach((scene, i) => {
  const dot = document.createElement('span');
  dot.className = 'scene-dot';
  dot.dataset.idx = i;
  const duration = (scene.end_time - scene.start_time) || 1;
  dot.style.width = (duration / totalDuration * 100) + '%';
  dot.textContent = i + 1;
  dot.addEventListener('click', () => {
    currentTime = scene.start_time + 0.01;
    resetAllElements();
    if (!playing) { playing = true; btnPlay.textContent = '⏸'; lastTimestamp = null; requestAnimationFrame(render); }
  });
  sceneBar.appendChild(dot);
});
const sceneDots = sceneBar.querySelectorAll('.scene-dot');

// ── Build DOM from IR ──
const sceneDOMs = [];
IR.scenes.forEach((scene, si) => {
  const sceneDiv = document.createElement('div');
  sceneDiv.className = 'mg-scene';
  sceneDiv.dataset.sceneId = scene.scene_id;

  scene.elements.forEach(el => {
    const elDiv = document.createElement('div');
    elDiv.className = `mg-element type-${el.type}`;
    elDiv.dataset.elementId = el.id;
    elDiv.dataset.sceneIdx = si;

    applyPosition(elDiv, el.position, el.size, el.type);

    if (el.type === 'background' || el.type === 'image' || el.type === 'icon') {
      if (el.src) {
        const img = document.createElement('img');
        img.src = el.src;
        img.alt = el.id;
        img.draggable = false;
        elDiv.appendChild(img);
      }
    } else if (el.type === 'text') {
      elDiv.textContent = el.content || '';
      if (el.text_style) elDiv.classList.add(`text-${el.text_style}`);
    }
    // shape is just a styled div

    if (el.style) {
      Object.entries(el.style).forEach(([k, v]) => { elDiv.style[k] = v; });
    }

    sceneDiv.appendChild(elDiv);
  });

  container.appendChild(sceneDiv);
  sceneDOMs.push(sceneDiv);
});

// ── Audio (optional) ──
let audioEl = null;
if (IR.audio_path) {
  audioEl = new Audio(IR.audio_path);
  audioEl.preload = 'auto';
}

// ── Position helper ──
function snapCenter(val) {
  if (typeof val !== 'string' || !val.endsWith('%')) return val;
  const n = parseFloat(val);
  if (isNaN(n)) return val;
  if (n >= 45 && n <= 55) return '50%';
  return val;
}

function applyPosition(div, pos, size, elType) {
  if (!pos) return;
  const x = pos.x || '0';
  const y = pos.y || '0';

  if (size) {
    if (size.width) div.style.width = size.width;
    if (size.height) div.style.height = size.height;
  }

  if (elType === 'background' || elType === 'text' || elType === 'shape') {
    div.style.left = x;
    div.style.top = y;
    return;
  }

  const xMap = { 'center': '50%', 'left': '5%', 'right': '95%' };
  const yMap = { 'center': '50%', 'top': '5%', 'bottom': '95%' };
  const resolvedX = snapCenter(xMap[x] || x);
  const resolvedY = snapCenter(yMap[y] || y);

  div.style.left = resolvedX;
  div.style.top = resolvedY;
  div.style.transform = 'translate(-50%, -50%)';
}

// ── Camera movement ──
function applyCamera(sceneDiv, camera, progress) {
  if (!camera || camera === 'none') { sceneDiv.style.transform = ''; return; }
  const p = Math.min(1, Math.max(0, progress));
  switch (camera) {
    case 'ken-burns': { const s = 1 + 0.08*p; sceneDiv.style.transform = `scale(${s}) translate(${-10*p}px, ${-5*p}px)`; break; }
    case 'pan-left':  sceneDiv.style.transform = `translateX(${-30*p}px)`; break;
    case 'pan-right': sceneDiv.style.transform = `translateX(${30*p}px)`; break;
    case 'pan-up':    sceneDiv.style.transform = `translateY(${-30*p}px)`; break;
    case 'pan-down':  sceneDiv.style.transform = `translateY(${30*p}px)`; break;
    case 'zoom-in-slow':  sceneDiv.style.transform = `scale(${1 + 0.1*p})`; break;
    case 'zoom-out-slow': sceneDiv.style.transform = `scale(${1.1 - 0.1*p})`; break;
  }
}

// ── Scene transitions ──
function applyTransition(outScene, inScene, transition, progress) {
  const p = Math.min(1, Math.max(0, progress));
  const ep = easeInOutCubic(p);
  if (!transition || transition === 'cut') {
    outScene.style.opacity = p < 0.5 ? '1' : '0';
    inScene.style.opacity = p < 0.5 ? '0' : '1';
    return;
  }
  switch (transition) {
    case 'crossfade':
      outScene.style.opacity = String(1 - ep);
      inScene.style.opacity = String(ep);
      break;
    case 'slide-left':
      outScene.style.opacity = '1'; outScene.style.transform = `translateX(${-100*ep}%)`;
      inScene.style.opacity = '1'; inScene.style.transform = `translateX(${100*(1-ep)}%)`;
      break;
    case 'slide-right':
      outScene.style.opacity = '1'; outScene.style.transform = `translateX(${100*ep}%)`;
      inScene.style.opacity = '1'; inScene.style.transform = `translateX(${-100*(1-ep)}%)`;
      break;
    case 'wipe-left': case 'wipe-right': case 'wipe-up': case 'wipe-down': {
      outScene.style.opacity = '1'; inScene.style.opacity = '1';
      const dir = transition.replace('wipe-', '');
      const clipMap = { left: `inset(0 ${100*ep}% 0 0)`, right: `inset(0 0 0 ${100*ep}%)`, up: `inset(0 0 ${100*ep}% 0)`, down: `inset(${100*ep}% 0 0 0)` };
      outScene.style.clipPath = clipMap[dir] || '';
      inScene.style.clipPath = '';
      break;
    }
    case 'zoom-through':
      outScene.style.opacity = String(1 - ep); outScene.style.transform = `scale(${1 + 2*ep})`;
      inScene.style.opacity = String(ep); inScene.style.transform = `scale(${1 - 0.3*(1-ep)})`;
      break;
    default:
      outScene.style.opacity = String(1 - ep);
      inScene.style.opacity = String(ep);
  }
}

// ── Animation engine ──
function baseTransform(elDiv) {
  if (elDiv.classList.contains('type-background') || elDiv.classList.contains('type-text') || elDiv.classList.contains('type-shape')) return '';
  return 'translate(-50%, -50%)';
}

function animateElement(elDiv, elData, sceneStartTime, now) {
  const delay = (elData.animation_delay || 0);
  const duration = (elData.animation_duration || 0.6);
  const animStart = sceneStartTime + delay;
  const elapsed = now - animStart;
  if (elapsed < 0) { elDiv.style.opacity = '0'; return; }
  const anim = elData.animation || 'none';
  const bt = baseTransform(elDiv);
  if (anim === 'none') { elDiv.style.opacity = '1'; return; }
  if (anim === 'custom' && elData.keyframes && elData.keyframes.length > 0) { animateKeyframes(elDiv, elData, elapsed); return; }

  const progress = Math.min(1, elapsed / duration);
  const ep = easeOutBack(progress, elData.animation_overshoot ? 1.7 : 0);
  const epSmooth = easeOutCubic(progress);
  const intensity = elData.animation_intensity || 1.0;
  const from = elData.animation_from || null;

  switch (anim) {
    case 'fade-in': elDiv.style.opacity = String(epSmooth); break;
    case 'fade-out': elDiv.style.opacity = String(1 - epSmooth); break;
    case 'slide-left': { const dist = (from === 'right' ? 100 : -100)*intensity; elDiv.style.opacity = String(epSmooth); elDiv.style.transform = `${bt} translateX(${dist*(1-ep)}px)`; break; }
    case 'slide-right': { const dist = (from === 'left' ? -100 : 100)*intensity; elDiv.style.opacity = String(epSmooth); elDiv.style.transform = `${bt} translateX(${dist*(1-ep)}px)`; break; }
    case 'slide-up': { const dist = (from === 'bottom' ? 100 : -100)*intensity; elDiv.style.opacity = String(epSmooth); elDiv.style.transform = `${bt} translateY(${dist*(1-ep)}px)`; break; }
    case 'slide-down': { const dist = (from === 'top' ? -100 : 100)*intensity; elDiv.style.opacity = String(epSmooth); elDiv.style.transform = `${bt} translateY(${dist*(1-ep)}px)`; break; }
    case 'zoom-in': elDiv.style.opacity = String(epSmooth); elDiv.style.transform = `${bt} scale(${0.3 + 0.7*ep})`; break;
    case 'zoom-out': elDiv.style.opacity = String(epSmooth); elDiv.style.transform = `${bt} scale(${1.5 - 0.5*ep})`; break;
    case 'pop': elDiv.style.opacity = String(Math.min(1, progress*3)); elDiv.style.transform = `${bt} scale(${ep})`; break;
    case 'bounce': {
      const bounceP = easeOutBounce(progress);
      elDiv.style.opacity = String(Math.min(1, progress*3));
      const fromDir = from || 'top';
      const axis = (fromDir === 'left' || fromDir === 'right') ? 'X' : 'Y';
      const sign = (fromDir === 'right' || fromDir === 'bottom') ? 1 : -1;
      elDiv.style.transform = `${bt} translate${axis}(${sign*80*intensity*(1-bounceP)}px)`;
      break;
    }
    case 'rotate-in': elDiv.style.opacity = String(epSmooth); elDiv.style.transform = `${bt} rotate(${-180*(1-ep)}deg) scale(${0.5+0.5*ep})`; break;
    case 'typewriter': typewriterAnimate(elDiv, elData, progress); break;
    case 'char-cascade': charCascadeAnimate(elDiv, elData, progress); break;
    case 'count-up': countUpAnimate(elDiv, elData, progress); break;
    case 'grow': {
      elDiv.style.opacity = '1';
      const growDir = from || 'bottom';
      if (growDir === 'bottom' || growDir === 'top') {
        elDiv.style.transformOrigin = growDir === 'bottom' ? 'center bottom' : 'center top';
        elDiv.style.transform = `${bt} scaleY(${epSmooth})`;
      } else {
        elDiv.style.transformOrigin = growDir === 'left' ? 'left center' : 'right center';
        elDiv.style.transform = `${bt} scaleX(${epSmooth})`;
      }
      break;
    }
    case 'float': {
      elDiv.style.opacity = String(epSmooth);
      elDiv.style.transform = `${bt} translateY(${Math.sin(elapsed*2)*8*intensity}px)`;
      break;
    }
    default: elDiv.style.opacity = '1';
  }
}

// ── Keyframe animation ──
function animateKeyframes(elDiv, elData, elapsed) {
  const kfs = elData.keyframes;
  const lastKf = kfs[kfs.length - 1];
  const totalKfTime = lastKf.time || 1;
  if (elapsed >= totalKfTime) { applyKeyframeState(elDiv, lastKf); return; }
  let ki = 0;
  for (let i = 0; i < kfs.length - 1; i++) {
    if (elapsed >= kfs[i].time && elapsed < kfs[i+1].time) { ki = i; break; }
  }
  const kfA = kfs[ki], kfB = kfs[ki+1] || kfA;
  const segDuration = (kfB.time - kfA.time) || 1;
  const t = easeInOutCubic(Math.min(1, Math.max(0, (elapsed - kfA.time) / segDuration)));
  const state = {};
  ['x','y'].forEach(prop => { if (kfA[prop] !== undefined || kfB[prop] !== undefined) state[prop] = lerpCSS(kfA[prop], kfB[prop], t); });
  ['scale','opacity','rotate'].forEach(prop => {
    const a = kfA[prop] ?? (prop === 'scale' ? 1 : prop === 'opacity' ? 1 : 0);
    const b = kfB[prop] ?? (prop === 'scale' ? 1 : prop === 'opacity' ? 1 : 0);
    state[prop] = a + (b - a) * t;
  });
  applyInterpolatedState(elDiv, state);
}

function applyKeyframeState(div, kf) {
  applyInterpolatedState(div, { scale: kf.scale ?? 1, opacity: kf.opacity ?? 1, rotate: kf.rotate ?? 0, x: kf.x, y: kf.y });
}

function applyInterpolatedState(div, s) {
  if (s.opacity !== undefined) div.style.opacity = String(s.opacity);
  let transform = baseTransform(div);
  if (s.x !== undefined) div.style.left = snapCenter(s.x);
  if (s.y !== undefined) div.style.top = snapCenter(s.y);
  if (s.scale !== undefined && s.scale !== 1) transform += ` scale(${s.scale})`;
  if (s.rotate !== undefined && s.rotate !== 0) transform += ` rotate(${s.rotate}deg)`;
  div.style.transform = transform;
}

function lerpCSS(a, b, t) {
  if (a === undefined) return b;
  if (b === undefined) return a;
  const aNum = parseFloat(a), bNum = parseFloat(b);
  if (isNaN(aNum) || isNaN(bNum)) return t < 0.5 ? a : b;
  const aUnit = String(a).replace(/[0-9.\-]/g, '') || 'px';
  return (aNum + (bNum - aNum) * t) + aUnit;
}

// ── Special text animations ──
function typewriterAnimate(div, data, progress) {
  const text = data.content || '';
  div.textContent = text.slice(0, Math.floor(text.length * progress));
  div.style.opacity = '1';
}

function charCascadeAnimate(div, data, progress) {
  const text = data.content || '';
  if (!div._charSpans) {
    div.innerHTML = '';
    div._charSpans = text.split('').map(ch => {
      const span = document.createElement('span');
      span.textContent = ch;
      span.style.display = 'inline-block';
      span.style.opacity = '0';
      span.style.transform = 'translateY(-30px)';
      span.style.transition = 'none';
      div.appendChild(span);
      return span;
    });
  }
  div.style.opacity = '1';
  const stagger = 1 / Math.max(1, div._charSpans.length);
  div._charSpans.forEach((span, i) => {
    const cp = easeOutCubic(Math.max(0, Math.min(1, (progress - i*stagger) / stagger)));
    span.style.opacity = String(cp);
    span.style.transform = `translateY(${-30*(1-cp)}px)`;
  });
}

function countUpAnimate(div, data, progress) {
  const target = parseFloat(data.content) || 0;
  div.textContent = Math.round(target * easeOutCubic(progress)).toLocaleString();
  div.style.opacity = '1';
}

// ── Easing functions ──
function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
function easeInOutCubic(t) { return t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t+2, 3)/2; }
function easeOutBack(t, overshoot) {
  if (!overshoot) return easeOutCubic(t);
  const c1 = overshoot, c3 = c1 + 1;
  return 1 + c3 * Math.pow(t-1, 3) + c1 * Math.pow(t-1, 2);
}
function easeOutBounce(t) {
  const n1 = 7.5625, d1 = 2.75;
  if (t < 1/d1) return n1*t*t;
  if (t < 2/d1) return n1*(t-=1.5/d1)*t+0.75;
  if (t < 2.5/d1) return n1*(t-=2.25/d1)*t+0.9375;
  return n1*(t-=2.625/d1)*t+0.984375;
}

// ── Main render loop ──
const TRANSITION_DURATION = 0.8;

function findSceneIndex(time) {
  for (let i = IR.scenes.length - 1; i >= 0; i--) {
    if (time >= IR.scenes[i].start_time) return i;
  }
  return 0;
}

function render(now) {
  if (!playing) return;
  if (lastTimestamp === null) lastTimestamp = now;
  const dt = (now - lastTimestamp) / 1000;
  lastTimestamp = now;
  currentTime += dt;

  if (currentTime >= totalDuration) { currentTime = 0; resetAllElements(); }

  const sceneIdx = findSceneIndex(currentTime);
  const scene = IR.scenes[sceneIdx];
  const prevIdx = sceneIdx - 1;
  const transitionActive = prevIdx >= 0 && (currentTime - scene.start_time) < TRANSITION_DURATION;

  sceneDOMs.forEach((sd, i) => {
    if (transitionActive && (i === prevIdx || i === sceneIdx)) { /* transition in progress */ }
    else if (i === sceneIdx) { sd.classList.add('active'); sd.style.opacity = '1'; sd.style.transform = ''; sd.style.clipPath = ''; }
    else { sd.classList.remove('active'); sd.style.opacity = '0'; sd.style.transform = ''; sd.style.clipPath = ''; }
  });

  if (transitionActive) {
    const tp = (currentTime - scene.start_time) / TRANSITION_DURATION;
    sceneDOMs[prevIdx].classList.add('active');
    sceneDOMs[sceneIdx].classList.add('active');
    applyTransition(sceneDOMs[prevIdx], sceneDOMs[sceneIdx], IR.scenes[prevIdx].transition_to_next || 'cut', tp);
  }

  if (!transitionActive && scene.camera) {
    const sceneDuration = (scene.end_time - scene.start_time) || 1;
    applyCamera(sceneDOMs[sceneIdx], scene.camera, (currentTime - scene.start_time) / sceneDuration);
  }

  scene.elements.forEach((elData, ei) => { const elDiv = sceneDOMs[sceneIdx].children[ei]; if (elDiv) animateElement(elDiv, elData, scene.start_time, currentTime); });
  if (transitionActive && prevIdx >= 0) {
    IR.scenes[prevIdx].elements.forEach((elData, ei) => { const elDiv = sceneDOMs[prevIdx].children[ei]; if (elDiv) animateElement(elDiv, elData, IR.scenes[prevIdx].start_time, currentTime); });
  }

  if (scene.subtitle) { subtitleEl.textContent = scene.subtitle; subtitleEl.classList.add('visible'); }
  else { subtitleEl.classList.remove('visible'); }

  if (sceneIdx !== activeSceneIdx) { sceneDots.forEach((d, i) => d.classList.toggle('active', i === sceneIdx)); activeSceneIdx = sceneIdx; }

  progressBar.style.width = (currentTime / totalDuration * 100) + '%';
  timeDisplay.textContent = formatTime(currentTime) + ' / ' + formatTime(totalDuration);
  requestAnimationFrame(render);
}

function resetAllElements() {
  container.querySelectorAll('.mg-element').forEach(el => { el.style.opacity = '0'; el.style.transform = baseTransform(el); el.style.clipPath = ''; if (el._charSpans) el._charSpans = null; });
  sceneDOMs.forEach(sd => { sd.classList.remove('active'); sd.style.opacity = '0'; sd.style.transform = ''; sd.style.clipPath = ''; });
}

function formatTime(s) { const m = Math.floor(s / 60); return m + ':' + String(Math.floor(s % 60)).padStart(2, '0'); }

// ── Controls ──
btnPlay.addEventListener('click', () => {
  playing = !playing;
  btnPlay.textContent = playing ? '⏸' : '▶';
  if (playing) { lastTimestamp = null; requestAnimationFrame(render); }
});

function seekTo(e) {
  const rect = e.currentTarget.getBoundingClientRect();
  currentTime = ((e.clientX - rect.left) / rect.width) * totalDuration;
  resetAllElements();
  if (!playing) { playing = true; btnPlay.textContent = '⏸'; lastTimestamp = null; requestAnimationFrame(render); }
}

// ── Audio sync ──
if (audioEl) {
  audioEl.play().catch(() => {});
  setInterval(() => { if (playing && audioEl && Math.abs(audioEl.currentTime - currentTime) > 0.3) audioEl.currentTime = currentTime; }, 1000);
}

// ── Start ──
requestAnimationFrame(render);

window.seekToScene = function(idx) {
  if (idx >= 0 && idx < IR.scenes.length) {
    currentTime = IR.scenes[idx].start_time + 0.01;
    resetAllElements();
    if (!playing) { playing = true; btnPlay.textContent = '⏸'; lastTimestamp = null; requestAnimationFrame(render); }
  }
};

function fitToWindow() {
  const wrapper = document.getElementById('mg-wrapper');
  const sx = window.innerWidth / 1280;
  const sy = window.innerHeight / 760;
  const s = Math.min(sx, sy, 1);
  wrapper.style.transform = 'scale(' + s + ')';
}
window.addEventListener('resize', fitToWindow);
fitToWindow();
