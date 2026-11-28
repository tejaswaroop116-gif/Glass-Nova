/* Glass Nova — Universal Adaptive Performance Engine & Zero-Reflow Layout Cache
 * Automatically profiles CPU cores, device memory, mobile/touch class, network save-data,
 * WebGL renderer capability, and live FPS pacing to select & dynamically tune 4 tiers:
 *   "ultra" | "high" | "medium" | "low"
 * Supports ?perf=low|medium|high|ultra URL override for deterministic QA testing.
 */
(function () {
  var TIER_ORDER = ['low', 'medium', 'high', 'ultra'];

  var TIER_PRESETS = {
    ultra: {
      tier: 'ultra',
      maxDpr: 1.5,
      antialias: true,
      starCount: 1050,
      trailCount: 18,
      gateCount: 12,
      shardCount: 12,
      ringSegments: 84,
      sphereSegments: 28,
      lights: 3,
      activeFps: 60,
      idleFps: 24,
      pauseOnIdle: false,
      roadmapStarCount: 1100,
      roadmapTubeSegs: 360,
      roadmapTexSize: 1024,
      enableParallax: true
    },
    high: {
      tier: 'high',
      maxDpr: 1.25,
      antialias: false,
      starCount: 620,
      trailCount: 12,
      gateCount: 9,
      shardCount: 12,
      ringSegments: 64,
      sphereSegments: 22,
      lights: 2,
      activeFps: 60,
      idleFps: 18,
      pauseOnIdle: false,
      roadmapStarCount: 700,
      roadmapTubeSegs: 260,
      roadmapTexSize: 512,
      enableParallax: true
    },
    medium: {
      tier: 'medium',
      maxDpr: 1.0,
      antialias: false,
      starCount: 340,
      trailCount: 7,
      gateCount: 7,
      shardCount: 8,
      ringSegments: 48,
      sphereSegments: 16,
      lights: 2,
      activeFps: 45,
      idleFps: 0,
      pauseOnIdle: true,
      roadmapStarCount: 420,
      roadmapTubeSegs: 180,
      roadmapTexSize: 512,
      enableParallax: false
    },
    low: {
      tier: 'low',
      maxDpr: 0.85,
      antialias: false,
      starCount: 160,
      trailCount: 4,
      gateCount: 5,
      shardCount: 6,
      ringSegments: 32,
      sphereSegments: 12,
      lights: 1,
      activeFps: 30,
      idleFps: 0,
      pauseOnIdle: true,
      roadmapStarCount: 220,
      roadmapTubeSegs: 120,
      roadmapTexSize: 256,
      enableParallax: false
    }
  };

  function detectInitialTier() {
    // Allow deterministic QA override via ?perf=low|medium|high|ultra
    try {
      var params = new URLSearchParams(window.location.search);
      var forced = (params.get('perf') || '').toLowerCase();
      if (TIER_PRESETS[forced]) return forced;
    } catch (_) {}

    var reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    var isMobile = window.innerWidth < 768 || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || '');
    var isAndroid = /Android/i.test(navigator.userAgent || '');
    var cores = navigator.hardwareConcurrency || 4;
    var mem = navigator.deviceMemory || 4;
    var saveData = !!(navigator.connection && navigator.connection.saveData);

    // Quick WebGL renderer probe (detect software rasterizers like SwiftShader / llvmpipe)
    var isSoftwareGL = false;
    var gpuName = '';
    try {
      var probe = document.createElement('canvas');
      var gl = probe.getContext('webgl2') || probe.getContext('webgl') || probe.getContext('experimental-webgl');
      if (!gl) {
        return 'low';
      }
      var dbg = gl.getExtension('WEBGL_debug_renderer_info');
      if (dbg) {
        gpuName = String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) || '');
        if (/swiftshader|llvmpipe|software|microsoft basic render/i.test(gpuName)) {
          isSoftwareGL = true;
        }
      }
      var lose = gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();
    } catch (_) {}

    if (saveData || isSoftwareGL || cores <= 2 || mem <= 2) return 'low';
    if (reduceMotion) return 'medium';

    if (isMobile) {
      // Low/mid Android phones start at low/medium; high-end iPhones/flagships get medium/high
      if (isAndroid && (cores <= 4 || mem <= 4)) return 'low';
      if (cores >= 6 && mem >= 6 && !isAndroid) return 'high';
      return 'medium';
    }

    // Desktop / Laptop classification
    var isIntegrated = /intel|uhd|iris|radeon\(tm\) graphics|vega 3|vega 6|vega 8|apple m1|apple m2/i.test(gpuName);
    var isDiscrete = /nvidia|geforce|rtx|gtx|radeon rx|arc a|apple m3|apple m4|pro|max/i.test(gpuName);

    if (isDiscrete && cores >= 8 && mem >= 8) return 'ultra';
    if (isIntegrated || cores <= 4 || mem <= 4) return 'medium';
    if (cores >= 8 && mem >= 8) return 'high';
    return 'medium';
  }

  var currentTier = detectInitialTier();
  var listeners = [];

  function applyTierToDom(tier) {
    document.documentElement.setAttribute('data-perf-tier', tier);
  }
  applyTierToDom(currentTier);

  // Cached Layout Registry — eliminates getBoundingClientRect() / offsetTop inside rAF & scroll!
  var layoutCache = {
    vw: window.innerWidth,
    vh: Math.max(1, window.innerHeight),
    maxScroll: 1,
    journeyTop: 0,
    journeyHeight: 1,
    journeyRange: 1,
    sections: {}
  };

  var TRACKED_IDS = [
    'reveal', 'nova-journey', 'intro', 'modes', 'capabilities',
    'roadmap', 'companions', 'security', 'setup', 'finale'
  ];

  function measureLayout() {
    layoutCache.vw = window.innerWidth;
    layoutCache.vh = Math.max(1, window.innerHeight);
    layoutCache.maxScroll = Math.max(1, document.documentElement.scrollHeight - layoutCache.vh);

    for (var i = 0; i < TRACKED_IDS.length; i++) {
      var id = TRACKED_IDS[i];
      var el = document.getElementById(id);
      if (el) {
        layoutCache.sections[id] = {
          top: el.offsetTop,
          height: el.offsetHeight,
          bottom: el.offsetTop + el.offsetHeight
        };
      }
    }

    var j = layoutCache.sections['nova-journey'];
    if (j) {
      layoutCache.journeyTop = j.top;
      layoutCache.journeyHeight = j.height;
      layoutCache.journeyRange = Math.max(1, j.height - layoutCache.vh);
    }
  }

  // Live FPS Watchdog: automatically steps down a tier if frames consistently stutter (<30fps)
  var slowFrameStreak = 0;
  var lastWatchCheck = performance.now();
  var downgradedOnce = false;

  function reportFrameDelta(dtMs) {
    if (downgradedOnce) return;
    if (dtMs > 36 && dtMs < 250) {
      slowFrameStreak++;
      if (slowFrameStreak >= 24) {
        var idx = TIER_ORDER.indexOf(currentTier);
        if (idx > 0) {
          downgradedOnce = true;
          setTier(TIER_ORDER[idx - 1]);
        }
        slowFrameStreak = 0;
      }
    } else if (dtMs <= 26) {
      slowFrameStreak = Math.max(0, slowFrameStreak - 1);
    }
    lastWatchCheck = performance.now();
  }

  function setTier(newTier) {
    if (!TIER_PRESETS[newTier] || newTier === currentTier) return;
    currentTier = newTier;
    applyTierToDom(currentTier);
    var cfg = getConfig();
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](cfg); } catch (_) {}
    }
  }

  function getConfig() {
    return Object.assign({}, TIER_PRESETS[currentTier]);
  }

  var resizeTimer = 0;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(measureLayout, 140);
  }, { passive: true });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', measureLayout);
  } else {
    measureLayout();
  }
  window.addEventListener('load', function () {
    measureLayout();
    setTimeout(measureLayout, 400);
  }, { once: true });

  window.__novaPerf = {
    get tier() { return currentTier; },
    getConfig: getConfig,
    setTier: setTier,
    onTierChange: function (fn) { listeners.push(fn); },
    reportFrameDelta: reportFrameDelta,
    layout: layoutCache,
    measureLayout: measureLayout
  };
})();
