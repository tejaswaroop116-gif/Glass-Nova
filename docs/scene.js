/* Glass Nova — True Cinematic 3D World, Dual-Zone Continuity & DOM-Synced Camera Flight
 * Inspired by Codrops Cinematic Scroll Animations (MIT):
 *  - Sleek dark-obsidian refractive glass Core + luminous rings & volumetric glow (no low-poly washout)
 *  - Physically continuous flight: Entry Core at z=0 opens into a Portal Tunnel leading to Vault Core at z=-22
 *  - Live DOM-anchored post-journey chapter keyframes (never drifts out of sync with #roadmap height)
 *  - Continuous RGB color blending, velocity-reactive light trails & accurate reduced-motion settling
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
    g1.addColorStop(0, 'rgba(56, 232, 255, 0.14)');
    g1.addColorStop(1, 'rgba(4, 5, 9, 0)');
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, W, H);

    const g2 = ctx.createRadialGradient(W * 0.74, H * 0.70, 10, W * 0.74, H * 0.70, nebR);
    g2.addColorStop(0, 'rgba(155, 123, 255, 0.14)');
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
  g.addColorStop(0.30, midRgba);
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
  renderer.toneMappingExposure = 1.1;

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
  scene.add(new THREE.AmbientLight(0x5278b8, 0.35));

  const keyLight = new THREE.PointLight(0x38e8ff, 34, 42);
  keyLight.position.set(-5.5, 4.0, 7.0);
  scene.add(keyLight);

  const fillLight = new THREE.PointLight(0x9b7bff, 28, 42);
  fillLight.position.set(6.0, -3.5, 4.0);
  scene.add(fillLight);

  const vaultKeyLight = new THREE.PointLight(0x38e8ff, 32, 38);
  vaultKeyLight.position.set(-4.5, 3.5, -16);
  scene.add(vaultKeyLight);

  const rimDir = new THREE.DirectionalLight(0x9be8ff, 0.72);
  rimDir.position.set(0, 8, -10);
  scene.add(rimDir);

  // Shared refined glow textures
  const cyanGlowTex = createGlowTexture(THREE, 'rgba(86, 232, 255, 0.65)', 'rgba(56, 232, 255, 0.16)');
  const orbGlowTex = createGlowTexture(THREE, 'rgba(220, 248, 255, 0.85)', 'rgba(110, 220, 255, 0.18)');

  // Helper: Build a sleek dark-obsidian refractive Glass Nova Core assembly
  function buildNovaCoreAssembly(withArticulatedShards) {
    const group = new THREE.Group();

    const glowMat = new THREE.SpriteMaterial({
      map: cyanGlowTex,
      color: 0x38e8ff,
      transparent: true,
      opacity: 0.46,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    });
    const glowSprite = new THREE.Sprite(glowMat);
    glowSprite.scale.set(6.0, 6.0, 1);
    group.add(glowSprite);

    // Smooth dark obsidian refractive singularity (never washes out white!)
    const coreMat = new THREE.MeshStandardMaterial({
      color: 0x06101f,
      emissive: 0x0d5c78,
      emissiveIntensity: 0.62,
      metalness: 0.92,
      roughness: 0.08,
      transparent: true,
      opacity: 0.92
    });
    const coreSphere = new THREE.Mesh(new THREE.SphereGeometry(0.62, 32, 24), coreMat);
    group.add(coreSphere);

    // Delicate inner neural energy wireframe
    const latticeMat = new THREE.LineBasicMaterial({
      color: 0x38e8ff,
      transparent: true,
      opacity: 0.36
    });
    const lattice = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.08, 1)),
      latticeMat
    );
    group.add(lattice);

    // 3 sleek gyroscopic resonance rings
    const ringDefs = [
      { r: 2.25, tube: 0.015, color: 0x38e8ff, op: 0.58, rot: [1.1, 0.25, 0.1] },
      { r: 2.85, tube: 0.012, color: 0x9b7bff, op: 0.46, rot: [0.65, -0.6, 0.9] },
      { r: 3.50, tube: 0.009, color: 0x4f8cff, op: 0.30, rot: [-0.85, 0.45, -0.3] }
    ];
    const rings = ringDefs.map((cfg) => {
      const mat = new THREE.MeshBasicMaterial({
        color: cfg.color,
        transparent: true,
        opacity: cfg.op
      });
      const mesh = new THREE.Mesh(new THREE.TorusGeometry(cfg.r, cfg.tube, 8, 96), mat);
      mesh.rotation.set(cfg.rot[0], cfg.rot[1], cfg.rot[2]);
      group.add(mesh);
      return { mesh, mat };
    });

    // Optional 12 articulated dark-glass shards (Entry Core blooms open into a fly-through portal)
    const shardEdgeMat = new THREE.LineBasicMaterial({
      color: 0x38e8ff,
      transparent: true,
      opacity: 0.46
    });
    let shards = [];
    if (withArticulatedShards) {
      const shardDirs = [
        [ 1,  0,  0], [-1,  0,  0], [ 0,  1,  0], [ 0, -1,  0],
        [ 0.7,  0.7,  0.2], [-0.7,  0.7,  0.2], [ 0.7, -0.7,  0.2], [-0.7, -0.7,  0.2],
        [ 0.65,  0.25, -0.7], [-0.65,  0.25, -0.7], [ 0.55, -0.5,  0.65], [-0.55, -0.5,  0.65]
      ];
      const sGeo = new THREE.OctahedronGeometry(0.56, 0);
      const sEdgeGeo = new THREE.EdgesGeometry(sGeo);
      const sMat = new THREE.MeshStandardMaterial({
        color: 0x071224,
        emissive: 0x0d2d4a,
        emissiveIntensity: 0.42,
        metalness: 0.9,
        roughness: 0.12,
        transparent: true,
        opacity: 0.52,
        flatShading: true
      });
      shards = shardDirs.map((d, idx) => {
        const dir = new THREE.Vector3(d[0], d[1], d[2]).normalize();
        const holder = new THREE.Group();
        const mesh = new THREE.Mesh(sGeo, sMat);
        mesh.scale.set(1.12, 0.74, 0.36);
        const edge = new THREE.LineSegments(sEdgeGeo, shardEdgeMat);
        edge.scale.copy(mesh.scale).multiplyScalar(1.02);
        holder.add(mesh, edge);
        holder.position.copy(dir).multiplyScalar(1.48);
        holder.lookAt(dir.clone().multiplyScalar(4));
        group.add(holder);
        return { holder, dir, baseDist: 1.48, phase: idx * 0.52 };
      });
    }

    return { group, glowMat, coreMat, coreSphere, lattice, latticeMat, rings, shards, shardEdgeMat };
  }

  // =====================================================
  // 1A. ENTRY NOVA CORE (fixed at z = 0; opens into portal for Act II)
  // =====================================================
  const entryCore = buildNovaCoreAssembly(true);
  scene.add(entryCore.group);

  // =====================================================
  // 1B. VAULT / DEEP-SPACE NOVA CORE (fixed at z = -22; zero teleport!)
  // =====================================================
  const vaultCore = buildNovaCoreAssembly(true);
  vaultCore.group.position.set(0, 0, -22);
  scene.add(vaultCore.group);

  // =====================================================
  // 2. DEEP-SPACE GEOMETRIC PORTAL TUNNEL (z = -3.2 to z = -19.5)
  // =====================================================
  const tunnelGroup = new THREE.Group();
  scene.add(tunnelGroup);

  const gateCount = isMobileNow() ? 9 : 13;
  const tunnelGates = [];
  for (let i = 0; i < gateCount; i++) {
    const zPos = -3.0 - i * 1.38;
    const isHex = i % 2 === 0;
    const radius = 3.0 + Math.sin(i * 0.65) * 0.4;
    const geo = new THREE.TorusGeometry(radius, 0.015, 6, isHex ? 6 : 48);
    const mat = new THREE.MeshBasicMaterial({
      color: i % 3 === 0 ? 0x9b7bff : 0x38e8ff,
      transparent: true,
      opacity: 0.0
    });
    const gate = new THREE.Mesh(geo, mat);
    gate.position.set(Math.sin(i * 0.42) * 0.45, Math.cos(i * 0.38) * 0.32, zPos);
    gate.rotation.z = i * 0.24;
    tunnelGroup.add(gate);
    tunnelGates.push({ mesh: gate, mat, idx: i });
  }

  // =====================================================
  // 3. CRYSTALLINE SECURITY VAULT (at z = -22 around Vault Core)
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
  const vaultOuter = new THREE.Mesh(new THREE.DodecahedronGeometry(3.45, 0), vaultMat);
  vaultGroup.add(vaultOuter);

  const vaultRingMat = new THREE.MeshBasicMaterial({
    color: 0x38e8ff,
    transparent: true,
    opacity: 0.0
  });
  const vaultRing1 = new THREE.Mesh(new THREE.TorusGeometry(4.15, 0.018, 6, 6), vaultRingMat);
  const vaultRing2 = new THREE.Mesh(new THREE.TorusGeometry(4.65, 0.014, 6, 6), vaultRingMat);
  vaultRing2.rotation.x = Math.PI * 0.5;
  vaultGroup.add(vaultRing1, vaultRing2);

  // =====================================================
  // 4. FOUR REFINED COMPANION ENERGY SATELLITES (orbiting z = -22)
  // =====================================================
  const companionGroup = new THREE.Group();
  companionGroup.position.set(0, 0, -22);
  scene.add(companionGroup);

  const compColors = [0x38e8ff, 0x9b7bff, 0x34d399, 0x4f8cff];
  const companionOrbs = compColors.map((hex, i) => {
    const g = new THREE.Group();
    const orb = new THREE.Mesh(
      new THREE.SphereGeometry(0.11, 20, 16),
      new THREE.MeshStandardMaterial({
        color: 0x0b1628,
        emissive: hex,
        emissiveIntensity: 0.95,
        roughness: 0.15,
        metalness: 0.8
      })
    );
    const microRing = new THREE.Mesh(
      new THREE.TorusGeometry(0.24, 0.008, 6, 36),
      new THREE.MeshBasicMaterial({ color: hex, transparent: true, opacity: 0.68 })
    );
    microRing.rotation.set(1.1, i * 0.6, 0.2);
    const halo = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: orbGlowTex,
        color: hex,
        transparent: true,
        opacity: 0.58,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      })
    );
    halo.scale.set(1.15, 1.15, 1);
    g.add(orb, microRing, halo);
    companionGroup.add(g);
    return { group: g, microRing, angle: (i * Math.PI) / 2, yOff: (i % 2 === 0 ? 1 : -1) * 0.72 };
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
    const radius = 2.6 + (i % 6) * 0.62 + Math.random() * 0.4;
    const baseY = (i % 2 === 0 ? 1 : -1) * (0.4 + Math.random() * 2.6);
    const zOffset = -Math.random() * 26;
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

    const color = i % 3 === 0 ? 0x9b7bff : (i % 2 === 0 ? 0x38e8ff : 0xbceeff);
    const mat = new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.14
    });
    const line = new THREE.Line(geo, mat);
    line.position.z = zOffset;
    line.rotation.x = (Math.random() - 0.5) * 0.35;
    trailGroup.add(line);

    orbitalTrails.push({
      line,
      mat,
      speed: (0.5 + Math.random() * 1.1) * (i % 2 === 0 ? 1 : -1),
      baseOpacity: 0.10 + Math.random() * 0.12
    });
  }

  // =====================================================
  // 6. DEEP-SPACE LAYERED STARFIELD
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
    size: isMobileNow() ? 0.075 : 0.062,
    vertexColors: true,
    transparent: true,
    opacity: 0.76,
    depthWrite: false
  });
  const starField = new THREE.Points(starGeo, starMat);
  scene.add(starField);

  // =====================================================
  // LIVE DOM-ANCHORED CHAPTER CHOREOGRAPHY
  // =====================================================
  const journeyEl = document.getElementById('nova-journey');

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

  const postNodes = POST_CHAPTER_DEFS.map((d) => ({ ...d, el: document.getElementById(d.id) })).filter((d) => d.el);

  function clamp01(v) { return Math.max(0, Math.min(1, v)); }
  function smoothstep(v) { v = clamp01(v); return v * v * (3 - 2 * v); }
  function lerp(a, b, t) { return a + (b - a) * t; }

  const tmpColA = new THREE.Color();
  const tmpColB = new THREE.Color();
  const desiredAcc = new THREE.Color(0x38e8ff);
  const desiredFill = new THREE.Color(0x9b7bff);

  function setBlendedColors(hexA, hexB, fillA, fillB, t) {
    tmpColA.setHex(hexA);
    tmpColB.setHex(hexB);
    desiredAcc.copy(tmpColA).lerp(tmpColB, t);

    tmpColA.setHex(fillA);
    tmpColB.setHex(fillB);
    desiredFill.copy(tmpColA).lerp(tmpColB, t);
  }

  function getSceneState() {
    const sy = window.scrollY || 0;
    const vh = Math.max(1, window.innerHeight);

    let inJourney = false;
    let journeyP = 0;
    let beforeJourney = true;

    if (journeyEl) {
      const r = journeyEl.getBoundingClientRect();
      const range = Math.max(1, journeyEl.offsetHeight - vh);
      if (r.top > 0) {
        beforeJourney = true;
        journeyP = 0;
      } else if (r.bottom < vh) {
        beforeJourney = false;
        journeyP = 1;
      } else {
        beforeJourney = false;
        inJourney = true;
        journeyP = clamp01(-r.top / range);
      }
    }

    const state = {
      cam: [isMobileNow() ? 0.0 : 1.35, 0.35, isMobileNow() ? 10.6 : 9.2],
      tgt: [isMobileNow() ? 0.0 : -0.4, 0.0, 0.0],
      fov: isMobileNow() ? 56 : 46,
      roll: 0.0,
      entryX: isMobileNow() ? 0.0 : 1.55,
      entryScale: 1.0,
      entryShardOpen: 0.0,
      vaultScale: 0.85,
      vaultShardOpen: 0.08,
      ringSpread: 0.0,
      tunnelVis: 0.0,
      vaultVis: 0.0,
      compRadius: 4.4,
      inJourney,
      journeyP
    };

    if (beforeJourney && journeyEl) {
      const r = journeyEl.getBoundingClientRect();
      const approach = smoothstep(clamp01(1 - r.top / vh));
      state.cam = [
        lerp(isMobileNow() ? 0.0 : 1.35, 0.0, approach),
        lerp(0.35, 0.45, approach),
        lerp(isMobileNow() ? 10.6 : 9.2, 7.6, approach)
      ];
      state.tgt = [lerp(isMobileNow() ? 0.0 : -0.4, 0.0, approach), 0, 0];
      state.entryX = lerp(isMobileNow() ? 0.0 : 1.55, 0.0, approach);
      state.entryShardOpen = approach * 0.18;
      state.tunnelVis = approach * 0.22;
      setBlendedColors(0x38e8ff, 0x38e8ff, 0x9b7bff, 0x9b7bff, 0);
      return state;
    }

    if (inJourney) {
      state.entryX = 0.0;
      if (journeyP < 0.25) {
        // ACT I: APPROACH — Orbit Entry Core at z = 0 as its 12 shards begin unlocking
        const t = smoothstep(journeyP / 0.25);
        state.cam = [lerp(0.0, -2.5, t), lerp(0.45, 1.25, t), lerp(7.6, 4.6, t)];
        state.tgt = [0, 0, lerp(0, -2.0, t)];
        state.fov = lerp(46, 51, t);
        state.roll = lerp(0, -0.05, t);
        state.entryScale = lerp(1.0, 1.2, t);
        state.entryShardOpen = lerp(0.18, 0.85, t);
        state.tunnelVis = lerp(0.22, 0.72, t);
        setBlendedColors(0x38e8ff, 0x4f8cff, 0x9b7bff, 0x38e8ff, t * 0.4);
      } else if (journeyP < 0.55) {
        // ACT II: CORE DIVE — Fly STRAIGHT THROUGH Entry Core (z=0) & Portal Tunnel toward Vault Core (z=-22)
        const t = smoothstep((journeyP - 0.25) / 0.30);
        state.cam = [
          lerp(-2.5, 0.3, t) + Math.sin(t * Math.PI) * 0.85,
          lerp(1.25, -0.2, t),
          lerp(4.6, -15.4, t)
        ];
        state.tgt = [Math.sin(t * Math.PI) * 0.35, 0, lerp(-2.0, -22.0, t)];
        state.fov = lerp(51, 63, Math.sin(t * Math.PI * 0.85));
        state.roll = Math.sin(t * Math.PI) * 0.1;
        state.entryScale = lerp(1.2, 1.5, t);
        state.entryShardOpen = lerp(0.85, 1.45, t);
        state.vaultScale = lerp(0.65, 0.92, t);
        state.vaultShardOpen = lerp(0.45, 0.12, t);
        state.ringSpread = lerp(0.0, 0.75, t);
        state.tunnelVis = 0.90;
        state.vaultVis = smoothstep((t - 0.4) / 0.6) * 0.65;
        setBlendedColors(0x4f8cff, 0x34d399, 0x38e8ff, 0x38e8ff, t * 0.5);
      } else if (journeyP < 0.80) {
        // ACT III: THE VAULT — Smooth orbit around Vault Core at z = -22 (zero teleport!)
        const t = smoothstep((journeyP - 0.55) / 0.25);
        const angle = lerp(-0.35, 1.15, t);
        state.cam = [
          Math.sin(angle) * 7.2,
          lerp(-0.2, 2.0, t),
          -22 + Math.cos(angle) * 7.2
        ];
        state.tgt = [0, 0, -22];
        state.fov = lerp(53, 47, t);
        state.roll = lerp(0.04, -0.035, t);
        state.vaultScale = 0.92;
        state.vaultShardOpen = lerp(0.12, 0.08, t);
        state.tunnelVis = lerp(0.82, 0.22, t);
        state.vaultVis = 0.86;
        state.compRadius = lerp(5.2, 4.3, t);
        setBlendedColors(0x34d399, 0x38e8ff, 0x38e8ff, 0x9b7bff, t * 0.5);
      } else {
        // ACT IV: HORIZON — Pull back to reveal Vault Core + 4 refined Companion Satellites
        const t = smoothstep((journeyP - 0.80) / 0.20);
        state.cam = [lerp(6.5, 0.0, t), lerp(2.0, 0.8, t), lerp(-19.0, -13.0, t)];
        state.tgt = [0, 0, -22];
        state.fov = lerp(47, 48, t);
        state.roll = lerp(-0.035, 0, t);
        state.vaultScale = lerp(0.92, 1.04, t);
        state.vaultShardOpen = lerp(0.08, 0.24, t);
        state.tunnelVis = lerp(0.22, 0.06, t);
        state.vaultVis = lerp(0.86, 0.14, t);
        state.compRadius = lerp(4.3, 4.1, t);
        setBlendedColors(0x38e8ff, 0x9b7bff, 0x9b7bff, 0x38e8ff, t * 0.45);
      }
      return state;
    }

    // =================================================
    // LIVE DOM-ANCHORED POST-JOURNEY CHAPTERS
    // Exact scroll sync with #intro -> #finale regardless of #roadmap height!
    // =================================================
    const probeY = sy + vh * 0.35;
    let a = postNodes[0];
    let b = postNodes[postNodes.length - 1];
    let t = 0;

    if (postNodes.length >= 2) {
      if (probeY <= postNodes[0].el.offsetTop) {
        a = b = postNodes[0];
        t = 0;
      } else if (probeY >= postNodes[postNodes.length - 1].el.offsetTop) {
        a = b = postNodes[postNodes.length - 1];
        t = 1;
      } else {
        for (let i = 0; i < postNodes.length - 1; i++) {
          const y0 = postNodes[i].el.offsetTop;
          const y1 = postNodes[i + 1].el.offsetTop;
          if (probeY >= y0 && probeY <= y1) {
            a = postNodes[i];
            b = postNodes[i + 1];
            t = smoothstep((probeY - y0) / Math.max(1, y1 - y0));
            break;
          }
        }
      }
    }

    state.cam = [lerp(a.cam[0], b.cam[0], t), lerp(a.cam[1], b.cam[1], t), lerp(a.cam[2], b.cam[2], t)];
    state.tgt = [lerp(a.tgt[0], b.tgt[0], t), lerp(a.tgt[1], b.tgt[1], t), lerp(a.tgt[2], b.tgt[2], t)];
    state.fov = lerp(a.fov, b.fov, t);
    state.roll = lerp(a.roll, b.roll, t);
    state.vaultScale = lerp(a.scale, b.scale, t);
    state.vaultShardOpen = lerp(a.shard, b.shard, t);
    state.ringSpread = lerp(a.spread, b.spread, t);
    state.tunnelVis = 0.04;
    state.vaultVis = lerp(a.vault, b.vault, t);
    state.compRadius = lerp(a.compR, b.compR, t);
    setBlendedColors(a.acc, b.acc, a.fill, b.fill, t);
    return state;
  }

  // Smooth runtime interpolation state
  const curCam = new THREE.Vector3(1.2, 0.4, 9.6);
  const curTgt = new THREE.Vector3(0, 0, 0);
  const targetCamVec = new THREE.Vector3();
  const targetTgtVec = new THREE.Vector3();
  const curAccent = new THREE.Color(0x38e8ff);
  const curFill = new THREE.Color(0x9b7bff);

  let curFov = isMobileNow() ? 56 : 46;
  let curRoll = 0;
  let curEntryX = isMobileNow() ? 0 : 1.55;
  let curEntryShard = 0;
  let curVaultShard = 0.08;
  let curRingSpread = 0;
  let curTunnelVis = 0;
  let curVaultVis = 0;
  let curCompR = 4.4;

  let lastScrollY = window.scrollY || 0;
  let scrollVel = 0;
  let momentum = 0;
  const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
  let rafId = 0;

  function wakeIfNeeded() {
    if (!document.hidden && !window.__novaRoadmapActive && !rafId) {
      lastTime = performance.now();
      rafId = requestAnimationFrame(renderFrame);
    }
  }

  window.addEventListener('scroll', () => {
    const curY = window.scrollY || 0;
    const dy = curY - lastScrollY;
    lastScrollY = curY;
    scrollVel = Math.max(-1.4, Math.min(1.4, scrollVel + dy * 0.0026));
    wakeIfNeeded();
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
    wakeIfNeeded();
  }, { passive: true });

  function updateCoreAssembly(core, shardOpen, ringSpread, scaleTarget, dt, elapsed, spinBoost, damp) {
    const nextS = core.group.scale.x + (scaleTarget - core.group.scale.x) * damp;
    core.group.scale.setScalar(nextS);
    core.group.position.y = Math.sin(elapsed * 0.72) * 0.12;

    core.coreSphere.rotation.y += dt * 0.3 * spinBoost;
    core.lattice.rotation.y -= dt * 0.18 * spinBoost;
    core.lattice.rotation.z += dt * 0.10 * spinBoost;

    for (const sh of core.shards) {
      const dist = sh.baseDist + shardOpen * 2.45 + Math.sin(elapsed * 1.1 + sh.phase) * 0.05;
      sh.holder.position.copy(sh.dir).multiplyScalar(dist);
      sh.holder.lookAt(sh.dir.clone().multiplyScalar(dist + 3));
      sh.holder.rotation.z += shardOpen * 0.65 + elapsed * 0.12;
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
    if (window.__novaRoadmapActive) {
      if (!reduceMotion && !document.hidden) rafId = requestAnimationFrame(renderFrame);
      return;
    }

    const dt = Math.min(0.05, Math.max(0.001, (now - lastTime) / 1000));
    lastTime = now;
    if (!reduceMotion) elapsed += dt;

    scrollVel *= 0.89;
    momentum = momentum * 0.91 + Math.abs(scrollVel) * 0.22;
    const energy = Math.min(1.8, momentum);

    mouse.x += (mouse.tx - mouse.x) * 0.055;
    mouse.y += (mouse.ty - mouse.y) * 0.055;

    const st = getSceneState();
    const damp = reduceMotion ? 0.18 : 0.088;

    window.__nova3D = {
      inJourney: st.inJourney,
      journeyP: st.journeyP,
      energy
    };

    const px = isMobileNow() ? 0 : mouse.x * 0.42;
    const py = isMobileNow() ? 0 : -mouse.y * 0.28;

    targetCamVec.set(st.cam[0] + px, st.cam[1] + py, st.cam[2]);
    targetTgtVec.set(st.tgt[0], st.tgt[1], st.tgt[2]);

    curCam.lerp(targetCamVec, damp);
    curTgt.lerp(targetTgtVec, damp);
    curFov += ((isMobileNow() ? st.fov + 7 : st.fov) - curFov) * damp;
    curRoll += (st.roll - curRoll) * damp;

    camera.position.copy(curCam);
    camera.fov = curFov;
    camera.updateProjectionMatrix();
    camera.lookAt(curTgt);
    camera.rotation.z += curRoll;

    curEntryX += (st.entryX - curEntryX) * damp;
    entryCore.group.position.x = curEntryX;
    curEntryShard += (st.entryShardOpen - curEntryShard) * damp;
    curVaultShard += (st.vaultShardOpen - curVaultShard) * damp;
    curRingSpread += (st.ringSpread - curRingSpread) * damp;
    curTunnelVis += (st.tunnelVis - curTunnelVis) * damp;
    curVaultVis += (st.vaultVis - curVaultVis) * damp;
    curCompR += (st.compRadius - curCompR) * damp;

    // Continuous RGB color blending
    curAccent.lerp(desiredAcc, 0.065);
    curFill.lerp(desiredFill, 0.065);
    keyLight.color.copy(curAccent);
    fillLight.color.copy(curFill);
    vaultKeyLight.color.copy(curAccent);

    const spinBoost = 1 + energy * 3.2;
    updateCoreAssembly(entryCore, curEntryShard, curRingSpread * 0.5, st.entryScale, dt, elapsed, spinBoost, damp);
    updateCoreAssembly(vaultCore, curVaultShard, curRingSpread, st.vaultScale, dt, elapsed, spinBoost, damp);

    // Portal tunnel gates
    for (const gate of tunnelGates) {
      gate.mat.opacity = curTunnelVis * (0.20 + 0.32 * (0.5 + 0.5 * Math.sin(elapsed * 1.3 - gate.idx * 0.45)));
      gate.mesh.rotation.z += dt * (0.07 + energy * 0.35) * (gate.idx % 2 === 0 ? 1 : -1);
    }

    // Crystalline Security Vault
    vaultMat.opacity = curVaultVis * 0.28;
    vaultRingMat.opacity = curVaultVis * 0.52;
    vaultOuter.rotation.y += dt * 0.16 * spinBoost;
    vaultRing1.rotation.z -= dt * 0.22 * spinBoost;
    vaultRing2.rotation.y += dt * 0.18 * spinBoost;

    // 4 Refined Companion Energy Satellites
    for (let i = 0; i < companionOrbs.length; i++) {
      const c = companionOrbs[i];
      const a = c.angle + elapsed * (0.30 + i * 0.04) + energy * 0.5;
      c.group.position.set(
        Math.cos(a) * curCompR,
        c.yOff + Math.sin(elapsed * 1.1 + i) * 0.24,
        Math.sin(a) * curCompR
      );
      c.microRing.rotation.z += dt * 0.8;
    }

    // Velocity-reactive curved light trails
    for (const tr of orbitalTrails) {
      tr.line.rotation.y += dt * (0.14 + energy * 1.75) * tr.speed;
      const targetOp = Math.min(0.86, tr.baseOpacity + energy * 0.56);
      tr.mat.opacity += (targetOp - tr.mat.opacity) * 0.14;
    }

    starField.rotation.y = elapsed * 0.011;

    renderer.render(scene, camera);

    // Keep rendering until camera settles even under prefers-reduced-motion, then pause cleanly
    const unsettled =
      curCam.distanceTo(targetCamVec) > 0.02 ||
      curTgt.distanceTo(targetTgtVec) > 0.02 ||
      Math.abs(scrollVel) > 0.004;

    if (!document.hidden && (!reduceMotion || unsettled)) {
      rafId = requestAnimationFrame(renderFrame);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = 0;
    } else {
      wakeIfNeeded();
    }
  });

  window.__novaSceneReady = true;
  renderFrame(performance.now());
}

initWebGLWorld();
