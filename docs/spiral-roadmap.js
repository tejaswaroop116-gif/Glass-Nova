/* Glass Nova — Integrated 3D Spiral Roadmap (adapted from nova_spiral_roadmap_updated.html) */
import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js';

const journey = document.getElementById('sr-journey');
const sticky = document.getElementById('sr-sticky');
const container = document.getElementById('sr-stage');
if (!journey || !sticky || !container) {
  // Roadmap section not present on page
} else {
  const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const perfCfg = window.__novaPerf ? window.__novaPerf.getConfig() : null;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#03050a');
  scene.fog = new THREE.FogExp2(0x03050a, 0.018);

  const getSize = () => ({
    w: Math.max(1, container.clientWidth || window.innerWidth),
    h: Math.max(1, container.clientHeight || window.innerHeight)
  });

  let { w: stageW, h: stageH } = getSize();
  const camera = new THREE.PerspectiveCamera(48, stageW / stageH, 0.1, 500);
  const renderer = new THREE.WebGLRenderer({
    antialias: perfCfg ? !!perfCfg.antialias : window.innerWidth >= 900,
    powerPreference: perfCfg && perfCfg.tier === 'low' ? 'low-power' : 'high-performance'
  });
  const maxRoadmapDpr = perfCfg ? perfCfg.maxDpr : (window.innerWidth < 700 ? 1.1 : 1.25);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxRoadmapDpr));
  renderer.setSize(stageW, stageH);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  container.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0x8dbbff, 0.65));
  const key = new THREE.PointLight(0x3acbff, 120, 140);
  scene.add(key);
  const fill = new THREE.PointLight(0x7054ff, 85, 130);
  scene.add(fill);

  // Starfield (adaptive density by performance tier)
  const starCount = perfCfg ? perfCfg.roadmapStarCount : (window.innerWidth < 700 ? 500 : 1000);
  const starGeo = new THREE.BufferGeometry();
  const starPos = [];
  for (let i = 0; i < starCount; i++) {
    starPos.push((Math.random() - 0.5) * 130, (Math.random() - 0.5) * 100, (Math.random() - 0.5) * 160);
  }
  starGeo.setAttribute('position', new THREE.Float32BufferAttribute(starPos, 3));
  scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xbcdcff, size: 0.085, transparent: true, opacity: 0.65 })));

  // Soft distant color haze
  const haze = new THREE.Group();
  for (let i = 0; i < 7; i++) {
    const hMesh = new THREE.Mesh(
      new THREE.SphereGeometry(9 + i * 2, 16, 12),
      new THREE.MeshBasicMaterial({ color: [0x12345b, 0x24144b, 0x0b4557][i % 3], transparent: true, opacity: 0.035, depthWrite: false })
    );
    hMesh.position.set((i - 3) * 10, (i - 3) * 7, -28 - i * 7);
    haze.add(hMesh);
  }
  scene.add(haze);

  // Five-waypoint 3D spiral track
  const turns = 2.65, height = 34, radius = 5.3, curvePoints = [];
  for (let i = 0; i <= 1200; i++) {
    const t = i / 1200;
    const a = t * Math.PI * 2 * turns;
    curvePoints.push(new THREE.Vector3(Math.cos(a) * radius, t * height - height / 2, Math.sin(a) * radius));
  }
  const tubeSegs = perfCfg ? perfCfg.roadmapTubeSegs : 320;
  const curve = new THREE.CatmullRomCurve3(curvePoints);
  const trackBase = new THREE.Mesh(new THREE.TubeGeometry(curve, tubeSegs, 0.055, 6, false), new THREE.MeshBasicMaterial({ color: 0x16415b, transparent: true, opacity: 0.42, toneMapped: false }));
  scene.add(trackBase);
  const trackGlow = new THREE.Mesh(new THREE.TubeGeometry(curve, tubeSegs, 0.035, 5, false), new THREE.MeshBasicMaterial({ color: 0x35baff, transparent: true, opacity: 0.62, toneMapped: false }));
  scene.add(trackGlow);
  const trackCore = new THREE.Mesh(new THREE.TubeGeometry(curve, Math.floor(tubeSegs * 0.75), 0.012, 4, false), new THREE.MeshBasicMaterial({ color: 0xc2f5ff, transparent: true, opacity: 0.65, toneMapped: false }));
  scene.add(trackCore);

  // Permanent central metallic pole
  const pole = new THREE.Mesh(
    new THREE.CylinderGeometry(0.045, 0.045, height + 7, 16),
    new THREE.MeshStandardMaterial({ color: 0x7892a6, metalness: 0.55, roughness: 0.38, emissive: 0x07121c, emissiveIntensity: 0.12 })
  );
  scene.add(pole);
  const poleTop = new THREE.Mesh(
    new THREE.CylinderGeometry(0.07, 0.07, 0.12, 16),
    new THREE.MeshStandardMaterial({ color: 0x9fb6c8, metalness: 0.45, roughness: 0.28 })
  );
  poleTop.position.y = height / 2 + 3.5;
  scene.add(poleTop);

  const trace = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(curvePoints),
    new THREE.LineBasicMaterial({ color: 0x49d7ff, transparent: true, opacity: 0.9 })
  );
  scene.add(trace);

  // 5 Procedural 3D Planetary Waypoints
  const milestoneT = [0.08, 0.29, 0.50, 0.71, 0.92];
  const nodes = [];
  const planetPalette = [0x45c8ff, 0x8b78ff, 0x52e2b3, 0xffb86b, 0xff78c8];
  const accentColors = [0x9beaff, 0xb7a9ff, 0x9affd6, 0xffd4a0, 0xffb1df];
  const baseHex = ['#125078', '#55418c', '#117363', '#8a542c', '#884263'];
  const patchRgba = ['rgba(100,205,255,.18)', 'rgba(194,155,255,.16)', 'rgba(100,255,205,.16)', 'rgba(255,210,130,.16)', 'rgba(255,160,220,.16)'];

  milestoneT.forEach((t, i) => {
    const g = new THREE.Group();
    g.position.copy(curve.getPoint(t));

    const texW = perfCfg ? perfCfg.roadmapTexSize : (window.innerWidth >= 700 ? 512 : 256);
    const tw = texW, th = Math.floor(texW / 2), ts = texW / 512;
    const canvas = document.createElement('canvas');
    canvas.width = tw;
    canvas.height = th;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = baseHex[i];
    ctx.fillRect(0, 0, tw, th);

    const lighting = ctx.createRadialGradient(130 * ts, 60 * ts, 15 * ts, 260 * ts, 130 * ts, 325 * ts);
    lighting.addColorStop(0, 'rgba(255,255,255,.28)');
    lighting.addColorStop(0.48, 'rgba(255,255,255,.07)');
    lighting.addColorStop(1, 'rgba(0,0,0,.52)');
    ctx.fillStyle = lighting;
    ctx.fillRect(0, 0, tw, th);

    for (let c = 0; c < 34; c++) {
      const x = Math.random() * tw, y = Math.random() * th;
      const rx = (8 + Math.random() * 22) * ts, ry = (5 + Math.random() * 14) * ts;
      ctx.save();
      ctx.translate(x, y);
      ctx.scale(rx, ry);
      const patch = ctx.createRadialGradient(0, 0, 0.08, 0, 0, 1);
      patch.addColorStop(0, patchRgba[i]);
      patch.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = patch;
      ctx.beginPath();
      ctx.arc(0, 0, 1, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    for (let b = 0; b < 18; b++) {
      ctx.beginPath();
      const y = (b * 14.5 + Math.random() * 6) * ts;
      ctx.moveTo(0, y);
      for (let x = 0; x <= tw; x += 6 * ts) {
        ctx.lineTo(x, y + Math.sin(x * (0.036 / ts) + b) * 2 * ts + Math.sin(x * (0.012 / ts) + b * 2) * 3 * ts);
      }
      ctx.strokeStyle = 'rgba(255,255,255,.045)';
      ctx.lineWidth = (1.5 + Math.random() * 2) * ts;
      ctx.stroke();
    }

    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    const globe = new THREE.Mesh(
      new THREE.SphereGeometry(i === 4 ? 0.48 : 0.42, 42, 28),
      new THREE.MeshStandardMaterial({ map: tex, color: 0xffffff, metalness: 0.12, roughness: 0.65, emissive: planetPalette[i], emissiveIntensity: 0.08 })
    );
    g.add(globe);

    const cloudMat1 = new THREE.Mesh(
      new THREE.SphereGeometry(i === 4 ? 0.485 : 0.425, 28, 20),
      new THREE.MeshBasicMaterial({ color: accentColors[i], transparent: true, opacity: 0.018, depthWrite: false })
    );
    cloudMat1.scale.set(1.02, 0.99, 1.01);
    g.add(cloudMat1);

    const cloudMat2 = new THREE.Mesh(
      new THREE.SphereGeometry(i === 4 ? 0.49 : 0.43, 24, 18),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.012, depthWrite: false })
    );
    cloudMat2.scale.set(0.99, 1.015, 1.01);
    g.add(cloudMat2);

    if (i === 0) {
      const mechRing = new THREE.Mesh(new THREE.TorusGeometry(0.53, 0.012, 6, 72), new THREE.MeshStandardMaterial({ color: 0x8bc7df, metalness: 0.85, roughness: 0.3 }));
      mechRing.rotation.set(0.7, 0.2, 0.4);
      g.add(mechRing);
      for (let k = 0; k < 8; k++) {
        const bolt = new THREE.Mesh(new THREE.SphereGeometry(0.018, 8, 6), new THREE.MeshStandardMaterial({ color: 0xc4e6f5, metalness: 0.7, roughness: 0.3 }));
        const a = (k * Math.PI) / 4;
        bolt.position.set(Math.cos(a) * 0.45, Math.sin(a) * 0.45, 0);
        g.add(bolt);
      }
    }
    if (i === 1) {
      const neural = new THREE.Group();
      for (let k = 0; k < 18; k++) {
        const a = k * 2.399, r = 0.25 + (k % 4) * 0.045;
        const dot = new THREE.Mesh(new THREE.SphereGeometry(0.012, 8, 6), new THREE.MeshBasicMaterial({ color: accentColors[i] }));
        dot.position.set(Math.cos(a) * r, Math.sin(a) * r, Math.sin(k * 1.7) * 0.16);
        neural.add(dot);
      }
      g.add(neural);
      g.userData.neural = neural;
    }
    if (i === 2) {
      for (let k = 0; k < 2; k++) {
        const wave = new THREE.Mesh(new THREE.TorusGeometry(0.56 + k * 0.08, 0.008, 6, 96), new THREE.MeshBasicMaterial({ color: accentColors[i], transparent: true, opacity: 0.4 }));
        wave.rotation.set(0.8 + k * 0.35, 0.4, 1.1);
        g.add(wave);
      }
    }
    if (i === 3) {
      const shardGroup = new THREE.Group();
      for (let k = 0; k < 6; k++) {
        const shard = new THREE.Mesh(new THREE.OctahedronGeometry(0.045, 0), new THREE.MeshStandardMaterial({ color: accentColors[i], metalness: 0.5, roughness: 0.3, emissive: planetPalette[i], emissiveIntensity: 0.12 }));
        const a = (k * Math.PI) / 3;
        shard.position.set(Math.cos(a) * 0.62, Math.sin(a) * 0.62, Math.sin(k) * 0.12);
        shardGroup.add(shard);
      }
      g.add(shardGroup);
      g.userData.shards = shardGroup;
    }
    if (i === 4) {
      const halo2 = new THREE.Mesh(new THREE.TorusGeometry(1.02, 0.012, 8, 120), new THREE.MeshStandardMaterial({ color: accentColors[i], metalness: 0.5, roughness: 0.2, transparent: true, opacity: 0.55 }));
      halo2.rotation.set(0.7, 0.6, 0.2);
      g.add(halo2);
      const innerHalo = new THREE.Mesh(new THREE.TorusGeometry(0.62, 0.009, 6, 96), new THREE.MeshBasicMaterial({ color: 0xffb8ef, transparent: true, opacity: 0.3 }));
      innerHalo.rotation.set(1.2, 0.2, 0.6);
      g.add(innerHalo);
    }

    const aura = new THREE.Mesh(new THREE.SphereGeometry(i === 4 ? 0.72 : 0.58, 28, 20), new THREE.MeshBasicMaterial({ color: planetPalette[i], transparent: true, opacity: 0.11, depthWrite: false }));
    g.add(aura);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(i === 4 ? 0.88 : 0.72, 0.018, 8, 84), new THREE.MeshStandardMaterial({ color: planetPalette[i], metalness: 0.35, roughness: 0.25, transparent: true, opacity: 0.68 }));
    ring.rotation.set(0.18 + i * 0.22, 0.35, Math.PI * 0.22);
    g.add(ring);
    const atmosphere = new THREE.Mesh(new THREE.SphereGeometry(i === 4 ? 0.53 : 0.47, 28, 20), new THREE.MeshBasicMaterial({ color: planetPalette[i], transparent: true, opacity: 0.075, side: THREE.BackSide, depthWrite: false }));
    g.add(atmosphere);

    const orbit = new THREE.Group();
    orbit.position.copy(g.position);
    const orbitDot = new THREE.Mesh(new THREE.SphereGeometry(0.055, 12, 8), new THREE.MeshBasicMaterial({ color: planetPalette[i] }));
    orbitDot.position.set(1.05, 0, 0);
    orbit.add(orbitDot);
    scene.add(orbit);

    g.userData.orbit = orbit;
    g.userData.clouds = [cloudMat1, cloudMat2];
    g.userData.aura = aura;
    g.userData.ring = ring;
    g.userData.atmosphere = atmosphere;
    scene.add(g);
    nodes.push(g);
  });

  const energyBeam = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 1, 8), new THREE.MeshBasicMaterial({ color: 0x66e5ff, transparent: true, opacity: 0, depthWrite: false }));
  scene.add(energyBeam);

  // Scoped overview labels (mounted inside sticky viewport, never leaking outside #roadmap)
  const overviewLabels = [];
  const labelNames = [
    ['01 · CORE', 'Foundation'],
    ['02 · MIND', 'Intelligence & memory'],
    ['03 · SENSES', 'Perception'],
    ['04 · ACTION', 'Execution'],
    ['05 · EVOLUTION', 'Future vision']
  ];
  labelNames.forEach((a) => {
    const el = document.createElement('div');
    el.className = 'sr-overview-label';
    el.innerHTML = `<b>${a[0]}</b><small>${a[1]}</small>`;
    sticky.appendChild(el);
    overviewLabels.push(el);
  });

  // Spiral dust & ambient motes
  const dust = [];
  for (let i = 0; i < 80; i++) {
    const p = curve.getPoint(Math.random());
    p.add(new THREE.Vector3((Math.random() - 0.5) * 4, (Math.random() - 0.5) * 2.5, (Math.random() - 0.5) * 4));
    dust.push(p);
  }
  scene.add(new THREE.Points(new THREE.BufferGeometry().setFromPoints(dust), new THREE.PointsMaterial({ color: 0x63cfff, size: 0.12, transparent: true, opacity: 0.55 })));

  const ambientMotes = [];
  for (let i = 0; i < 20; i++) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.035, 8, 6), new THREE.MeshBasicMaterial({ color: 0x9beaff, transparent: true, opacity: 0.35 + Math.random() * 0.25 }));
    m.position.set((Math.random() - 0.5) * 18, (Math.random() - 0.5) * height, (Math.random() - 0.5) * 16);
    scene.add(m);
    ambientMotes.push({ mesh: m, phase: Math.random() * 6.28, speed: 0.15 + Math.random() * 0.25 });
  }

  const transitSparks = [];
  for (let i = 0; i < 12; i++) {
    const spark = new THREE.Mesh(new THREE.SphereGeometry(0.055, 10, 8), new THREE.MeshBasicMaterial({ color: 0x8af3ff, transparent: true, opacity: 0.9 }));
    scene.add(spark);
    transitSparks.push(spark);
  }

  // Optional milestone audio chime
  let soundEnabled = false, lastSegment = -1, completionTriggered = false, audioCtx = null;
  function playActivation() {
    if (!soundEnabled) return;
    try {
      audioCtx ??= new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = 'sine';
      o.frequency.setValueAtTime(660, audioCtx.currentTime);
      o.frequency.exponentialRampToValueAtTime(990, audioCtx.currentTime + 0.22);
      g.gain.setValueAtTime(0.0001, audioCtx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.045, audioCtx.currentTime + 0.04);
      g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.35);
      o.connect(g);
      g.connect(audioCtx.destination);
      o.start();
      o.stop(audioCtx.currentTime + 0.36);
    } catch (_) {}
  }

  const soundBtn = document.getElementById('sr-soundToggle');
  if (soundBtn) {
    soundBtn.addEventListener('click', () => {
      soundEnabled = !soundEnabled;
      soundBtn.textContent = soundEnabled ? 'SOUND ON' : 'SOUND OFF';
      if (soundEnabled) playActivation();
    });
  }

  const milestoneStatuses = [
    { label: 'COMPLETED · v1.0.0-beta', cls: 'complete' },
    { label: 'COMPLETED · ACTIVE POLISH', cls: 'complete' },
    { label: 'SHIPPED · ONGOING TUNING', cls: 'current' },
    { label: 'SHIPPED · ONGOING EXPANSION', cls: 'current' },
    { label: 'PLANNED · FUTURE VISION', cls: 'upcoming' }
  ];

  const featureSets = [
    [
      { tag: 'DONE', cls: 'done', text: 'PySide6 desktop UI, backend IPC & v1.0.0-beta ZIP launcher' },
      { tag: 'DONE', cls: 'done', text: 'Gemini Live, 0 ms offline mode, Vosk & Windows SAPI TTS' },
      { tag: 'DONE', cls: 'done', text: 'Fail-closed 9-category permissions & Windows DPAPI encryption' }
    ],
    [
      { tag: 'DONE', cls: 'done', text: 'Local Ollama runtime (qwen2.5:3b, moondream) & Settings UI' },
      { tag: 'DONE', cls: 'done', text: 'Local memory, opt-in TF-IDF/embedding index & Safe Python' },
      { tag: 'ONGOING', cls: 'ongoing', text: 'Deeper multi-step offline reasoning & modular orchestration' }
    ],
    [
      { tag: 'DONE', cls: 'done', text: 'Offline "Hey Nova" wake word & AUTO NEAREST mic calibration' },
      { tag: 'DONE', cls: 'done', text: 'Webcam hand-gesture mouse, Point at Screen & video frames' },
      { tag: 'ONGOING', cls: 'ongoing', text: 'Continuous multimodal perception & gesture responsiveness' }
    ],
    [
      { tag: 'DONE', cls: 'done', text: 'Verified Windows UIAutomation, Playwright & YouTube controls' },
      { tag: 'DONE', cls: 'done', text: 'Guided walkthroughs & reusable semantic workflow replay' },
      { tag: 'DONE', cls: 'done', text: '6 curated workflow templates & visual dry-run workflow debugger' }
    ],
    [
      { tag: 'PLANNED', cls: 'planned', text: 'Signed Windows .exe installer & seamless auto-updater' },
      { tag: 'PLANNED', cls: 'planned', text: 'Expanded plugin & skill ecosystem' },
      { tag: 'PLANNED', cls: 'planned', text: 'Adaptive long-term personalization (future vision)' }
    ]
  ];

  const content = [
    ['01 / CORE', 'Foundation & architecture.', 'PySide6 desktop UI, backend engine, Gemini Live voice and typed chat, structured IPC, offline mode, system tray, permissions, action routing and verification. Reliability improvements remain ongoing.'],
    ['02 / MIND', 'Intelligence & memory.', 'Local Ollama runtime, conversation context, local memory, knowledge indexing, document/code analysis, offline agent and hardware-aware model management. Advanced reasoning is ongoing; adaptive learning is future vision.'],
    ['03 / SENSES', 'Perception & interaction.', 'Hey Nova wake word, voice/audio processing, mic sensitivity and AUTO NEAREST, gesture mouse, screen understanding, screen-region pointing and video/lecture frame understanding. Multimodal perception is being improved.'],
    ['04 / ACTION', 'Execution & computer control.', 'Desktop and app control, mouse/keyboard automation, UI detection, browser and YouTube controls, file operations, permission-gated actions, verification, task/memory management and multi-step workflows. Reliable complex autonomy remains ongoing.'],
    ['05 / EVOLUTION', 'Growth & future intelligence.', 'Persistent memory, task/workflow storage, context-aware conversations and workflow recording/replay form the foundation. Adaptive behavior, self-improvement and long-term personalized intelligence remain future vision.']
  ];

  const info = document.getElementById('sr-info');
  const num = document.getElementById('sr-num');
  const heading = document.getElementById('sr-heading');
  const desc = document.getElementById('sr-desc');
  const featureList = document.getElementById('sr-featureList');
  const statusEl = document.getElementById('sr-milestoneStatus');
  const chapter = document.getElementById('sr-chapter');
  const bar = document.getElementById('sr-bar');
  const scrolllabel = document.getElementById('sr-scrolllabel');
  const flashEl = document.getElementById('sr-completionFlash');
  const outroBanner = document.getElementById('sr-outro');
  const navButtons = Array.from(document.querySelectorAll('#sr-waypointNav button'));

  let progress = 0, targetProgress = 0, activeMilestone = 0, lastRenderedSt = -2, lastStatusText = '';
  let rafId = 0, isInView = false, lastFrameTime = performance.now(), labelsVisible = true;
  const overviewPoints = milestoneT.map((t, i) => {
    const wp = curve.getPoint(t).clone();
    wp.x += (i % 2 === 0 ? -1 : 1) * 2.35;
    wp.z += 0.45;
    return wp;
  });
  const tmpProj = new THREE.Vector3();

  function readScrollTarget() {
    const r = journey.getBoundingClientRect();
    const range = Math.max(1, journey.offsetHeight - window.innerHeight);
    targetProgress = Math.max(0, Math.min(1, -r.top / range));
    const fullyInside = r.top <= 40 && r.bottom >= window.innerHeight - 40;
    window.__novaRoadmapActive = fullyInside;
    document.body.classList.toggle('roadmap-active', fullyInside);
  }

  function smooth(x) {
    x = Math.max(0, Math.min(1, x));
    return x * x * (3 - 2 * x);
  }

  function journeyState() {
    const p = progress;
    const zoom = smooth(Math.min(1, p / 0.12));
    const ride = Math.max(0, Math.min(1, (p - 0.12) / 0.88));
    const rideFloat = ride * 4;
    let seg = Math.min(3, Math.floor(rideFloat));
    let local = rideFloat - seg;
    if (ride >= 1) { seg = 3; local = 1; }
    const t = p <= 0.12 ? milestoneT[0] : milestoneT[seg] + (milestoneT[seg + 1] - milestoneT[seg]) * smooth(local);
    return { zoom, ride, seg, local, t };
  }

  function jumpToWaypoint(i) {
    const range = Math.max(1, journey.offsetHeight - window.innerHeight);
    const target = i === 0 ? 0.12 : 0.12 + 0.88 * (i / 4);
    const sectionTop = journey.getBoundingClientRect().top + window.scrollY;
    window.scrollTo({ top: sectionTop + target * range, behavior: reduceMotion ? 'auto' : 'smooth' });
  }

  function update() {
    if (bar) bar.style.width = (progress * 100).toFixed(1) + '%';

    const q = journeyState();
    let st = -1, panelExit = 0, panelEnter = 0;
    if (progress > 0.075 && progress <= 0.12) st = 0;
    else if (progress > 0.12 && progress < 0.995) {
      if (q.local < 0.55) {
        st = q.seg;
        panelExit = smooth(Math.max(0, (q.local - 0.16) / 0.24));
      } else {
        st = Math.min(4, q.seg + 1);
        panelEnter = smooth((q.local - 0.55) / 0.18);
      }
    }
    if (q.ride >= 0.995) st = 4;

    activeMilestone = Math.max(0, st);
    const active = st >= 0 && progress < 0.975;
    if (info) info.classList.toggle('show', active);
    if (outroBanner) outroBanner.classList.toggle('show', progress >= 0.975);

    if (active) {
      if (st !== lastRenderedSt) {
        lastRenderedSt = st;
        num.textContent = content[st][0];
        heading.textContent = content[st][1];
        desc.textContent = content[st][2];
        featureList.innerHTML = featureSets[st].map((x) => '<li><span class="sr-tag sr-tag-' + x.cls + '">' + x.tag + '</span><span>' + x.text + '</span></li>').join('');
        chapter.textContent = content[st][0].replace(' / ', ' — ');
        scrolllabel.textContent = 'Waypoint ' + (st + 1) + ' · reached';
      }

      const stMeta = milestoneStatuses[st] || { label: 'PLANNED', cls: 'upcoming' };
      if (stMeta.label !== lastStatusText) {
        lastStatusText = stMeta.label;
        statusEl.textContent = stMeta.label;
        statusEl.className = 'sr-milestone-status ' + stMeta.cls;
      }

      if (progress > 0.12 && q.ride < 0.975 && window.innerWidth > 700) {
        const exitX = panelExit * Math.min(900, stageW * 0.95);
        const enterX = (1 - panelEnter) * Math.min(240, stageW * 0.26);
        info.style.transform = `translate3d(${(st === q.seg ? exitX : enterX).toFixed(1)}px, -50%, 0)`;
        info.style.opacity = String(st === q.seg ? 1 - panelExit : panelEnter);
      } else {
        info.style.transform = '';
        info.style.opacity = '';
      }
    } else if (info) {
      info.style.transform = '';
      info.style.opacity = '';
    }

    if (progress <= 0.075) {
      if (lastRenderedSt !== -1) {
        lastRenderedSt = -1;
        chapter.textContent = 'OVERVIEW';
        scrolllabel.innerHTML = '<span class="sr-hint-icon">⌄</span> Scroll to zoom into waypoint 01';
      }
    } else if (progress >= 0.975) {
      if (lastRenderedSt !== 99) {
        lastRenderedSt = 99;
        chapter.textContent = 'THE FUTURE';
        scrolllabel.textContent = 'Journey complete · continue scrolling';
      }
    } else if (!active && lastRenderedSt !== -3) {
      lastRenderedSt = -3;
      chapter.textContent = 'IN TRANSIT';
      scrolllabel.textContent = 'Following the spiral · in transit';
    }

    const now = performance.now();
    nodes.forEach((n, i) => {
      const arrivalTarget = i === q.seg + 1 && q.local > 0.78 && q.local < 1.0 && q.ride > 0;
      const arrival = smooth(Math.max(0, Math.min(1, (q.local - 0.78) / 0.16))) * (arrivalTarget ? 1 : 0);
      const activeNode = i === st && active;
      const glow = activeNode ? 0 : arrival;
      n.children[0].material.emissiveIntensity = 0.06 + glow * 0.42;
      n.scale.setScalar(1 + glow * 0.08 + Math.sin(now * 0.003 + i) * 0.006 * glow);
      if (!reduceMotion) {
        n.rotation.y = now * 0.00022 * (i + 1);
        if (n.userData.orbit) n.userData.orbit.rotation.y = now * 0.0007 * (i + 1);
        if (n.userData.clouds) {
          n.userData.clouds[0].rotation.y = -now * 0.00035 * (i + 1);
          n.userData.clouds[1].rotation.x = now * 0.00018 * (i + 1);
        }
      }
      if (n.userData.clouds) {
        n.userData.clouds[0].material.opacity = 0.018 + glow * 0.025;
        n.userData.clouds[1].material.opacity = 0.012 + glow * 0.018;
      }
      if (n.userData.aura) {
        n.userData.aura.material.opacity = activeNode ? 0.025 : 0.055 + glow * 0.08;
        n.userData.aura.scale.setScalar(activeNode ? 1 : 1 + glow * 0.18 + 0.012 * Math.sin(now * 0.002 + i));
      }
      if (n.userData.ring) n.userData.ring.material.opacity = activeNode ? 0.38 : 0.45 + glow * 0.25;
    });

    navButtons.forEach((b, i) => b.classList.toggle('active', i === st));

    const beamTarget = nodes[Math.max(0, st)].position;
    energyBeam.position.copy(beamTarget).multiplyScalar(0.5);
    energyBeam.position.y *= 0.65;
    energyBeam.scale.y = Math.max(0.1, beamTarget.length());
    energyBeam.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), beamTarget.clone().normalize());
    energyBeam.material.opacity = active ? 0.22 + 0.18 * Math.sin(now * 0.004) : 0;

    const finale = smooth(Math.max(0, (progress - 0.94) / 0.06));
    trackGlow.material.opacity = 0.92 + finale * 0.08;
    trackCore.material.color.setHex(finale > 0.5 ? 0xffc7f1 : 0xc2f5ff);
    key.intensity = 120 + finale * 65;
    fill.intensity = 85 + finale * 45;

    const showLabels = progress < 0.05;
    if (showLabels || labelsVisible) {
      labelsVisible = showLabels;
      overviewLabels.forEach((el, i) => {
        if (!showLabels) {
          el.style.opacity = '0';
          return;
        }
        tmpProj.copy(overviewPoints[i]).project(camera);
        const x = (tmpProj.x * 0.5 + 0.5) * stageW;
        const y = (-tmpProj.y * 0.5 + 0.5) * stageH;
        el.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) translate(-50%, -50%)`;
        el.style.opacity = tmpProj.z < 1 ? '0.92' : '0';
      });
    }
  }

  function renderFrame() {
    const now = performance.now();
    const dt = Math.min(0.05, Math.max(0.001, (now - lastFrameTime) * 0.001));
    lastFrameTime = now;
    const frameScale = dt * 60;

    const scrollDelta = targetProgress - progress;
    const damp = 1 - Math.pow(1 - Math.min(0.20, Math.max(0.08, 0.08 + Math.abs(scrollDelta) * 0.55)), frameScale);
    progress += scrollDelta * (reduceMotion ? 1 : damp);
    if (Math.abs(targetProgress - progress) < 0.0004) progress = targetProgress;
    update();

    const q = journeyState(), p = progress, t = q.t;
    if (!reduceMotion) {
      ambientMotes.forEach((o) => {
        o.mesh.position.y += o.speed * 0.006 * frameScale;
        if (o.mesh.position.y > height / 2 + 4) o.mesh.position.y = -height / 2 - 4;
        o.mesh.position.x += Math.sin(now * 0.0003 + o.phase) * 0.0015 * frameScale;
        o.mesh.material.opacity = 0.22 + 0.25 * (0.5 + 0.5 * Math.sin(now * 0.001 + o.phase));
      });
    }
    transitSparks.forEach((spark, i) => {
      const u = (p * 5.5 + i / 12) % 1;
      spark.position.copy(curve.getPoint(u));
      spark.visible = p > 0.12 && p < 0.995;
    });

    if (q.ride > 0 && q.seg !== lastSegment) {
      if (lastSegment >= 0) playActivation();
      lastSegment = q.seg;
    }
    if (p >= 0.995 && !completionTriggered) {
      completionTriggered = true;
      if (flashEl) flashEl.classList.add('active');
      playActivation();
    }
    if (p < 0.97) {
      completionTriggered = false;
      if (flashEl) flashEl.classList.remove('active');
    }

    const point = curve.getPoint(t);
    const tangent = curve.getTangent(t).normalize();
    const rideCam = point.clone().add(tangent.clone().multiplyScalar(-2.35)).add(new THREE.Vector3(0, 0.22, 0));
    const lookAheadT = Math.min(1, t + 0.035);
    const ahead = curve.getPoint(lookAheadT);
    const lookTarget = ahead.clone().add(tangent.clone().multiplyScalar(0.45));

    const overviewPos = new THREE.Vector3(0, height * 0.52, 38);
    const overviewLook = new THREE.Vector3(0, 0, 0);
    const zoomBlend = smooth(Math.min(1, p / 0.12));
    const desiredPos = overviewPos.lerp(rideCam, zoomBlend);
    const posLerp = reduceMotion ? 1 : 1 - Math.pow(1 - 0.12, frameScale);
    const lookLerp = reduceMotion ? 1 : 1 - Math.pow(1 - 0.16, frameScale);
    camera.position.lerp(desiredPos, posLerp);
    const desiredLook = overviewLook.lerp(lookTarget, zoomBlend);
    camera.userData.lookTarget ??= new THREE.Vector3();
    camera.userData.lookTarget.lerp(desiredLook, lookLerp);
    camera.lookAt(camera.userData.lookTarget);

    const curvatureBank = Math.max(-0.055, Math.min(0.055, (tangent.x - tangent.z) * 0.045));
    camera.userData.bank ??= 0;
    camera.userData.bank += (curvatureBank - camera.userData.bank) * (1 - Math.pow(1 - 0.08, frameScale));
    camera.rotation.z = camera.userData.bank;
    camera.fov = 54 - 13 * zoomBlend;
    camera.updateProjectionMatrix();

    key.position.copy(point).add(new THREE.Vector3(5, 5, 5));
    fill.position.copy(point).add(new THREE.Vector3(-5, -3, -5));
    renderer.render(scene, camera);

    if (isInView && !document.hidden) {
      rafId = requestAnimationFrame(renderFrame);
    } else {
      rafId = 0;
    }
  }

  function startLoop() {
    if (!rafId && isInView && !document.hidden) {
      lastFrameTime = performance.now();
      rafId = requestAnimationFrame(renderFrame);
    }
  }

  // Waypoint buttons
  navButtons.forEach((btn) => {
    btn.addEventListener('click', () => jumpToWaypoint(+btn.dataset.way));
  });

  // 3D Planet click-to-jump (raycaster scoped to canvas rect)
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  renderer.domElement.addEventListener('click', (ev) => {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / Math.max(1, rect.height)) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects(nodes, true);
    if (hits.length) {
      let obj = hits[0].object;
      while (obj && !nodes.includes(obj)) obj = obj.parent;
      if (obj) {
        const i = nodes.indexOf(obj);
        if (i >= 0) jumpToWaypoint(i);
      }
    }
  });

  // Explore waypoint detail modal
  const modal = document.getElementById('sr-detailModal');
  const detailsBtn = document.getElementById('sr-detailsBtn');
  const detailClose = document.getElementById('sr-detailClose');
  if (detailsBtn && modal) {
    detailsBtn.addEventListener('click', () => {
      const ix = activeMilestone;
      document.getElementById('sr-detailEyebrow').textContent = content[ix][0];
      document.getElementById('sr-detailTitle').textContent = content[ix][1];
      document.getElementById('sr-detailText').textContent =
        content[ix][2] + ' This waypoint represents a key stage in Nova’s evolution; its listed capabilities can be explored and refined as the roadmap progresses.';
      modal.classList.add('open');
    });
  }
  if (detailClose && modal) {
    detailClose.addEventListener('click', () => modal.classList.remove('open'));
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.classList.remove('open');
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modal.classList.contains('open')) modal.classList.remove('open');
    });
  }

  // Only render Three.js when #roadmap is visible in viewport (and pause #nova-webgl behind it)
  if ('IntersectionObserver' in window) {
    const visObs = new IntersectionObserver((entries) => {
      isInView = Boolean(entries[0] && entries[0].isIntersecting);
      if (isInView) {
        readScrollTarget();
        startLoop();
      } else {
        window.__novaRoadmapActive = false;
        document.body.classList.remove('roadmap-active');
        if (rafId) {
          cancelAnimationFrame(rafId);
          rafId = 0;
        }
      }
    }, { rootMargin: '150px 0px' });
    visObs.observe(journey);
  } else {
    isInView = true;
    startLoop();
  }

  window.addEventListener('scroll', () => {
    if (isInView) readScrollTarget();
  }, { passive: true });

  window.addEventListener('resize', () => {
    const sz = getSize();
    stageW = sz.w;
    stageH = sz.h;
    camera.aspect = stageW / stageH;
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, window.innerWidth < 700 ? 1.25 : 1.5));
    renderer.setSize(stageW, stageH);
    if (isInView) renderFrame();
  }, { passive: true });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden && rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    } else if (isInView) {
      startLoop();
    }
  });

  // Initial pose (also clear fallback if Three.js finished loading on a slow connection)
  window.__novaSpiralReady = true;
  const rmSection = document.getElementById('roadmap');
  const fbEl = document.getElementById('sr-fallback');
  if (rmSection) rmSection.classList.remove('is-fallback');
  if (fbEl) fbEl.hidden = true;
  camera.position.set(0, height * 0.52, 38);
  camera.lookAt(0, 0, 0);
  update();
  renderer.render(scene, camera);
}
