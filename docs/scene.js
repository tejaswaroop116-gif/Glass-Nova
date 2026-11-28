/* Glass Nova — True Cinematic 3D World, Morphing Nova Core & Scroll-Controlled Camera Flight
 * Inspired by Codrops Cinematic Scroll Animations (MIT):
 *  - Continuous 3D camera journey (position, lookAt, dynamic FOV warp & subtle roll)
 *  - Transforming Nova Core (12 articulated glass shards that open into a fly-through portal)
 *  - Deep-space geometric Portal Tunnel, Crystalline Vault & 4 Companion Energy Satellites
 *  - Velocity-reactive curved light trails with momentum decay & mobile resize guard
 *  - Full graceful 2D fallback, tab-hidden pause, #roadmap pause & prefers-reduced-motion support
 */

const canvas = document.getElementById('nova-webgl');
const reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

function initFallback2D() {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  let W = 0, H = 0, DPR = 1;
  const isMobile = window.innerWidth < 700;
  const count = isMobile ? 65 : 150;
  const stars = Array.from({ length: count }, (_, i) => ({
    x: Math.random(),
    y: Math.random(),
    r: 0.7 + Math.random() * 1.6,
    phase: Math.random() * Math.PI * 2,
    tint: i % 4 === 0 ? '155, 123, 255' : (i % 3 === 0 ? '56, 232, 255' : '220, 240, 255')
  }));

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 1.5);
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = Math.max(1, Math.floor(W * DPR));
    canvas.height = Math.max(1, Math.floor(H * DPR));
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  let t = 0;
  let rafId = 0;
  function draw() {
    if (window.__novaRoadmapActive) {
      if (!reduceMotion && !document.hidden) rafId = requestAnimationFrame(draw);
      return;
    }
    ctx.clearRect(0, 0, W, H);
    const nebR = Math.max(W, H) * 0.58;
    const g1 = ctx.createRadialGradient(W * 0.28, H * 0.30, 10, W * 0.28, H * 0.30, nebR);
    g1.addColorStop(0, 'rgba(56, 232, 255, 0.16)');
    g1.addColorStop(1, 'rgba(4, 5, 9, 0)');
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, W, H);

    const g2 = ctx.createRadialGradient(W * 0.74, H * 0.70, 10, W * 0.74, H * 0.70, nebR);
    g2.addColorStop(0, 'rgba(155, 123, 255, 0.16)');
    g2.addColorStop(1, 'rgba(4, 5, 9, 0)');
    ctx.fillStyle = g2;
    ctx.fillRect(0, 0, W, H);

    if (!reduceMotion) t += 0.014;
    for (const s of stars) {
      const a = 0.28 + 0.52 * (0.5 + 0.5 * Math.sin(t + s.phase));
      ctx.fillStyle = `rgba(${s.tint}, ${a.toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(s.x * W, s.y * H, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
    if (!reduceMotion && !document.hidden) rafId = requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize, { passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = 0;
    } else if (!reduceMotion && !rafId) {
      rafId = requestAnimationFrame(draw);
    }
  });
  window.__novaWebGLFallback = true;
  resize();
  draw();
}

function createGlowTexture(THREE, innerRgba, midRgba) {
  const c = document.createElement('canvas');
  c.width = 128;
  c.height = 128;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(64, 64, 2, 64, 64, 64);
  g.addColorStop(0, innerRgba);
  g.addColorStop(0.32, midRgba);
  g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  tex.needsUpdate = true;
  return tex;
}

async function initWebGLWorld() {
  if (!canvas) return;
  let THREE;
  try {
    THREE = await import('https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js');
  } catch (_) {
    initFallback2D();
    return;
  }

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: window.innerWidth >= 768,
      alpha: true,
      powerPreference: 'high-performance'
    });
  } catch (_) {
    initFallback2D();
    return;
  }

  const isMobileNow = () => window.innerWidth < 768;
  let lastWidth = window.innerWidth;

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, isMobileNow() ? 1.25 : 1.5));
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.18;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x04050a, 0.032);

  const camera = new THREE.PerspectiveCamera(
    isMobileNow() ? 56 : 46,
    window.innerWidth / Math.max(1, window.innerHeight),
    0.1,
    180
  );
  camera.position.set(1.2, 0.4, 9.6);

  // =====================================================
  // CINEMATIC LIGHTING RIG
  // =====================================================
  scene.add(new THREE.AmbientLight(0x8bb8ff, 0.42));

  const keyLight = new THREE.PointLight(0x38e8ff, 48, 46);
  keyLight.position.set(-5.5, 4.0, 7.0);
  scene.add(keyLight);

  const fillLight = new THREE.PointLight(0x9b7bff, 42, 46);
  fillLight.position.set(6.0, -3.5, 4.0);
  scene.add(fillLight);

  const tunnelLight = new THREE.PointLight(0x38e8ff, 36, 40);
  tunnelLight.position.set(0, 0, -14);
  scene.add(tunnelLight);

  const rimDir = new THREE.DirectionalLight(0xcbeeff, 0.95);
  rimDir.position.set(0, 8, -10);
  scene.add(rimDir);

  // =====================================================
  // 1. THE MORPHING NOVA CORE (at origin z = 0)
  // =====================================================
  const coreGroup = new THREE.Group();
  scene.add(coreGroup);

  // Volumetric glow halo sprite (zero-cost fake bloom)
  const coreGlowMat = new THREE.SpriteMaterial({
    map: createGlowTexture(THREE, 'rgba(130, 244, 255, 0.85)', 'rgba(56, 232, 255, 0.24)'),
    color: 0x38e8ff,
    transparent: true,
    opacity: 0.72,
    blending: THREE.AdditiveBlending,
    depthWrite: false
  });
  const coreGlow = new THREE.Sprite(coreGlowMat);
  coreGlow.scale.set(6.8, 6.8, 1);
  coreGroup.add(coreGlow);

  // Inner Quantum Singularity
  const innerGeo = new THREE.OctahedronGeometry(0.76, 1);
  const innerMat = new THREE.MeshStandardMaterial({
    color: 0xc2f7ff,
    emissive: 0x38e8ff,
    emissiveIntensity: 1.05,
    metalness: 0.25,
    roughness: 0.15,
    transparent: true,
    opacity: 0.88,
    flatShading: true
  });
  const innerCore = new THREE.Mesh(innerGeo, innerMat);
  coreGroup.add(innerCore);

  // Neural inner lattice wireframe
  const latticeGeo = new THREE.IcosahedronGeometry(1.18, 1);
  const latticeMat = new THREE.LineBasicMaterial({
    color: 0x7eeeff,
    transparent: true,
    opacity: 0.34
  });
  const latticeMesh = new THREE.LineSegments(new THREE.EdgesGeometry(latticeGeo), latticeMat);
  coreGroup.add(latticeMesh);

  // 12 Articulated Faceted Glass Shards that physically bloom open into a portal!
  const shardDirections = [
    [ 1,  0,  0], [-1,  0,  0], [ 0,  1,  0], [ 0, -1,  0],
    [ 0.7,  0.7,  0.2], [-0.7,  0.7,  0.2], [ 0.7, -0.7,  0.2], [-0.7, -0.7,  0.2],
    [ 0.65,  0.25, -0.7], [-0.65,  0.25, -0.7], [ 0.55, -0.5,  0.65], [-0.55, -0.5,  0.65]
  ];
  const shardGeo = new THREE.OctahedronGeometry(0.62, 0);
  const shardEdgeGeo = new THREE.EdgesGeometry(shardGeo);
  const shardMat = new THREE.MeshStandardMaterial({
    color: 0x0e1d36,
    emissive: 0x164468,
    emissiveIntensity: 0.55,
    metalness: 0.88,
    roughness: 0.14,
    transparent: true,
    opacity: 0.56,
    flatShading: true
  });
  const shardEdgeMat = new THREE.LineBasicMaterial({
    color: 0x38e8ff,
    transparent: true,
    opacity: 0.52
  });

  const shards = shardDirections.map((d, idx) => {
    const dir = new THREE.Vector3(d[0], d[1], d[2]).normalize();
    const holder = new THREE.Group();
    const mesh = new THREE.Mesh(shardGeo, shardMat);
    mesh.scale.set(1.15, 0.78, 0.42);
    const edge = new THREE.LineSegments(shardEdgeGeo, shardEdgeMat);
    edge.scale.copy(mesh.scale).multiplyScalar(1.02);
    holder.add(mesh, edge);
    holder.position.copy(dir).multiplyScalar(1.55);
    holder.lookAt(dir.clone().multiplyScalar(4));
    coreGroup.add(holder);
    return { holder, dir, baseDist: 1.55, phase: idx * 0.52 };
  });

  // Dual Resonance Gyro Rings (expand/separate in Modes & Portal acts)
  const ringConfigs = [
    { r: 2.35, tube: 0.018, color: 0x38e8ff, op: 0.62, rot: [1.1, 0.25, 0.1] },
    { r: 2.95, tube: 0.014, color: 0x9b7bff, op: 0.52, rot: [0.65, -0.6, 0.9] },
    { r: 3.65, tube: 0.011, color: 0x4f8cff, op: 0.34, rot: [-0.85, 0.45, -0.3] }
  ];
  const gyroRings = ringConfigs.map((cfg) => {
    const mat = new THREE.MeshBasicMaterial({
      color: cfg.color,
      transparent: true,
      opacity: cfg.op
    });
    const mesh = new THREE.Mesh(new THREE.TorusGeometry(cfg.r, cfg.tube, 8, 96), mat);
    mesh.rotation.set(cfg.rot[0], cfg.rot[1], cfg.rot[2]);
    coreGroup.add(mesh);
    return { mesh, mat, baseRot: cfg.rot };
  });

  // =====================================================
  // 2. DEEP-SPACE GEOMETRIC PORTAL TUNNEL (z = -3 to z = -34)
  //    The camera literally flies THROUGH these gates in Act II!
  // =====================================================
  const tunnelGroup = new THREE.Group();
  scene.add(tunnelGroup);

  const gateCount = isMobileNow() ? 9 : 14;
  const tunnelGates = [];
  for (let i = 0; i < gateCount; i++) {
    const zPos = -3.2 - i * 2.35;
    const isHex = i % 2 === 0;
    const radius = 3.1 + Math.sin(i * 0.6) * 0.45;
    const geo = new THREE.TorusGeometry(radius, 0.018, 6, isHex ? 6 : 48);
    const mat = new THREE.MeshBasicMaterial({
      color: i % 3 === 0 ? 0x9b7bff : 0x38e8ff,
      transparent: true,
      opacity: 0.0
    });
    const gate = new THREE.Mesh(geo, mat);
    gate.position.set(Math.sin(i * 0.42) * 0.6, Math.cos(i * 0.38) * 0.4, zPos);
    gate.rotation.z = i * 0.24;
    tunnelGroup.add(gate);
    tunnelGates.push({ mesh: gate, mat, baseZ: zPos, idx: i });
  }

  // =====================================================
  // 3. CRYSTALLINE SECURITY VAULT (at z = -22, summoned in Act III & Security)
  // =====================================================
  const vaultGroup = new THREE.Group();
  vaultGroup.position.set(0, 0, -22);
  scene.add(vaultGroup);

  const vaultMat = new THREE.MeshBasicMaterial({
    color: 0x34d399,
    wireframe: true,
    transparent: true,
    opacity: 0.0
  });
  const vaultOuter = new THREE.Mesh(new THREE.DodecahedronGeometry(3.4, 0), vaultMat);
  vaultGroup.add(vaultOuter);

  const vaultRingMat = new THREE.MeshBasicMaterial({
    color: 0x38e8ff,
    transparent: true,
    opacity: 0.0
  });
  const vaultRing1 = new THREE.Mesh(new THREE.TorusGeometry(4.1, 0.022, 6, 6), vaultRingMat);
  const vaultRing2 = new THREE.Mesh(new THREE.TorusGeometry(4.6, 0.016, 6, 6), vaultRingMat);
  vaultRing2.rotation.x = Math.PI * 0.5;
  vaultGroup.add(vaultRing1, vaultRing2);

  // =====================================================
  // 4. FOUR COMPANION ENERGY SATELLITES (Cyan, Violet, Emerald, Blue)
  // =====================================================
  const companionGroup = new THREE.Group();
  scene.add(companionGroup);

  const compColors = [0x38e8ff, 0x9b7bff, 0x34d399, 0x4f8cff];
  const companionOrbs = compColors.map((hex, i) => {
    const g = new THREE.Group();
    const orb = new THREE.Mesh(
      new THREE.OctahedronGeometry(0.24, 1),
      new THREE.MeshStandardMaterial({
        color: 0xffffff,
        emissive: hex,
        emissiveIntensity: 1.1,
        roughness: 0.2,
        metalness: 0.3
      })
    );
    const halo = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: createGlowTexture(THREE, 'rgba(255,255,255,0.9)', 'rgba(120,220,255,0.22)'),
        color: hex,
        transparent: true,
        opacity: 0.7,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      })
    );
    halo.scale.set(1.6, 1.6, 1);
    g.add(orb, halo);
    companionGroup.add(g);
    return { group: g, orb, angle: (i * Math.PI) / 2, yOff: (i % 2 === 0 ? 1 : -1) * 0.8 };
  });

  // =====================================================
  // 5. VELOCITY-REACTIVE CURVED LIGHT TRAILS (Codrops Demo 1 technique)
  // =====================================================
  const trailGroup = new THREE.Group();
  scene.add(trailGroup);

  const trailCount = isMobileNow() ? 14 : 26;
  const trailSegments = 18;
  const orbitalTrails = [];

  for (let i = 0; i < trailCount; i++) {
    const radius = 2.6 + (i % 6) * 0.65 + Math.random() * 0.4;
    const baseY = (i % 2 === 0 ? 1 : -1) * (0.4 + Math.random() * 2.8);
    const zOffset = -Math.random() * 28;
    const angleSpan = 0.32 + Math.random() * 0.38;
    const baseAngle = (i / trailCount) * Math.PI * 2;
    const positions = new Float32Array((trailSegments + 1) * 3);

    for (let j = 0; j <= trailSegments; j++) {
      const a = baseAngle + angleSpan * (j / trailSegments);
      positions[j * 3] = Math.cos(a) * radius;
      positions[j * 3 + 1] = baseY;
      positions[j * 3 + 2] = Math.sin(a) * radius;
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));

    const color = i % 3 === 0 ? 0x9b7bff : (i % 2 === 0 ? 0x38e8ff : 0xc6f6ff);
    const mat = new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.16
    });
    const line = new THREE.Line(geo, mat);
    line.position.z = zOffset;
    line.rotation.x = (Math.random() - 0.5) * 0.35;
    trailGroup.add(line);

    orbitalTrails.push({
      line,
      mat,
      speed: (0.5 + Math.random() * 1.1) * (i % 2 === 0 ? 1 : -1),
      baseOpacity: 0.12 + Math.random() * 0.14
    });
  }

  // =====================================================
  // 6. DEEP-SPACE LAYERED STARFIELD (spans full Z flight corridor)
  // =====================================================
  const starCount = isMobileNow() ? 650 : 1550;
  const starPositions = new Float32Array(starCount * 3);
  const starColors = new Float32Array(starCount * 3);
  const cCyan = new THREE.Color(0x5eeeff);
  const cViolet = new THREE.Color(0xb195ff);
  const cIce = new THREE.Color(0xdcecff);

  for (let i = 0; i < starCount; i++) {
    starPositions[i * 3] = (Math.random() - 0.5) * 56;
    starPositions[i * 3 + 1] = (Math.random() - 0.5) * 42;
    starPositions[i * 3 + 2] = (Math.random() - 0.72) * 86;
    const col = i % 5 === 0 ? cViolet : (i % 3 === 0 ? cCyan : cIce);
    starColors[i * 3] = col.r;
    starColors[i * 3 + 1] = col.g;
    starColors[i * 3 + 2] = col.b;
  }

  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
  starGeo.setAttribute('color', new THREE.BufferAttribute(starColors, 3));

  const starMat = new THREE.PointsMaterial({
    size: isMobileNow() ? 0.08 : 0.068,
    vertexColors: true,
    transparent: true,
    opacity: 0.78,
    depthWrite: false
  });
  const starField = new THREE.Points(starGeo, starMat);
  scene.add(starField);

  // =====================================================
  // CINEMATIC CAMERA & CORE CHOREOGRAPHY TRACKING
  // Computes exact live scroll progress inside #nova-journey
  // AND across every page section so camera never drifts!
  // =====================================================
  const journeyEl = document.getElementById('nova-journey');
  const sectionIds = ['reveal', 'nova-journey', 'intro', 'modes', 'capabilities', 'roadmap', 'companions', 'security', 'setup', 'finale'];

  function clamp01(v) { return Math.max(0, Math.min(1, v)); }
  function smoothstep(v) { v = clamp01(v); return v * v * (3 - 2 * v); }
  function lerp(a, b, t) { return a + (b - a) * t; }

  function getSceneState() {
    const sy = window.scrollY || 0;
    const vh = Math.max(1, window.innerHeight);
    const maxScroll = Math.max(1, document.documentElement.scrollHeight - vh);
    const globalP = clamp01(sy / maxScroll);

    // Measure exact progress of #nova-journey pinned 3D flight
    let inJourney = false;
    let journeyP = 0;
    let beforeJourney = true;
    let afterJourney = false;

    if (journeyEl) {
      const r = journeyEl.getBoundingClientRect();
      const range = Math.max(1, journeyEl.offsetHeight - vh);
      if (r.top > 0) {
        beforeJourney = true;
        journeyP = 0;
      } else if (r.bottom < vh) {
        beforeJourney = false;
        afterJourney = true;
        journeyP = 1;
      } else {
        beforeJourney = false;
        inJourney = true;
        journeyP = clamp01(-r.top / range);
      }
    }

    // Default target state (Hero)
    const state = {
      cam: [isMobileNow() ? 0.0 : 1.35, 0.35, isMobileNow() ? 10.6 : 9.2],
      tgt: [isMobileNow() ? 0.0 : -0.4, 0.0, 0.0],
      fov: isMobileNow() ? 56 : 46,
      roll: 0.0,
      corePos: [isMobileNow() ? 0.0 : 1.55, 0.0, 0.0],
      coreScale: 1.0,
      shardOpen: 0.0,     // 0 = closed crystalline sphere, 1 = wide-open fly-through portal
      ringSpread: 0.0,    // 0 = tight gyro, 1 = expanded dual-resonance rings
      tunnelVis: 0.0,     // 0..1 visibility of geometric portal tunnel
      vaultVis: 0.0,      // 0..1 visibility of security vault
      compRadius: 4.8,    // orbit radius of 4 companion satellites
      compZ: 0.0,
      accentHex: 0x38e8ff,
      fillHex: 0x9b7bff,
      inJourney,
      journeyP
    };

    if (beforeJourney && journeyEl) {
      // Transitioning from Hero (#reveal) toward the Core approach
      const r = journeyEl.getBoundingClientRect();
      const approach = smoothstep(clamp01(1 - r.top / vh));
      state.cam = [
        lerp(isMobileNow() ? 0.0 : 1.35, 0.0, approach),
        lerp(0.35, 0.45, approach),
        lerp(isMobileNow() ? 10.6 : 9.2, 7.6, approach)
      ];
      state.tgt = [lerp(isMobileNow() ? 0.0 : -0.4, 0.0, approach), 0, 0];
      state.corePos = [lerp(isMobileNow() ? 0.0 : 1.55, 0.0, approach), 0, 0];
      state.shardOpen = approach * 0.18;
      state.tunnelVis = approach * 0.25;
      return state;
    }

    if (inJourney) {
      // =================================================
      // 4-ACT CINEMATIC 3D FLIGHT INSIDE #nova-journey
      // =================================================
      if (journeyP < 0.25) {
        // ACT I: APPROACH & ORBIT — Core unlocks and breathes
        const t = smoothstep(journeyP / 0.25);
        state.cam = [lerp(0.0, -2.6, t), lerp(0.45, 1.35, t), lerp(7.6, 4.6, t)];
        state.tgt = [0, 0, lerp(0, -1.5, t)];
        state.fov = lerp(46, 51, t);
        state.roll = lerp(0, -0.055, t);
        state.corePos = [0, 0, 0];
        state.coreScale = lerp(1.0, 1.22, t);
        state.shardOpen = lerp(0.18, 0.85, t);
        state.tunnelVis = lerp(0.25, 0.72, t);
        state.accentHex = 0x38e8ff;
        state.fillHex = 0x9b7bff;
      } else if (journeyP < 0.55) {
        // ACT II: PORTAL DIVE — Fly STRAIGHT THROUGH the open Nova Core & Tunnel!
        const t = smoothstep((journeyP - 0.25) / 0.30);
        state.cam = [
          lerp(-2.6, 0.35, t) + Math.sin(t * Math.PI) * 0.9,
          lerp(1.35, -0.25, t),
          lerp(4.6, -15.8, t)
        ];
        state.tgt = [Math.sin(t * Math.PI) * 0.4, 0, lerp(-1.5, -24.0, t)];
        state.fov = lerp(51, 63, Math.sin(t * Math.PI * 0.85));
        state.roll = Math.sin(t * Math.PI) * 0.11;
        state.corePos = [0, 0, 0];
        state.coreScale = lerp(1.22, 1.55, t);
        state.shardOpen = lerp(0.85, 1.35, t);
        state.ringSpread = lerp(0.0, 0.8, t);
        state.tunnelVis = 0.92;
        state.vaultVis = smoothstep((t - 0.45) / 0.55) * 0.65;
        state.compZ = lerp(0, -16, t);
        state.accentHex = 0x4f8cff;
        state.fillHex = 0x38e8ff;
      } else if (journeyP < 0.80) {
        // ACT III: THE VAULT — Orbit around the Crystalline Fail-Closed Shield
        const t = smoothstep((journeyP - 0.55) / 0.25);
        const angle = lerp(-0.4, 1.25, t);
        state.cam = [
          Math.sin(angle) * 7.2,
          lerp(-0.25, 2.2, t),
          -22 + Math.cos(angle) * 7.2
        ];
        state.tgt = [0, 0, -22];
        state.fov = lerp(54, 47, t);
        state.roll = lerp(0.04, -0.04, t);
        state.corePos = [0, 0, -22];
        state.coreScale = 0.92;
        state.shardOpen = lerp(0.9, 0.12, t);
        state.tunnelVis = lerp(0.85, 0.25, t);
        state.vaultVis = 0.88;
        state.compZ = -22;
        state.compRadius = lerp(5.4, 4.2, t);
        state.accentHex = 0x34d399;
        state.fillHex = 0x38e8ff;
      } else {
        // ACT IV: HORIZON — Pull back to reveal Core + 4 Companion Satellites
        const t = smoothstep((journeyP - 0.80) / 0.20);
        state.cam = [lerp(6.8, 0.0, t), lerp(2.2, 0.8, t), lerp(-19.5, -13.2, t)];
        state.tgt = [0, 0, -22];
        state.fov = lerp(47, 48, t);
        state.roll = lerp(-0.04, 0, t);
        state.corePos = [0, 0, -22];
        state.coreScale = lerp(0.92, 1.12, t);
        state.shardOpen = lerp(0.12, 0.28, t);
        state.tunnelVis = lerp(0.25, 0.08, t);
        state.vaultVis = lerp(0.88, 0.18, t);
        state.compZ = -22;
        state.compRadius = lerp(4.2, 3.6, t);
        state.accentHex = 0x9b7bff;
        state.fillHex = 0x38e8ff;
      }
      return state;
    }

    // =================================================
    // POST-JOURNEY INTERACTIVE CHAPTERS (#intro -> #finale)
    // Smooth continuous camera & Core transformations per chapter!
    // =================================================
    const postStart = journeyEl ? (journeyEl.offsetTop + journeyEl.offsetHeight - vh) : vh;
    const postP = clamp01((sy - postStart) / Math.max(1, maxScroll - postStart));

    const POST_CHAPTERS = [
      // #intro
      { p: 0.00, cam: [0.0, 0.8, -13.2],   tgt: [0, 0, -22],     fov: 48, roll: 0.00, shard: 0.25, spread: 0.2, vault: 0.14, compR: 3.8, scale: 1.05, acc: 0x38e8ff, fill: 0x9b7bff },
      // #modes (Dual Online/Offline resonance rings)
      { p: 0.16, cam: [3.8, -0.8, -14.8],  tgt: [-0.4, 0, -22],  fov: 46, roll: 0.03, shard: 0.42, spread: 0.95, vault: 0.08, compR: 4.6, scale: 0.96, acc: 0x38e8ff, fill: 0x4f8cff },
      // #capabilities (Technical lattice perspective)
      { p: 0.34, cam: [-3.4, 2.6, -15.4],  tgt: [0.3, -0.2, -22],fov: 47, roll: -0.03,shard: 0.65, spread: 0.55, vault: 0.12, compR: 4.8, scale: 1.02, acc: 0x4f8cff, fill: 0x9b7bff },
      // #roadmap handoff
      { p: 0.54, cam: [0.0, 1.0, -11.5],   tgt: [0, 0, -22],     fov: 46, roll: 0.00, shard: 0.20, spread: 0.2, vault: 0.06, compR: 5.2, scale: 0.85, acc: 0x38e8ff, fill: 0x9b7bff },
      // #companions (4 glowing companion satellites sweep close!)
      { p: 0.72, cam: [-3.2, -1.0, -15.6], tgt: [0.2, 0.1, -22], fov: 49, roll: -0.02,shard: 0.18, spread: 0.3, vault: 0.05, compR: 2.55,scale: 0.90, acc: 0x9b7bff, fill: 0x34d399 },
      // #security (Crystalline Vault shield locks back in!)
      { p: 0.84, cam: [3.2, 0.9, -15.2],   tgt: [0, 0, -22],     fov: 46, roll: 0.02, shard: 0.04, spread: 0.15, vault: 0.78, compR: 4.8, scale: 0.94, acc: 0x34d399, fill: 0xfbbf24 },
      // #setup
      { p: 0.92, cam: [-1.4, 0.4, -13.6],  tgt: [0, 0, -22],     fov: 46, roll: 0.00, shard: 0.16, spread: 0.3, vault: 0.12, compR: 4.4, scale: 0.96, acc: 0x4f8cff, fill: 0x38e8ff },
      // #finale (Heroic centered Nova Core vista!)
      { p: 1.00, cam: [0.0, 0.25, -15.2],  tgt: [0, 0, -22],     fov: 50, roll: 0.00, shard: 0.34, spread: 0.65, vault: 0.16, compR: 3.3, scale: 1.24, acc: 0x38e8ff, fill: 0x9b7bff }
    ];

    let a = POST_CHAPTERS[0];
    let b = POST_CHAPTERS[POST_CHAPTERS.length - 1];
    for (let i = 0; i < POST_CHAPTERS.length - 1; i++) {
      if (postP >= POST_CHAPTERS[i].p && postP <= POST_CHAPTERS[i + 1].p) {
        a = POST_CHAPTERS[i];
        b = POST_CHAPTERS[i + 1];
        break;
      }
    }
    const t = smoothstep((postP - a.p) / Math.max(0.0001, b.p - a.p));

    state.cam = [lerp(a.cam[0], b.cam[0], t), lerp(a.cam[1], b.cam[1], t), lerp(a.cam[2], b.cam[2], t)];
    state.tgt = [lerp(a.tgt[0], b.tgt[0], t), lerp(a.tgt[1], b.tgt[1], t), lerp(a.tgt[2], b.tgt[2], t)];
    state.fov = lerp(a.fov, b.fov, t);
    state.roll = lerp(a.roll, b.roll, t);
    state.corePos = [0, 0, -22];
    state.coreScale = lerp(a.scale, b.scale, t);
    state.shardOpen = lerp(a.shard, b.shard, t);
    state.ringSpread = lerp(a.spread, b.spread, t);
    state.tunnelVis = 0.04;
    state.vaultVis = lerp(a.vault, b.vault, t);
    state.compRadius = lerp(a.compR, b.compR, t);
    state.compZ = -22;
    state.accentHex = t < 0.5 ? a.acc : b.acc;
    state.fillHex = t < 0.5 ? a.fill : b.fill;
    return state;
  }

  // Smooth runtime state
  const curCam = new THREE.Vector3(1.2, 0.4, 9.6);
  const curTgt = new THREE.Vector3(0, 0, 0);
  const curCorePos = new THREE.Vector3(1.55, 0, 0);
  const curAccent = new THREE.Color(0x38e8ff);
  const curFill = new THREE.Color(0x9b7bff);
  const tgtAccent = new THREE.Color(0x38e8ff);
  const tgtFill = new THREE.Color(0x9b7bff);
  let curFov = isMobileNow() ? 56 : 46;
  let curRoll = 0;
  let curShardOpen = 0;
  let curRingSpread = 0;
  let curTunnelVis = 0;
  let curVaultVis = 0;
  let curCompR = 4.8;
  let curCompZ = 0;

  let lastScrollY = window.scrollY || 0;
  let scrollVel = 0;
  let momentum = 0;
  const mouse = { x: 0, y: 0, tx: 0, ty: 0 };

  window.addEventListener('scroll', () => {
    const curY = window.scrollY || 0;
    const dy = curY - lastScrollY;
    lastScrollY = curY;
    scrollVel = Math.max(-1.4, Math.min(1.4, scrollVel + dy * 0.0026));
    if (reduceMotion && !document.hidden && !window.__novaRoadmapActive) {
      if (!rafId) rafId = requestAnimationFrame(renderFrame);
    }
  }, { passive: true });

  window.addEventListener('pointermove', (e) => {
    if (isMobileNow()) return;
    mouse.tx = (e.clientX / Math.max(1, window.innerWidth) - 0.5) * 2;
    mouse.ty = (e.clientY / Math.max(1, window.innerHeight) - 0.5) * 2;
  }, { passive: true });

  // Mobile address-bar resize guard (Codrops Demo 1 technique)
  window.addEventListener('resize', () => {
    const w = window.innerWidth;
    const h = Math.max(1, window.innerHeight);
    if (isMobileNow() && w === lastWidth) return;
    lastWidth = w;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, isMobileNow() ? 1.25 : 1.5));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }, { passive: true });

  let rafId = 0;
  let elapsed = 0;
  let lastTime = performance.now();

  function renderFrame(now) {
    if (window.__novaRoadmapActive) {
      if (!reduceMotion && !document.hidden) rafId = requestAnimationFrame(renderFrame);
      return;
    }

    const dt = Math.min(0.05, Math.max(0.001, (now - lastTime) / 1000));
    lastTime = now;
    if (!reduceMotion) elapsed += dt;

    // Velocity & inertia decay (Codrops Demo 1 physics)
    scrollVel *= 0.89;
    momentum = momentum * 0.91 + Math.abs(scrollVel) * 0.22;
    const energy = Math.min(1.8, momentum);

    mouse.x += (mouse.tx - mouse.x) * 0.055;
    mouse.y += (mouse.ty - mouse.y) * 0.055;

    const st = getSceneState();
    const damp = reduceMotion ? 1 : 0.085;

    // Publish live journey state for synchronized 3D typography in cinematic.js
    window.__nova3D = {
      inJourney: st.inJourney,
      journeyP: st.journeyP,
      energy
    };

    const px = isMobileNow() ? 0 : mouse.x * 0.45;
    const py = isMobileNow() ? 0 : -mouse.y * 0.30;

    curCam.lerp(new THREE.Vector3(st.cam[0] + px, st.cam[1] + py, st.cam[2]), damp);
    curTgt.lerp(new THREE.Vector3(st.tgt[0], st.tgt[1], st.tgt[2]), damp);
    curFov += ((isMobileNow() ? st.fov + 7 : st.fov) - curFov) * damp;
    curRoll += (st.roll - curRoll) * damp;

    camera.position.copy(curCam);
    camera.fov = curFov;
    camera.updateProjectionMatrix();
    camera.lookAt(curTgt);
    camera.rotation.z += curRoll;

    // Core position, scale & articulated glass shard bloom
    curCorePos.lerp(new THREE.Vector3(st.corePos[0], st.corePos[1], st.corePos[2]), damp * 1.15);
    coreGroup.position.copy(curCorePos);
    coreGroup.position.y += Math.sin(elapsed * 0.72) * 0.14;
    const nextScale = coreGroup.scale.x + (st.coreScale - coreGroup.scale.x) * damp;
    coreGroup.scale.setScalar(nextScale);

    curShardOpen += (st.shardOpen - curShardOpen) * damp;
    curRingSpread += (st.ringSpread - curRingSpread) * damp;
    curTunnelVis += (st.tunnelVis - curTunnelVis) * damp;
    curVaultVis += (st.vaultVis - curVaultVis) * damp;
    curCompR += (st.compRadius - curCompR) * damp;
    curCompZ += (st.compZ - curCompZ) * damp;

    const spinBoost = 1 + energy * 3.4;
    innerCore.rotation.y += dt * 0.42 * spinBoost;
    innerCore.rotation.x += dt * 0.22 * spinBoost;
    latticeMesh.rotation.y -= dt * 0.2 * spinBoost;
    latticeMesh.rotation.z += dt * 0.12 * spinBoost;

    // Articulated glass shards physically open into a fly-through portal
    for (const sh of shards) {
      const dist = sh.baseDist + curShardOpen * 2.45 + Math.sin(elapsed * 1.2 + sh.phase) * 0.06;
      sh.holder.position.copy(sh.dir).multiplyScalar(dist);
      sh.holder.lookAt(sh.dir.clone().multiplyScalar(dist + 3));
      sh.holder.rotation.z += curShardOpen * 0.8 + elapsed * 0.15;
    }

    // Gyroscopic resonance rings expand & tilt
    gyroRings[0].mesh.rotation.z += dt * 0.28 * spinBoost;
    gyroRings[1].mesh.rotation.x -= dt * 0.22 * spinBoost;
    gyroRings[2].mesh.rotation.y += dt * 0.16 * spinBoost;
    gyroRings[0].mesh.position.z = curRingSpread * 0.9;
    gyroRings[1].mesh.position.z = -curRingSpread * 0.9;
    gyroRings[2].mesh.scale.setScalar(1 + curRingSpread * 0.22);

    // Portal tunnel gates pulse & rotate smoothly
    for (const gate of tunnelGates) {
      gate.mat.opacity = curTunnelVis * (0.22 + 0.35 * (0.5 + 0.5 * Math.sin(elapsed * 1.4 - gate.idx * 0.45)));
      gate.mesh.rotation.z += dt * (0.08 + energy * 0.4) * (gate.idx % 2 === 0 ? 1 : -1);
    }

    // Crystalline Security Vault
    vaultMat.opacity = curVaultVis * 0.32;
    vaultRingMat.opacity = curVaultVis * 0.55;
    vaultOuter.rotation.y += dt * 0.18 * spinBoost;
    vaultRing1.rotation.z -= dt * 0.25 * spinBoost;
    vaultRing2.rotation.y += dt * 0.22 * spinBoost;

    // 4 Companion Energy Satellites
    companionGroup.position.set(curCorePos.x, curCorePos.y, curCompZ);
    for (let i = 0; i < companionOrbs.length; i++) {
      const c = companionOrbs[i];
      const a = c.angle + elapsed * (0.34 + i * 0.05) + energy * 0.6;
      c.group.position.set(
        Math.cos(a) * curCompR,
        c.yOff + Math.sin(elapsed * 1.1 + i) * 0.28,
        Math.sin(a) * curCompR
      );
      c.orb.rotation.y += dt * 0.9;
    }

    // Dynamic lighting & color morphing across chapters
    tgtAccent.setHex(st.accentHex);
    tgtFill.setHex(st.fillHex);
    curAccent.lerp(tgtAccent, 0.055);
    curFill.lerp(tgtFill, 0.055);

    keyLight.color.copy(curAccent);
    keyLight.position.set(curCorePos.x - 5, 4, curCorePos.z + 6);
    fillLight.color.copy(curFill);
    fillLight.position.set(curCorePos.x + 5.5, -3, curCorePos.z + 4);
    tunnelLight.color.copy(curAccent);

    innerMat.emissive.copy(curAccent);
    innerMat.emissiveIntensity = 0.85 + Math.sin(elapsed * 2.1) * 0.2 + energy * 0.55;
    shardEdgeMat.color.copy(curAccent);
    latticeMat.color.copy(curAccent);
    coreGlowMat.color.copy(curAccent);
    coreGlowMat.opacity = 0.55 + Math.sin(elapsed * 1.6) * 0.12 + Math.min(0.28, energy * 0.22);

    // Velocity-reactive curved light trails
    for (const tr of orbitalTrails) {
      tr.line.rotation.y += dt * (0.16 + energy * 1.9) * tr.speed;
      const targetOp = Math.min(0.92, tr.baseOpacity + energy * 0.62);
      tr.mat.opacity += (targetOp - tr.mat.opacity) * 0.14;
    }

    // Deep-space starfield subtle drift
    starField.rotation.y = elapsed * 0.012;

    renderer.render(scene, camera);

    rafId = 0;
    if (!document.hidden && (!reduceMotion || Math.abs(scrollVel) > 0.004)) {
      rafId = requestAnimationFrame(renderFrame);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = 0;
    } else if (!reduceMotion && !rafId) {
      lastTime = performance.now();
      rafId = requestAnimationFrame(renderFrame);
    }
  });

  window.__novaSceneReady = true;
  renderFrame(performance.now());
}

initWebGLWorld();
