/* Glass Nova — Cinematic 3D Deep-Space Core, Scroll-Keyframed Camera & Velocity-Reactive Orbital Trails
 * Adapts scroll-keyframed camera choreography & velocity-reactive orbital trails (inspired by Codrops Cinematic Scroll Animations, MIT)
 * with automatic 2D Canvas fallback and strict GPU / mobile / reduced-motion optimizations.
 */

const canvas = document.getElementById('nova-webgl');
const reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

function initFallback2D() {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  let W = 0, H = 0, DPR = 1;
  const isMobile = window.innerWidth < 700;
  const count = isMobile ? 65 : 140;
  const stars = Array.from({ length: count }, (_, i) => ({
    x: Math.random(),
    y: Math.random(),
    r: 0.7 + Math.random() * 1.5,
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
    const nebR = Math.max(W, H) * 0.55;
    const g1 = ctx.createRadialGradient(W * 0.25, H * 0.28, 10, W * 0.25, H * 0.28, nebR);
    g1.addColorStop(0, 'rgba(56, 232, 255, 0.13)');
    g1.addColorStop(1, 'rgba(4, 5, 9, 0)');
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, W, H);

    const g2 = ctx.createRadialGradient(W * 0.76, H * 0.72, 10, W * 0.76, H * 0.72, nebR);
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
  resize();
  draw();
}

async function initWebGLScene() {
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
  renderer.toneMappingExposure = 1.12;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x040509, 0.042);

  const camera = new THREE.PerspectiveCamera(
    isMobileNow() ? 54 : 46,
    window.innerWidth / Math.max(1, window.innerHeight),
    0.1,
    120
  );
  camera.position.set(0.4, 0.3, 8.6);

  // Subtle cyan & violet cinematic lighting
  scene.add(new THREE.AmbientLight(0x88b4ff, 0.45));

  const cyanLight = new THREE.PointLight(0x38e8ff, 34, 32);
  cyanLight.position.set(-5.5, 3.8, 5.0);
  scene.add(cyanLight);

  const violetLight = new THREE.PointLight(0x9b7bff, 30, 32);
  violetLight.position.set(5.5, -3.2, 4.5);
  scene.add(violetLight);

  const rimLight = new THREE.DirectionalLight(0xbbeeff, 0.9);
  rimLight.position.set(0, 6, -6);
  scene.add(rimLight);

  // =====================================================
  // PROCEDURAL GLASS NOVA 3D CORE (Zero external .glb)
  // =====================================================
  const coreGroup = new THREE.Group();
  scene.add(coreGroup);

  // Outer refractive glass shell
  const outerGeo = new THREE.IcosahedronGeometry(1.55, 1);
  const outerMat = new THREE.MeshStandardMaterial({
    color: 0x0b1628,
    emissive: 0x103552,
    emissiveIntensity: 0.45,
    metalness: 0.85,
    roughness: 0.16,
    transparent: true,
    opacity: 0.42,
    flatShading: true
  });
  const outerShell = new THREE.Mesh(outerGeo, outerMat);
  coreGroup.add(outerShell);

  // Crisp faceted wireframe lattice
  const wireGeo = new THREE.EdgesGeometry(outerGeo);
  const wireMat = new THREE.LineBasicMaterial({
    color: 0x38e8ff,
    transparent: true,
    opacity: 0.36
  });
  const wireShell = new THREE.LineSegments(wireGeo, wireMat);
  wireShell.scale.setScalar(1.015);
  coreGroup.add(wireShell);

  // Inner luminous prism core
  const innerGeo = new THREE.OctahedronGeometry(0.68, 0);
  const innerMat = new THREE.MeshStandardMaterial({
    color: 0xaef4ff,
    emissive: 0x38e8ff,
    emissiveIntensity: 0.85,
    metalness: 0.3,
    roughness: 0.2,
    transparent: true,
    opacity: 0.78
  });
  const innerCore = new THREE.Mesh(innerGeo, innerMat);
  coreGroup.add(innerCore);

  // Gyroscopic cyan & violet orbital rings
  const ringConfigs = [
    { r: 2.25, tube: 0.012, color: 0x38e8ff, opacity: 0.45, rot: [1.15, 0.28, 0.1] },
    { r: 2.75, tube: 0.01, color: 0x9b7bff, opacity: 0.38, rot: [0.65, -0.55, 0.85] },
    { r: 3.35, tube: 0.008, color: 0x4f8cff, opacity: 0.24, rot: [-0.9, 0.45, -0.35] }
  ];
  const gyroRings = ringConfigs.map((cfg) => {
    const mesh = new THREE.Mesh(
      new THREE.TorusGeometry(cfg.r, cfg.tube, 8, 96),
      new THREE.MeshBasicMaterial({
        color: cfg.color,
        transparent: true,
        opacity: cfg.opacity
      })
    );
    mesh.rotation.set(cfg.rot[0], cfg.rot[1], cfg.rot[2]);
    coreGroup.add(mesh);
    return mesh;
  });

  // =====================================================
  // VELOCITY-REACTIVE ORBITAL PARTICLE TRAILS (Codrops Demo 1 technique)
  // =====================================================
  const trailGroup = new THREE.Group();
  scene.add(trailGroup);

  const trailCount = isMobileNow() ? 10 : 18;
  const trailSegments = 18;
  const orbitalTrails = [];

  for (let i = 0; i < trailCount; i++) {
    const radius = 2.45 + (i % 5) * 0.55 + Math.random() * 0.35;
    const baseY = (i % 2 === 0 ? 1 : -1) * (0.45 + Math.random() * 2.15);
    const angleSpan = 0.28 + Math.random() * 0.32;
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

    const color = i % 3 === 0 ? 0x9b7bff : (i % 2 === 0 ? 0x38e8ff : 0xb8f3ff);
    const mat = new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.16
    });
    const line = new THREE.Line(geo, mat);
    line.rotation.x = (Math.random() - 0.5) * 0.5;
    line.rotation.z = (Math.random() - 0.5) * 0.5;
    trailGroup.add(line);

    orbitalTrails.push({
      line,
      mat,
      speed: (0.45 + Math.random() * 0.9) * (i % 2 === 0 ? 1 : -1),
      baseOpacity: 0.12 + Math.random() * 0.12
    });
  }

  // =====================================================
  // FINE DRIFTING DEEP-SPACE PARTICLE FIELD
  // =====================================================
  const starCount = isMobileNow() ? 520 : 1250;
  const starPositions = new Float32Array(starCount * 3);
  const starColors = new Float32Array(starCount * 3);
  const cCyan = new THREE.Color(0x5eeeff);
  const cViolet = new THREE.Color(0xb195ff);
  const cIce = new THREE.Color(0xdcecff);

  for (let i = 0; i < starCount; i++) {
    starPositions[i * 3] = (Math.random() - 0.5) * 46;
    starPositions[i * 3 + 1] = (Math.random() - 0.5) * 34;
    starPositions[i * 3 + 2] = (Math.random() - 0.5) * 44 - 4;
    const col = i % 5 === 0 ? cViolet : (i % 3 === 0 ? cCyan : cIce);
    starColors[i * 3] = col.r;
    starColors[i * 3 + 1] = col.g;
    starColors[i * 3 + 2] = col.b;
  }

  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
  starGeo.setAttribute('color', new THREE.BufferAttribute(starColors, 3));

  const starMat = new THREE.PointsMaterial({
    size: isMobileNow() ? 0.065 : 0.055,
    vertexColors: true,
    transparent: true,
    opacity: 0.72,
    depthWrite: false
  });
  const starField = new THREE.Points(starGeo, starMat);
  scene.add(starField);

  // =====================================================
  // SCROLL-KEYFRAMED CAMERA WAYPOINTS (Codrops Demo 2 technique)
  // =====================================================
  const WAYPOINTS = [
    { p: 0.00, cam: [0.5, 0.25, 8.6],  tgt: [-0.2, 0.0, 0.0],  coreScale: 1.00, coreX: 1.35,  accent: 0x38e8ff },
    { p: 0.12, cam: [-2.7, 1.3, 6.2],  tgt: [0.15, 0.1, 0.0],  coreScale: 1.08, coreX: -0.25, accent: 0x62d9ff },
    { p: 0.25, cam: [2.9, -0.7, 6.9],  tgt: [0.0, 0.0, 0.0],   coreScale: 0.94, coreX: 0.55,  accent: 0x38e8ff },
    { p: 0.40, cam: [1.6, 2.5, 6.1],   tgt: [-0.25, -0.1, 0.0],coreScale: 0.98, coreX: -0.45, accent: 0x4f8cff },
    { p: 0.56, cam: [0.0, 0.6, 10.2],  tgt: [0.0, 0.0, 0.0],   coreScale: 0.80, coreX: 0.0,   accent: 0x38e8ff },
    { p: 0.72, cam: [-2.8, -0.9, 6.5], tgt: [0.2, 0.15, 0.0],  coreScale: 0.96, coreX: 0.35,  accent: 0x9b7bff },
    { p: 0.84, cam: [2.3, 0.85, 7.1],  tgt: [0.0, 0.0, 0.0],   coreScale: 0.92, coreX: -0.35, accent: 0x34d399 },
    { p: 0.93, cam: [-1.1, 0.35, 8.4], tgt: [0.0, 0.0, 0.0],   coreScale: 0.95, coreX: 0.15,  accent: 0x4f8cff },
    { p: 1.00, cam: [0.0, 0.15, 5.8],  tgt: [0.0, 0.0, 0.0],   coreScale: 1.15, coreX: 0.0,   accent: 0x38e8ff }
  ];

  const tmpCam = new THREE.Vector3(0.5, 0.25, 8.6);
  const tmpTgt = new THREE.Vector3(-0.2, 0.0, 0.0);
  const desiredCam = new THREE.Vector3(0.5, 0.25, 8.6);
  const desiredTgt = new THREE.Vector3(-0.2, 0.0, 0.0);
  const accentColor = new THREE.Color(0x38e8ff);
  const targetAccent = new THREE.Color(0x38e8ff);

  let scrollProgress = 0;
  let lastScrollY = window.scrollY || 0;
  let scrollVelocity = 0;
  let momentum = 0;
  const mouse = { x: 0, y: 0, tx: 0, ty: 0 };

  function updateScrollState() {
    const curY = window.scrollY || 0;
    const dy = curY - lastScrollY;
    lastScrollY = curY;
    scrollVelocity = Math.max(-1.2, Math.min(1.2, scrollVelocity + dy * 0.0022));
    const maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    scrollProgress = Math.max(0, Math.min(1, curY / maxScroll));
  }

  window.addEventListener('scroll', updateScrollState, { passive: true });
  window.addEventListener('pointermove', (e) => {
    if (isMobileNow()) return;
    mouse.tx = (e.clientX / Math.max(1, window.innerWidth) - 0.5) * 2;
    mouse.ty = (e.clientY / Math.max(1, window.innerHeight) - 0.5) * 2;
  }, { passive: true });

  // Mobile address bar resize guard (Codrops Demo 1 technique)
  window.addEventListener('resize', () => {
    const w = window.innerWidth;
    const h = Math.max(1, window.innerHeight);
    if (isMobileNow() && w === lastWidth) return;
    lastWidth = w;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, isMobileNow() ? 1.25 : 1.5));
    renderer.setSize(w, h, false);
    camera.fov = isMobileNow() ? 54 : 46;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }, { passive: true });

  function sampleWaypoints(p) {
    let a = WAYPOINTS[0];
    let b = WAYPOINTS[WAYPOINTS.length - 1];
    for (let i = 0; i < WAYPOINTS.length - 1; i++) {
      if (p >= WAYPOINTS[i].p && p <= WAYPOINTS[i + 1].p) {
        a = WAYPOINTS[i];
        b = WAYPOINTS[i + 1];
        break;
      }
    }
    const span = Math.max(0.0001, b.p - a.p);
    let t = Math.max(0, Math.min(1, (p - a.p) / span));
    // Smoothstep cinematic easing
    t = t * t * (3 - 2 * t);

    desiredCam.set(
      a.cam[0] + (b.cam[0] - a.cam[0]) * t,
      a.cam[1] + (b.cam[1] - a.cam[1]) * t,
      a.cam[2] + (b.cam[2] - a.cam[2]) * t
    );
    desiredTgt.set(
      a.tgt[0] + (b.tgt[0] - a.tgt[0]) * t,
      a.tgt[1] + (b.tgt[1] - a.tgt[1]) * t,
      a.tgt[2] + (b.tgt[2] - a.tgt[2]) * t
    );
    const scale = a.coreScale + (b.coreScale - a.coreScale) * t;
    const coreX = (isMobileNow() ? 0 : (a.coreX + (b.coreX - a.coreX) * t));
    targetAccent.setHex(a.accent).lerp(new THREE.Color(b.accent), t);
    return { scale, coreX };
  }

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

    // Velocity & momentum decay (Codrops Demo 1 physics)
    scrollVelocity *= 0.90;
    momentum = momentum * 0.92 + Math.abs(scrollVelocity) * 0.18;
    const energy = Math.min(1.4, momentum);

    mouse.x += (mouse.tx - mouse.x) * 0.055;
    mouse.y += (mouse.ty - mouse.y) * 0.055;

    const { scale, coreX } = sampleWaypoints(scrollProgress);

    // Apply subtle mouse parallax offset on top of scroll camera path
    const parallaxX = isMobileNow() ? 0 : mouse.x * 0.42;
    const parallaxY = isMobileNow() ? 0 : -mouse.y * 0.28;

    tmpCam.lerp(
      new THREE.Vector3(desiredCam.x + parallaxX, desiredCam.y + parallaxY, desiredCam.z),
      reduceMotion ? 1 : 0.075
    );
    tmpTgt.lerp(desiredTgt, reduceMotion ? 1 : 0.08);

    camera.position.copy(tmpCam);
    camera.lookAt(tmpTgt);

    // Core position, scale & gentle rotation
    coreGroup.position.x += (coreX - coreGroup.position.x) * (reduceMotion ? 1 : 0.06);
    coreGroup.position.y = Math.sin(elapsed * 0.65) * 0.14;
    const s = coreGroup.scale.x + (scale - coreGroup.scale.x) * (reduceMotion ? 1 : 0.06);
    coreGroup.scale.setScalar(s);

    const spinBoost = 1 + energy * 3.2;
    outerShell.rotation.y += dt * 0.16 * spinBoost;
    outerShell.rotation.x += dt * 0.07 * spinBoost;
    wireShell.rotation.copy(outerShell.rotation);
    innerCore.rotation.y -= dt * 0.34 * spinBoost;
    innerCore.rotation.z += dt * 0.18 * spinBoost;

    gyroRings[0].rotation.z += dt * 0.22 * spinBoost;
    gyroRings[1].rotation.x -= dt * 0.18 * spinBoost;
    gyroRings[2].rotation.y += dt * 0.14 * spinBoost;

    // Dynamic accent lighting shift per section
    accentColor.lerp(targetAccent, 0.05);
    cyanLight.color.copy(accentColor);
    wireMat.color.copy(accentColor);
    innerMat.emissive.copy(accentColor);
    innerMat.emissiveIntensity = 0.72 + Math.sin(elapsed * 1.9) * 0.18 + energy * 0.45;

    // Velocity-reactive orbital particle trails
    trailGroup.position.x = coreGroup.position.x;
    trailGroup.position.y = coreGroup.position.y;
    for (const tr of orbitalTrails) {
      tr.line.rotation.y += dt * (0.14 + energy * 1.65) * tr.speed;
      const targetOp = Math.min(0.85, tr.baseOpacity + energy * 0.58);
      tr.mat.opacity += (targetOp - tr.mat.opacity) * 0.12;
    }

    // Gentle starfield drift
    starField.rotation.y = elapsed * 0.015 + scrollProgress * 0.45;
    starField.rotation.x = scrollProgress * 0.18;

    renderer.render(scene, camera);

    if (!reduceMotion && !document.hidden) {
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
  updateScrollState();
  renderFrame(performance.now());
}

initWebGLScene();
