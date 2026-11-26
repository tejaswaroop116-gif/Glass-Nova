/* Glass Nova — 3D Perspective Deep-Space Dust, Twinkle & Scroll Warp */
(function () {
  var canvas = document.getElementById('nova-webgl');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  if (!ctx) return;

  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var isMobile = window.innerWidth < 700;
  var W = 0, H = 0, DPR = 1;
  var MAX_Z = 2.4;

  var count = isMobile ? 75 : 170;
  var stars = [];
  for (var i = 0; i < count; i++) {
    stars.push({
      x: (Math.random() - 0.5) * 3.2,
      y: (Math.random() - 0.5) * 2.4,
      z: 0.18 + Math.random() * (MAX_Z - 0.18),
      size: 0.9 + Math.random() * 1.7,
      phase: Math.random() * Math.PI * 2,
      twinkleSpeed: 1.2 + Math.random() * 2.4,
      isGlow: i % 7 === 0,
      tint: i % 4 === 0 ? '205, 180, 255' : (i % 3 === 0 ? '110, 232, 255' : '220, 242, 255')
    });
  }

  var mouse = { x: 0, y: 0, tx: 0, ty: 0 };
  var lastScrollY = window.scrollY || 0;
  var scrollVel = 0;
  var scrollP = 0;
  var t = 0;
  var rafId = 0;

  function resize() {
    isMobile = window.innerWidth < 700;
    DPR = Math.min(window.devicePixelRatio || 1, isMobile ? 1.5 : 2);
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = Math.max(1, Math.floor(W * DPR));
    canvas.height = Math.max(1, Math.floor(H * DPR));
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  window.addEventListener('resize', resize, { passive: true });
  window.addEventListener('scroll', function () {
    var curY = window.scrollY || 0;
    var dy = curY - lastScrollY;
    lastScrollY = curY;
    scrollVel = Math.max(-0.045, Math.min(0.045, scrollVel + dy * 0.00018));
    var maxS = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    scrollP = Math.min(1, Math.max(0, curY / maxS));
  }, { passive: true });

  window.addEventListener('pointermove', function (e) {
    if (isMobile) return;
    mouse.tx = (e.clientX / Math.max(1, W) - 0.5) * 2;
    mouse.ty = (e.clientY / Math.max(1, H) - 0.5) * 2;
  }, { passive: true });

  function draw() {
    ctx.clearRect(0, 0, W, H);

    mouse.x += (mouse.tx - mouse.x) * 0.06;
    mouse.y += (mouse.ty - mouse.y) * 0.06;
    scrollVel *= 0.90;
    if (!reduceMotion) t += 0.016;

    // Breathing cyan & violet 3D nebula clouds
    var nebR = Math.max(W, H) * 0.58;
    var cx1 = W * (0.24 + Math.sin(t * 0.25) * 0.05 + mouse.x * 0.045);
    var cy1 = H * (0.28 + Math.cos(t * 0.20) * 0.04 + mouse.y * 0.045);
    var g1 = ctx.createRadialGradient(cx1, cy1, 10, cx1, cy1, nebR);
    g1.addColorStop(0, 'rgba(56, 232, 255, 0.14)');
    g1.addColorStop(0.55, 'rgba(59, 130, 246, 0.05)');
    g1.addColorStop(1, 'rgba(3, 5, 10, 0)');
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, W, H);

    var cx2 = W * (0.76 - Math.cos(t * 0.22) * 0.05 - mouse.x * 0.045);
    var cy2 = H * (0.68 - Math.sin(t * 0.18) * 0.04 - mouse.y * 0.045);
    var g2 = ctx.createRadialGradient(cx2, cy2, 10, cx2, cy2, nebR * 0.95);
    g2.addColorStop(0, 'rgba(155, 123, 255, 0.16)');
    g2.addColorStop(0.55, 'rgba(139, 92, 246, 0.05)');
    g2.addColorStop(1, 'rgba(3, 5, 10, 0)');
    ctx.fillStyle = g2;
    ctx.fillRect(0, 0, W, H);

    // 3D perspective camera projection center (shifts with mouse parallax)
    var vpX = W * 0.5 - mouse.x * 75;
    var vpY = H * 0.48 - mouse.y * 55;
    var fov = Math.max(W, H) * 0.62;
    var speedZ = reduceMotion ? 0 : (0.0032 + Math.abs(scrollVel) * 0.72);

    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];
      var prevZ = s.z;
      if (!reduceMotion) {
        s.z -= speedZ;
        if (s.z <= 0.15) {
          s.z = MAX_Z;
          prevZ = MAX_Z;
          s.x = (Math.random() - 0.5) * 3.2;
          s.y = (Math.random() - 0.5) * 2.4;
        }
      }

      var invZ = 1 / s.z;
      var sx = vpX + (s.x + mouse.x * 0.18) * fov * invZ;
      var sy = vpY + (s.y + mouse.y * 0.14) * fov * invZ;

      if (sx < -30 || sx > W + 30 || sy < -30 || sy > H + 30) continue;

      var depthFade = Math.min(1, (MAX_Z - s.z) * 1.2) * Math.min(1, (s.z - 0.15) * 3.2);
      var twinkle = 0.68 + 0.32 * Math.sin(t * s.twinkleSpeed + s.phase);
      var alpha = Math.max(0.14, Math.min(0.98, depthFade * twinkle * 0.96));
      var rad = Math.max(0.75, Math.min(4.4, s.size * invZ * 0.92));

      // Subtle 3D warp streak when scrolling fast
      if (!reduceMotion && Math.abs(scrollVel) > 0.003) {
        var pInvZ = 1 / Math.min(MAX_Z, prevZ + Math.abs(scrollVel) * 2.2);
        var px = vpX + (s.x + mouse.x * 0.18) * fov * pInvZ;
        var py = vpY + (s.y + mouse.y * 0.14) * fov * pInvZ;
        ctx.strokeStyle = 'rgba(' + s.tint + ', ' + (alpha * 0.45).toFixed(3) + ')';
        ctx.lineWidth = Math.max(0.7, rad * 0.7);
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(sx, sy);
        ctx.stroke();
      }

      if (s.isGlow && rad > 1.0) {
        var halo = ctx.createRadialGradient(sx, sy, 0.2, sx, sy, rad * 3.8);
        halo.addColorStop(0, 'rgba(255, 255, 255, ' + Math.min(1, alpha * 1.15).toFixed(3) + ')');
        halo.addColorStop(0.35, 'rgba(' + s.tint + ', ' + (alpha * 0.62).toFixed(3) + ')');
        halo.addColorStop(1, 'rgba(' + s.tint + ', 0)');
        ctx.fillStyle = halo;
        ctx.beginPath();
        ctx.arc(sx, sy, rad * 3.8, 0, Math.PI * 2);
        ctx.fill();
      } else {
        ctx.fillStyle = 'rgba(' + s.tint + ', ' + alpha.toFixed(3) + ')';
        ctx.beginPath();
        ctx.arc(sx, sy, rad, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    if (!reduceMotion && !document.hidden) {
      rafId = requestAnimationFrame(draw);
    }
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = 0;
    } else if (!reduceMotion && !rafId) {
      rafId = requestAnimationFrame(draw);
    }
  });

  resize();
  draw();
})();
