/* Glass Nova — Universal Adaptive 3D World Engine
 * Adapts particle count, DPR, antialiasing, geometry complexity, lighting & FPS pacing
 * automatically across low-end Android, iPhones, integrated laptops, and gaming PCs via window.__novaPerf.
 * Zero layout thrashing: reads exclusively from cached layout measurements.
 */

const canvas = document.getElementById('nova-webgl');
const reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

function initFallback2D() {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  let W = 0, H = 0, DPR = 1;
  const isMobile = window.innerWidth < 700;
  const count = isMobile ? 55 : 120;
  const stars = Array.from({ length: count }, (_, i) => ({
    x: Math.random(),
    y: Math.random(),
    r: 0.7 + Math.random() * 1.5,
    phase: Math.random() * Math.PI * 2,
    tint: i % 4 === 0 ? '155, 123, 255' : (i % 3 === 0 ? '56, 232, 255' : '220, 240, 255')
  }));

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 1.25);
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = Math.max(1, Math.floor(W * DPR));
    canvas.height = Math.max(1, Math.floor(H * DPR));
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  function drawStatic() {
    ctx.clearRect(0, 0, W, H);
    const nebR = Math.max(W, H) * 0.56;
    const g1 = ctx.createRadialGradient(W * 0.28, H * 0.30, 10, W * 0.28, H * 0.30, nebR);
    g1.addColorStop(0, 'rgba(56, 232, 255, 0.14)');
    g1.addColorStop(1, 'rgba(4, 5, 9, 0)');
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, W, H);

    const g2 = ctx.createRadialGradient(W * 0.74, H * 0.70, 10, W * 0.74, H * 0.70, nebR);
    g2.addColorStop(0, 'rgba(155, 123, 255, 0.14)');
    g2.addColorStop(1, 'rgba(4, 5, 9, 0)');
    ctx.fillStyle = g2;
    ctx.fillRect(0, 0, W, H);

    for (const s of stars) {
      ctx.fillStyle = `rgba(${s.tint}, 0.65)`;
      ctx.beginPath();
      ctx.arc(s.x * W, s.y * H, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  window.addEventListener('resize', () => { resize(); drawStatic(); }, { passive: true });
  window.__novaWebGLFallback = true;
  resize();
  drawStatic();
}

function createGlowTexture(THREE, innerRgba, midRgba) {
  const c = document.createElement('canvas');
  c.width = 96;
  c.height = 96;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(48, 48, 2, 48, 48, 48);
  g.addColorStop(0, innerRgba);
  g.addColorStop(0.30, midRgba);
  g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 96, 96);
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

  const perf = window.__novaPerf;
  let cfg = perf ? perf.getConfig() : {
    tier: 'medium', maxDpr: 1.0, antialias: false, starCount: 360,
    trailCount: 8, gateCount: 7, shardCount: 8, ringSegments: 48,
    sphereSegments: 16, lights: 2, activeFps: 45, idleFps: 0, pauseOnIdle: true,
    enableParallax: false
  };

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: !!cfg.antialias,
      alpha: true,
      depth: true,
      stencil: false,
      powerPreference: cfg.tier === 'low' ? 'low-power' : 'high-performance'
    });
  } catch (_) {
    initFallback2D();
    return;
  }

  const isMobileNow = () => window.innerWidth < 768;
  let lastWidth = window.innerWidth;

  function applyRendererQuality() {
    const dpr = Math.min(window.devicePixelRatio || 1, cfg.maxDpr);
    renderer.setPixelRatio(dpr);
    renderer.setSize(window.innerWidth, window.innerHeight, false);
  }
  applyRendererQuality();
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.08;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x04050a, 0.032);

  const camera = new THREE.PerspectiveCamera(
    isMobileNow() ? 56 : 46,
    window.innerWidth / Math.max(1, window.innerHeight),
    0.1,
    160
  );
  camera.position.set(1.2, 0.4, 9.6);

  // =====================================================
  // ADAPTIVE LIGHTING RIG (1 to 3 lights by device tier)
  // =====================================================
  scene.add(new THREE.AmbientLight(0x5278b8, 0.42));

  const keyLight = new THREE.PointLight(0x38e8ff, 34, 42);
  keyLight.position.set(-5.5, 4.0, 7.0);
  scene.add(keyLight);

  let fillLight = null;
  if (cfg.lights >= 2) {
    fillLight = new THREE.PointLight(0x9b7bff, 28, 42);
    fillLight.position.set(6.0, -3.5, 4.0);
    scene.add(fillLight);
  }

  let vaultKeyLight = null;
  if (cfg.lights >= 3) {
    vaultKeyLight = new THREE.PointLight(0x38e8ff, 30, 38);
    vaultKeyLight.position.set(-4.5, 3.5, -16);
    scene.add(vaultKeyLight);
  }

  const cyanGlowTex = createGlowTexture(THREE, 'rgba(86, 232, 255, 0.62)', 'rgba(56, 232, 255, 0.14)');
  const orbGlowTex = createGlowTexture(THREE, 'rgba(220, 248, 255, 0.82)', 'rgba(110, 220, 255, 0.16)');

  // Shared geometries (reused across Entry & Vault Cores to minimize GPU memory!)
  const sharedCoreGeo = new THREE.SphereGeometry(0.62, cfg.sphereSegments, Math.max(10, Math.floor(cfg.sphereSegments * 0.75)));
  const sharedLatticeGeo = new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.08, 1));
  const sharedShardGeo = new THREE.OctahedronGeometry(0.56, 0);
  const sharedShardEdgeGeo = new THREE.EdgesGeometry(sharedShardGeo);
  const sharedRingGeos = [
    new THREE.TorusGeometry(2.25, 0.015, 6, cfg.ringSegments),
    new THREE.TorusGeometry(2.85, 0.012, 6, cfg.ringSegments),
    new THREE.TorusGeometry(3.50, 0.009, 6, cfg.ringSegments)
  ];

  const ALL_SHARD_DIRS = [
    [ 1,  0,  0], [-1,  0,  0], [ 0,  1,  0], [ 0, -1,  0],
    [ 0.7,  0.7,  0.2], [-0.7,  0.7,  0.2], [ 0.7, -0.7,  0.2], [-0.7, -0.7,  0.2],
    [ 0.65,  0.25, -0.7], [-0.65,  0.25, -0.7], [ 0.55, -0.5,  0.65], [-0.55, -0.5,  0.65]
  ].slice(0, cfg.shardCount);

  const sharedShardMat = new THREE.MeshStandardMaterial({
    color: 0x071224,
    emissive: 0x0d2d4a,
    emissiveIntensity: 0.42,
    metalness: 0.88,
    roughness: 0.14,
    transparent: true,
    opacity: 0.52,
    flatShading: true
  });

  function buildNovaCoreAssembly() {
    const group = new THREE.Group();

    const glowMat = new THREE.SpriteMaterial({
      map: cyanGlowTex,
      color: 0x38e8ff,
      transparent: true,
      opacity: 0.44,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    });
    const glowSprite = new THREE.Sprite(glowMat);
    glowSprite.scale.set(5.8, 5.8, 1);
    group.add(glowSprite);

    const coreMat = new THREE.MeshStandardMaterial({
      color: 0x06101f,
      emissive: 0x0d5c78,
      emissiveIntensity: 0.62,
      metalness: 0.9,
      roughness: 0.1,
      transparent: true,
      opacity: 0.92
    });
    const coreSphere = new THREE.Mesh(sharedCoreGeo, coreMat);
    group.add(coreSphere);

    const latticeMat = new THREE.LineBasicMaterial({
      color: 0x38e8ff,
      transparent: true,
      opacity: 0.34
    });
    const lattice = new THREE.LineSegments(sharedLatticeGeo, latticeMat);
    group.add(lattice);

    const ringDefs = [
      { color: 0x38e8ff, op: 0.56, rot: [1.1, 0.25, 0.1] },
      { color: 0x9b7bff, op: 0.44, rot: [0.65, -0.6, 0.9] },
      { color: 0x4f8cff, op: 0.28, rot: [-0.85, 0.45, -0.3] }
    ];
    const rings = ringDefs.map((rd, idx) => {
      const mat = new THREE.MeshBasicMaterial({ color: rd.color, transparent: true, opacity: rd.op });
      const mesh = new THREE.Mesh(sharedRingGeos[idx], mat);
      mesh.rotation.set(rd.rot[0], rd.rot[1], rd.rot[2]);
      group.add(mesh);
      return { mesh, mat };
    });

    const shardEdgeMat = new THREE.LineBasicMaterial({
      color: 0x38e8ff,
      transparent: true,
      opacity: 0.44
    });
    const shards = ALL_SHARD_DIRS.map((d, idx) => {
      const dir = new THREE.Vector3(d[0], d[1], d[2]).normalize();
      const holder = new THREE.Group();
      const mesh = new THREE.Mesh(sharedShardGeo, sharedShardMat);
      mesh.scale.set(1.12, 0.74, 0.36);
      const edge = new THREE.LineSegments(sharedShardEdgeGeo, shardEdgeMat);
      edge.scale.copy(mesh.scale).multiplyScalar(1.02);
      holder.add(mesh, edge);
      holder.position.copy(dir).multiplyScalar(1.48);
      holder.lookAt(dir.clone().multiplyScalar(4));
      group.add(holder);
      return { holder, dir, baseDist: 1.48, phase: idx * 0.52 };
    });

    return { group, glowMat, coreMat, coreSphere, lattice, latticeMat, rings, shards, shardEdgeMat };
  }

  // Dual-Zone Cores (Entry at z=0, Vault at z=-22) with automatic frustum visibility culling
  const entryCore = buildNovaCoreAssembly();
  scene.add(entryCore.group);

  const vaultCore = buildNovaCoreAssembly();
  vaultCore.group.position.set(0, 0, -22);
  vaultCore.group.visible = false;
  scene.add(vaultCore.group);

  // Adaptive Portal Tunnel Gates
  const tunnelGroup = new THREE.Group();
  tunnelGroup.visible = false;
  scene.add(tunnelGroup);

  const tunnelGates = [];
  const hexGateGeo = new THREE.TorusGeometry(3.05, 0.015, 5, 6);
  const circleGateGeo = new THREE.TorusGeometry(3.05, 0.014, 5, Math.min(36, cfg.ringSegments));

  for (let i = 0; i < cfg.gateCount; i++) {
    const zPos = -3.0 - i * (16.5 / Math.max(1, cfg.gateCount));
    const mat = new THREE.MeshBasicMaterial({
      color: i % 3 === 0 ? 0x9b7bff : 0x38e8ff,
      transparent: true,
      opacity: 0.0
    });
    const gate = new THREE.Mesh(i % 2 === 0 ? hexGateGeo : circleGateGeo, mat);
    gate.position.set(Math.sin(i * 0.5) * 0.4, Math.cos(i * 0.4) * 0.3, zPos);
    gate.rotation.z = i * 0.28;
    tunnelGroup.add(gate);
    tunnelGates.push({ mesh: gate, mat, idx: i });
  }

  // Crystalline Security Vault (at z=-22)
  const vaultGroup = new THREE.Group();
  vaultGroup.position.set(0, 0, -22);
  vaultGroup.visible = false;
  scene.add(vaultGroup);

  const vaultMat = new THREE.MeshBasicMaterial({ color: 0x34d399, wireframe: true, transparent: true, opacity: 0.0 });
  const vaultOuter = new THREE.Mesh(new THREE.DodecahedronGeometry(3.45, 0), vaultMat);
  vaultGroup.add(vaultOuter);

  const vaultRingMat = new THREE.MeshBasicMaterial({ color: 0x38e8ff, transparent: true, opacity: 0.0 });
  const vaultRing1 = new THREE.Mesh(new THREE.TorusGeometry(4.15, 0.018, 5, 6), vaultRingMat);
  const vaultRing2 = new THREE.Mesh(new THREE.TorusGeometry(4.65, 0.014, 5, 6), vaultRingMat);
  vaultRing2.rotation.x = Math.PI * 0.5;
  vaultGroup.add(vaultRing1, vaultRing2);

  // 4 Refined Companion Energy Satellites
  const companionGroup = new THREE.Group();
  companionGroup.position.set(0, 0, -22);
  companionGroup.visible = false;
  scene.add(companionGroup);

  const compOrbGeo = new THREE.SphereGeometry(0.11, 14, 12);
  const compMicroRingGeo = new THREE.TorusGeometry(0.24, 0.008, 5, 24);
  const compColors = [0x38e8ff, 0x9b7bff, 0x34d399, 0x4f8cff];
  const companionOrbs = compColors.map((hex, i) => {
    const g = new THREE.Group();
    const orb = new THREE.Mesh(
      compOrbGeo,
      new THREE.MeshStandardMaterial({ color: 0x0b1628, emissive: hex, emissiveIntensity: 0.9, roughness: 0.18, metalness: 0.78 })
    );
    const microRing = new THREE.Mesh(compMicroRingGeo, new THREE.MeshBasicMaterial({ color: hex, transparent: true, opacity: 0.65 }));
    microRing.rotation.set(1.1, i * 0.6, 0.2);
    const halo = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: orbGlowTex, color: hex, transparent: true, opacity: 0.54, blending: THREE.AdditiveBlending, depthWrite: false })
    );
    halo.scale.set(1.1, 1.1, 1);
    g.add(orb, microRing, halo);
    companionGroup.add(g);
    return { group: g, microRing, angle: (i * Math.PI) / 2, yOff: (i % 2 === 0 ? 1 : -1) * 0.72 };
  });

  // Adaptive Velocity-Reactive Light Trails
  const trailGroup = new THREE.Group();
  scene.add(trailGroup);

  const trailSegments = 14;
  const orbitalTrails = [];
  for (let i = 0; i < cfg.trailCount; i++) {
    const radius = 2.6 + (i % 6) * 0.62 + Math.random() * 0.35;
    const baseY = (i % 2 === 0 ? 1 : -1) * (0.4 + Math.random() * 2.5);
    const zOffset = -Math.random() * 26;
    const angleSpan = 0.32 + Math.random() * 0.36;
    const baseAngle = (i / Math.max(1, cfg.trailCount)) * Math.PI * 2;
    const positions = new Float32Array((trailSegments + 1) * 3);
    for (let j = 0; j <= trailSegments; j++) {
      const a = baseAngle + angleSpan * (j / trailSegments);
      positions[j * 3] = Math.cos(a) * radius;
      positions[j * 3 + 1] = baseY;
      positions[j * 3 + 2] = Math.sin(a) * radius;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.LineBasicMaterial({
      color: i % 3 === 0 ? 0x9b7bff : (i % 2 === 0 ? 0x38e8ff : 0xbceeff),
      transparent: true,
      opacity: 0.14
    });
    const line = new THREE.Line(geo, mat);
    line.position.z = zOffset;
    line.rotation.x = (Math.random() - 0.5) * 0.35;
    trailGroup.add(line);
    orbitalTrails.push({ line, mat, speed: (0.5 + Math.random() * 1.0) * (i % 2 === 0 ? 1 : -1), baseOpacity: 0.10 + Math.random() * 0.10 });
  }

  // Adaptive Deep-Space Starfield
  const starPositions = new Float32Array(cfg.starCount * 3);
  const starColors = new Float32Array(cfg.starCount * 3);
  const cCyan = new THREE.Color(0x5eeeff);
  const cViolet = new THREE.Color(0xb195ff);
  const cIce = new THREE.Color(0xdcecff);

  for (let i = 0; i < cfg.starCount; i++) {
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
  const starField = new THREE.Points(
    starGeo,
    new THREE.PointsMaterial({ size: isMobileNow() ? 0.08 : 0.064, vertexColors: true, transparent: true, opacity: 0.76, depthWrite: false })
  );
  scene.add(starField);

  // Live Tier Downgrade Listener (triggered by FPS Watchdog if device stutters)
  if (perf) {
    perf.onTierChange((nextCfg) => {
      cfg = nextCfg;
      applyRendererQuality();
      if (cfg.tier === 'low') {
        trailGroup.visible = false;
      }
    });
  }

  // =====================================================
  // ZERO-REFLOW CACHED CHAPTER CHOREOGRAPHY
  // =====================================================
  const POST_CHAPTER_DEFS = [
    { id: 'intro',        cam: [0.0, 0.8, -13.0],   tgt: [0, 0, -22],     fov: 48, roll: 0.00, shard: 0.24, spread: 0.22, vault: 0.14, compR: 4.1, scale: 1.04, acc: 0x38e8ff, fill: 0x9b7bff },
    { id: 'modes',        cam: [3.8, -0.7, -14.6],  tgt: [-0.3, 0, -22],  fov: 46, roll: 0.03, shard: 0.42, spread: 0.92, vault: 0.08, compR: 4.8, scale: 0.96, acc: 0x38e8ff, fill: 0x4f8cff },
    { id: 'capabilities', cam: [-3.4, 2.5, -15.2],  tgt: [0.3, -0.2, -22],fov: 47, roll: -0.03,shard: 0.62, spread: 0.55, vault: 0.12, compR: 5.0, scale: 1.00, acc: 0x4f8cff, fill: 0x9b7bff },
    { id: 'roadmap',      cam: [0.0, 1.0, -11.5],   tgt: [0, 0, -22],     fov: 46, roll: 0.00, shard: 0.20, spread: 0.20, vault: 0.06, compR: 5.2, scale: 0.86, acc: 0x38e8ff, fill: 0x9b7bff },
    { id: 'companions',   cam: [-3.2, -0.9, -15.4], tgt: [0.2, 0.1, -22], fov: 49, roll: -0.02,shard: 0.18, spread: 0.30, vault: 0.05, compR: 3.45,scale: 0.92, acc: 0x9b7bff, fill: 0x34d399 },
    { id: 'security',     cam: [3.2, 0.85, -15.0],  tgt: [0, 0, -22],     fov: 46, roll: 0.02, shard: 0.05, spread: 0.15, vault: 0.78, compR: 4.9, scale: 0.94, acc: 0x34d399, fill: 0xfbbf24 },
    { id: 'setup',        cam: [-1.4, 0.4, -13.6],  tgt: [0, 0, -22],     fov: 46, roll: 0.00, shard: 0.16, spread: 0.30, vault: 0.12, compR: 4.5, scale: 0.96, acc: 0x4f8cff, fill: 0x38e8ff },
    { id: 'finale',       cam: [0.0, 0.25, -15.0],  tgt: [0, 0, -22],     fov: 50, roll: 0.00, shard: 0.32, spread: 0.62, vault: 0.14, compR: 3.7, scale: 1.18, acc: 0x38e8ff, fill: 0x9b7bff }
  ];

  function clamp01(v) { return Math.max(0, Math.min(1, v)); }
  function smoothstep(v) { v = clamp01(v); return v * v * (3 - 2 * v); }
  function lerp(a, b, t) { return a + (b - a) * t; }

  const tmpColA = new THREE.Color();
  const tmpColB = new THREE.Color();
  const desiredAcc = new THREE.Color(0x38e8ff);
  const desiredFill = new THREE.Color(0x9b7bff);

  function setBlendedColors(hexA, hexB, fillA, fillB, t) {
    tmpColA.setHex(hexA); tmpColB.setHex(hexB);
    desiredAcc.copy(tmpColA).lerp(tmpColB, t);
    tmpColA.setHex(fillA); tmpColB.setHex(fillB);
    desiredFill.copy(tmpColA).lerp(tmpColB, t);
  }

  // Reusable state object (zero per-frame heap allocations!)
  const state = {
    camX: 1.35, camY: 0.35, camZ: 9.2,
    tgtX: -0.4, tgtY: 0.0, tgtZ: 0.0,
    fov: 46, roll: 0, entryX: 1.55, entryScale: 1.0, entryShardOpen: 0,
    vaultScale: 0.85, vaultShardOpen: 0.08, ringSpread: 0,
    tunnelVis: 0, vaultVis: 0, compRadius: 4.4, inJourney: false, journeyP: 0
  };

  function computeSceneState(sy) {
    const lay = perf ? perf.layout : null;
    const vh = lay ? lay.vh : Math.max(1, window.innerHeight);
    const jTop = lay ? lay.journeyTop : vh;
    const jBottom = lay ? (lay.journeyTop + lay.journeyHeight) : vh * 5;
    const jRange = lay ? lay.journeyRange : Math.max(1, vh * 3);

    const beforeJourney = sy < jTop;
    const inJourney = sy >= jTop && sy <= (jBottom - vh);
    const journeyP = inJourney ? clamp01((sy - jTop) / jRange) : (beforeJourney ? 0 : 1);

    state.inJourney = inJourney;
    state.journeyP = journeyP;
    const mob = isMobileNow();

    if (beforeJourney) {
      const approach = smoothstep(clamp01(1 - (jTop - sy) / vh));
      state.camX = lerp(mob ? 0.0 : 1.35, 0.0, approach);
      state.camY = lerp(0.35, 0.45, approach);
      state.camZ = lerp(mob ? 10.6 : 9.2, 7.6, approach);
      state.tgtX = lerp(mob ? 0.0 : -0.4, 0.0, approach);
      state.tgtY = 0; state.tgtZ = 0;
      state.fov = 46; state.roll = 0;
      state.entryX = lerp(mob ? 0.0 : 1.55, 0.0, approach);
      state.entryScale = 1.0; state.entryShardOpen = approach * 0.18;
      state.vaultScale = 0.85; state.vaultShardOpen = 0.08;
      state.ringSpread = 0; state.tunnelVis = approach * 0.22; state.vaultVis = 0; state.compRadius = 4.4;
      setBlendedColors(0x38e8ff, 0x38e8ff, 0x9b7bff, 0x9b7bff, 0);
      return state;
    }

    if (inJourney) {
      state.entryX = 0;
      if (journeyP < 0.25) {
        const t = smoothstep(journeyP / 0.25);
        state.camX = lerp(0.0, -2.5, t); state.camY = lerp(0.45, 1.25, t); state.camZ = lerp(7.6, 4.6, t);
        state.tgtX = 0; state.tgtY = 0; state.tgtZ = lerp(0, -2.0, t);
        state.fov = lerp(46, 51, t); state.roll = lerp(0, -0.05, t);
        state.entryScale = lerp(1.0, 1.2, t); state.entryShardOpen = lerp(0.18, 0.85, t);
        state.ringSpread = 0; state.tunnelVis = lerp(0.22, 0.72, t); state.vaultVis = 0;
        setBlendedColors(0x38e8ff, 0x4f8cff, 0x9b7bff, 0x38e8ff, t * 0.4);
      } else if (journeyP < 0.55) {
        const t = smoothstep((journeyP - 0.25) / 0.30);
        state.camX = lerp(-2.5, 0.3, t) + Math.sin(t * Math.PI) * 0.85;
        state.camY = lerp(1.25, -0.2, t); state.camZ = lerp(4.6, -15.4, t);
        state.tgtX = Math.sin(t * Math.PI) * 0.35; state.tgtY = 0; state.tgtZ = lerp(-2.0, -22.0, t);
        state.fov = lerp(51, 63, Math.sin(t * Math.PI * 0.85)); state.roll = Math.sin(t * Math.PI) * 0.1;
        state.entryScale = lerp(1.2, 1.5, t); state.entryShardOpen = lerp(0.85, 1.45, t);
        state.vaultScale = lerp(0.65, 0.92, t); state.vaultShardOpen = lerp(0.45, 0.12, t);
        state.ringSpread = lerp(0.0, 0.75, t); state.tunnelVis = 0.90;
        state.vaultVis = smoothstep((t - 0.4) / 0.6) * 0.65;
        setBlendedColors(0x4f8cff, 0x34d399, 0x38e8ff, 0x38e8ff, t * 0.5);
      } else if (journeyP < 0.80) {
        const t = smoothstep((journeyP - 0.55) / 0.25);
        const angle = lerp(-0.35, 1.15, t);
        state.camX = Math.sin(angle) * 7.2; state.camY = lerp(-0.2, 2.0, t); state.camZ = -22 + Math.cos(angle) * 7.2;
        state.tgtX = 0; state.tgtY = 0; state.tgtZ = -22;
        state.fov = lerp(53, 47, t); state.roll = lerp(0.04, -0.035, t);
        state.vaultScale = 0.92; state.vaultShardOpen = lerp(0.12, 0.08, t);
        state.tunnelVis = lerp(0.82, 0.20, t); state.vaultVis = 0.86; state.compRadius = lerp(5.2, 4.3, t);
        setBlendedColors(0x34d399, 0x38e8ff, 0x38e8ff, 0x9b7bff, t * 0.5);
      } else {
        const t = smoothstep((journeyP - 0.80) / 0.20);
        state.camX = lerp(6.5, 0.0, t); state.camY = lerp(2.0, 0.8, t); state.camZ = lerp(-19.0, -13.0, t);
        state.tgtX = 0; state.tgtY = 0; state.tgtZ = -22;
        state.fov = lerp(47, 48, t); state.roll = lerp(-0.035, 0, t);
        state.vaultScale = lerp(0.92, 1.04, t); state.vaultShardOpen = lerp(0.08, 0.24, t);
        state.tunnelVis = lerp(0.20, 0.0, t); state.vaultVis = lerp(0.86, 0.14, t); state.compRadius = lerp(4.3, 4.1, t);
        setBlendedColors(0x38e8ff, 0x9b7bff, 0x9b7bff, 0x38e8ff, t * 0.45);
      }
      return state;
    }

    // Post-Journey cached DOM section sync (zero layout reflow!)
    const secs = lay ? lay.sections : {};
    const probeY = sy + vh * 0.35;
    let a = POST_CHAPTER_DEFS[0];
    let b = POST_CHAPTER_DEFS[POST_CHAPTER_DEFS.length - 1];
    let t = 0;

    for (let i = 0; i < POST_CHAPTER_DEFS.length - 1; i++) {
      const s0 = secs[POST_CHAPTER_DEFS[i].id];
      const s1 = secs[POST_CHAPTER_DEFS[i + 1].id];
      if (!s0 || !s1) continue;
      if (probeY <= s0.top && i === 0) { a = b = POST_CHAPTER_DEFS[0]; t = 0; break; }
      if (probeY >= s0.top && probeY <= s1.top) {
        a = POST_CHAPTER_DEFS[i]; b = POST_CHAPTER_DEFS[i + 1];
        t = smoothstep((probeY - s0.top) / Math.max(1, s1.top - s0.top));
        break;
      }
      if (i === POST_CHAPTER_DEFS.length - 2 && probeY >= s1.top) { a = b = POST_CHAPTER_DEFS[i + 1]; t = 1; }
    }

    state.camX = lerp(a.cam[0], b.cam[0], t); state.camY = lerp(a.cam[1], b.cam[1], t); state.camZ = lerp(a.cam[2], b.cam[2], t);
    state.tgtX = lerp(a.tgt[0], b.tgt[0], t); state.tgtY = lerp(a.tgt[1], b.tgt[1], t); state.tgtZ = lerp(a.tgt[2], b.tgt[2], t);
    state.fov = lerp(a.fov, b.fov, t); state.roll = lerp(a.roll, b.roll, t);
    state.vaultScale = lerp(a.scale, b.scale, t); state.vaultShardOpen = lerp(a.shard, b.shard, t);
    state.ringSpread = lerp(a.spread, b.spread, t); state.tunnelVis = 0; state.vaultVis = lerp(a.vault, b.vault, t);
    state.compRadius = lerp(a.compR, b.compR, t);
    setBlendedColors(a.acc, b.acc, a.fill, b.fill, t);
    return state;
  }

  // Runtime smoothing state
  const curCam = new THREE.Vector3(1.2, 0.4, 9.6);
  const curTgt = new THREE.Vector3(0, 0, 0);
  const targetCamVec = new THREE.Vector3();
  const targetTgtVec = new THREE.Vector3();
  const curAccent = new THREE.Color(0x38e8ff);
  const curFill = new THREE.Color(0x9b7bff);

  let curFov = isMobileNow() ? 56 : 46;
  let curRoll = 0, curEntryX = isMobileNow() ? 0 : 1.55;
  let curEntryShard = 0, curVaultShard = 0.08, curRingSpread = 0;
  let curTunnelVis = 0, curVaultVis = 0, curCompR = 4.4;

  let currentScrollY = window.scrollY || 0;
  let lastScrollY = currentScrollY;
  let scrollVel = 0, momentum = 0;
  let lastActivityTime = performance.now();
  let lastRenderTime = 0;
  const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
  let rafId = 0;

  function wake(markActive) {
    if (markActive) lastActivityTime = performance.now();
    if (!document.hidden && !window.__novaRoadmapActive && !rafId) {
      lastTime = performance.now();
      rafId = requestAnimationFrame(renderFrame);
    }
  }

  window.addEventListener('scroll', () => {
    currentScrollY = window.scrollY || 0;
    const dy = currentScrollY - lastScrollY;
    lastScrollY = currentScrollY;
    scrollVel = Math.max(-1.4, Math.min(1.4, scrollVel + dy * 0.0026));
    wake(true);
  }, { passive: true });

  window.addEventListener('pointermove', (e) => {
    if (!cfg.enableParallax || isMobileNow()) return;
    mouse.tx = (e.clientX / Math.max(1, window.innerWidth) - 0.5) * 2;
    mouse.ty = (e.clientY / Math.max(1, window.innerHeight) - 0.5) * 2;
    wake(true);
  }, { passive: true });

  window.addEventListener('resize', () => {
    const w = window.innerWidth;
    const h = Math.max(1, window.innerHeight);
    if (isMobileNow() && w === lastWidth) return;
    lastWidth = w;
    applyRendererQuality();
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    wake(true);
  }, { passive: true });

  function updateCoreAssembly(core, shardOpen, ringSpread, scaleTarget, dt, elapsed, spinBoost, damp) {
    if (!core.group.visible) return;
    const nextS = core.group.scale.x + (scaleTarget - core.group.scale.x) * damp;
    core.group.scale.setScalar(nextS);
    core.group.position.y = Math.sin(elapsed * 0.72) * 0.12;

    core.coreSphere.rotation.y += dt * 0.3 * spinBoost;
    core.lattice.rotation.y -= dt * 0.18 * spinBoost;

    for (const sh of core.shards) {
      const dist = sh.baseDist + shardOpen * 2.45 + Math.sin(elapsed * 1.1 + sh.phase) * 0.05;
      sh.holder.position.copy(sh.dir).multiplyScalar(dist);
      sh.holder.lookAt(sh.dir.x * (dist + 3), sh.dir.y * (dist + 3), sh.dir.z * (dist + 3));
      sh.holder.rotation.z += shardOpen * 0.6 + elapsed * 0.12;
    }

    core.rings[0].mesh.rotation.z += dt * 0.25 * spinBoost;
    core.rings[1].mesh.rotation.x -= dt * 0.20 * spinBoost;
    core.rings[2].mesh.rotation.y += dt * 0.14 * spinBoost;
    core.rings[0].mesh.position.z = ringSpread * 0.85;
    core.rings[1].mesh.position.z = -ringSpread * 0.85;
    core.rings[2].mesh.scale.setScalar(1 + ringSpread * 0.2);

    core.coreMat.emissive.copy(curAccent);
    core.shardEdgeMat.color.copy(curAccent);
    core.latticeMat.color.copy(curAccent);
    core.glowMat.color.copy(curAccent);
  }

  let elapsed = 0;
  let lastTime = performance.now();

  function renderFrame(now) {
    rafId = 0;
    if (document.hidden) return;
    if (window.__novaRoadmapActive) {
      if (!reduceMotion && !cfg.pauseOnIdle) rafId = requestAnimationFrame(renderFrame);
      return;
    }

    const isIdle = (now - lastActivityTime) > 2000;
    const targetFps = isIdle ? (cfg.idleFps || 0) : cfg.activeFps;

    // Frame-rate pacing (adaptive FPS cap + idle throttling)
    if (targetFps > 0) {
      const minInterval = 1000 / targetFps;
      if (lastRenderTime && (now - lastRenderTime) < (minInterval - 1.5)) {
        rafId = requestAnimationFrame(renderFrame);
        return;
      }
    }

    const frameDeltaMs = lastRenderTime ? (now - lastRenderTime) : 16.6;
    lastRenderTime = now;
    if (perf && !isIdle) perf.reportFrameDelta(frameDeltaMs);

    const dt = Math.min(0.05, Math.max(0.001, (now - lastTime) / 1000));
    lastTime = now;
    if (!reduceMotion) elapsed += dt;

    scrollVel *= 0.89;
    momentum = momentum * 0.91 + Math.abs(scrollVel) * 0.22;
    const energy = Math.min(1.6, momentum);

    if (cfg.enableParallax && !isMobileNow()) {
      mouse.x += (mouse.tx - mouse.x) * 0.055;
      mouse.y += (mouse.ty - mouse.y) * 0.055;
    }

    const st = computeSceneState(currentScrollY);
    const damp = reduceMotion ? 0.2 : 0.1;

    const px = (cfg.enableParallax && !isMobileNow()) ? mouse.x * 0.38 : 0;
    const py = (cfg.enableParallax && !isMobileNow()) ? -mouse.y * 0.25 : 0;

    targetCamVec.set(st.camX + px, st.camY + py, st.camZ);
    targetTgtVec.set(st.tgtX, st.tgtY, st.tgtZ);

    curCam.lerp(targetCamVec, damp);
    curTgt.lerp(targetTgtVec, damp);
    curFov += ((isMobileNow() ? st.fov + 7 : st.fov) - curFov) * damp;
    curRoll += (st.roll - curRoll) * damp;

    camera.position.copy(curCam);
    if (Math.abs(camera.fov - curFov) > 0.03) {
      camera.fov = curFov;
      camera.updateProjectionMatrix();
    }
    camera.lookAt(curTgt);
    camera.rotation.z += curRoll;

    // Frustum / Zone Visibility Culling (cuts draw calls in half!)
    entryCore.group.visible = curCam.z > -9.5;
    vaultCore.group.visible = curCam.z < 5.5;
    companionGroup.visible = curCam.z < -4.0;

    curEntryX += (st.entryX - curEntryX) * damp;
    entryCore.group.position.x = curEntryX;
    curEntryShard += (st.entryShardOpen - curEntryShard) * damp;
    curVaultShard += (st.vaultShardOpen - curVaultShard) * damp;
    curRingSpread += (st.ringSpread - curRingSpread) * damp;
    curTunnelVis += (st.tunnelVis - curTunnelVis) * damp;
    curVaultVis += (st.vaultVis - curVaultVis) * damp;
    curCompR += (st.compRadius - curCompR) * damp;

    curAccent.lerp(desiredAcc, 0.07);
    curFill.lerp(desiredFill, 0.07);
    keyLight.color.copy(curAccent);
    if (fillLight) fillLight.color.copy(curFill);
    if (vaultKeyLight) vaultKeyLight.color.copy(curAccent);

    const spinBoost = 1 + energy * 3.0;
    updateCoreAssembly(entryCore, curEntryShard, curRingSpread * 0.5, st.entryScale, dt, elapsed, spinBoost, damp);
    updateCoreAssembly(vaultCore, curVaultShard, curRingSpread, st.vaultScale, dt, elapsed, spinBoost, damp);

    // Portal tunnel culling + update
    tunnelGroup.visible = curTunnelVis > 0.02;
    if (tunnelGroup.visible) {
      for (const gate of tunnelGates) {
        gate.mat.opacity = curTunnelVis * (0.20 + 0.30 * (0.5 + 0.5 * Math.sin(elapsed * 1.3 - gate.idx * 0.45)));
        gate.mesh.rotation.z += dt * (0.07 + energy * 0.32) * (gate.idx % 2 === 0 ? 1 : -1);
      }
    }

    // Security Vault culling + update
    vaultGroup.visible = curVaultVis > 0.02 && curCam.z < -4.0;
    if (vaultGroup.visible) {
      vaultMat.opacity = curVaultVis * 0.28;
      vaultRingMat.opacity = curVaultVis * 0.50;
      vaultOuter.rotation.y += dt * 0.16 * spinBoost;
      vaultRing1.rotation.z -= dt * 0.22 * spinBoost;
      vaultRing2.rotation.y += dt * 0.18 * spinBoost;
    }

    // Companion Satellites
    if (companionGroup.visible) {
      for (let i = 0; i < companionOrbs.length; i++) {
        const c = companionOrbs[i];
        const a = c.angle + elapsed * (0.30 + i * 0.04) + energy * 0.45;
        c.group.position.set(Math.cos(a) * curCompR, c.yOff + Math.sin(elapsed * 1.1 + i) * 0.22, Math.sin(a) * curCompR);
        c.microRing.rotation.z += dt * 0.75;
      }
    }

    // Velocity-reactive curved light trails
    if (trailGroup.visible) {
      for (const tr of orbitalTrails) {
        tr.line.rotation.y += dt * (0.14 + energy * 1.7) * tr.speed;
        const targetOp = Math.min(0.84, tr.baseOpacity + energy * 0.54);
        tr.mat.opacity += (targetOp - tr.mat.opacity) * 0.14;
      }
    }

    starField.rotation.y = elapsed * 0.01;

    renderer.render(scene, camera);

    const unsettled =
      curCam.distanceTo(targetCamVec) > 0.02 ||
      curTgt.distanceTo(targetTgtVec) > 0.02 ||
      Math.abs(scrollVel) > 0.004;

    // Idle power-saving: pause cleanly when settled on medium/low tiers or reduced-motion
    if (unsettled || (!reduceMotion && !(cfg.pauseOnIdle && isIdle))) {
      rafId = requestAnimationFrame(renderFrame);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = 0;
    } else {
      wake(true);
    }
  });

  window.__novaSceneReady = true;
  renderFrame(performance.now());
}

initWebGLWorld();
