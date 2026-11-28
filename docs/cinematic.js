/* Glass Nova — Cinematic Scroll Choreography, 4-Act 3D Depth Typography & Telemetry HUD
 * Inspired by Codrops Cinematic Scroll Animations (MIT).
 * Synchronizes depth-scaled typography directly with the 4-Act 3D camera flight in scene.js.
 */
(function () {
  var reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  // 1. Fast, non-blocking Cinematic Boot Veil
  var loader = document.getElementById('nova-loader');
  function dismissLoader() {
    if (!loader || loader.classList.contains('is-done')) return;
    loader.classList.add('is-done');
    setTimeout(function () {
      if (loader && loader.parentNode) loader.setAttribute('hidden', '');
    }, 600);
  }

  if (reduceMotion) {
    dismissLoader();
  } else {
    var fontReady = (document.fonts && document.fonts.ready) ? document.fonts.ready : Promise.resolve();
    Promise.race([
      fontReady,
      new Promise(function (resolve) { setTimeout(resolve, 420); })
    ]).then(function () {
      setTimeout(dismissLoader, 150);
    });
    window.addEventListener('load', function () { setTimeout(dismissLoader, 100); }, { once: true });
  }

  // 2. Synchronized 4-Act 3D Journey Typography Controller (#nova-journey)
  var journeySection = document.getElementById('nova-journey');
  var actEls = Array.prototype.slice.call(document.querySelectorAll('[data-nj-act]'));
  var jumpBtns = Array.prototype.slice.call(document.querySelectorAll('[data-nj-jump]'));

  var ACT_RANGES = [
    { start: 0.00, end: 0.25, label: '3D FLIGHT · APPROACH',  jumpP: 0.08 },
    { start: 0.25, end: 0.55, label: '3D FLIGHT · CORE DIVE', jumpP: 0.39 },
    { start: 0.55, end: 0.80, label: '3D FLIGHT · THE VAULT', jumpP: 0.67 },
    { start: 0.80, end: 1.00, label: '3D FLIGHT · HORIZON',   jumpP: 0.90 }
  ];

  function clamp01(v) { return Math.max(0, Math.min(1, v)); }

  function updateJourneyActs() {
    if (!journeySection || !actEls.length) return { inJourney: false, activeLabel: '' };

    var r = journeySection.getBoundingClientRect();
    var vh = Math.max(1, window.innerHeight);
    var range = Math.max(1, journeySection.offsetHeight - vh);
    var inJourney = r.top <= 40 && r.bottom >= vh - 40;
    var p = clamp01(-r.top / range);

    var activeIdx = 0;
    for (var i = 0; i < ACT_RANGES.length; i++) {
      var cfg = ACT_RANGES[i];
      var el = actEls[i];
      if (!el) continue;

      var span = Math.max(0.001, cfg.end - cfg.start);
      var local = (p - cfg.start) / span; // <0 before, 0..1 during, >1 after

      if (local >= 0 && local <= 1) activeIdx = i;

      var opacity = 0;
      var scale = 1;
      var ty = 0;

      if (local < 0) {
        opacity = 0;
        scale = reduceMotion ? 1 : 0.88;
        ty = reduceMotion ? 0 : 34;
      } else if (local < 0.22) {
        var tIn = clamp01(local / 0.22);
        var eIn = 1 - Math.pow(1 - tIn, 3);
        opacity = eIn;
        scale = reduceMotion ? 1 : (0.88 + 0.12 * eIn);
        ty = reduceMotion ? 0 : (34 * (1 - eIn));
      } else if (local < 0.78) {
        var tHold = (local - 0.22) / 0.56;
        opacity = 1;
        scale = reduceMotion ? 1 : (1.0 + tHold * 0.035);
        ty = reduceMotion ? 0 : (-tHold * 6);
      } else if (local <= 1.0) {
        var tOut = clamp01((local - 0.78) / 0.22);
        var eOut = tOut * tOut;
        opacity = i === ACT_RANGES.length - 1 ? Math.max(0.15, 1 - eOut * 0.85) : (1 - eOut);
        scale = reduceMotion ? 1 : (1.035 + eOut * 0.12);
        ty = reduceMotion ? 0 : (-6 - eOut * 22);
      } else {
        opacity = 0;
        scale = reduceMotion ? 1 : 1.15;
        ty = reduceMotion ? 0 : -28;
      }

      el.style.opacity = opacity.toFixed(3);
      el.style.transform = reduceMotion
        ? 'none'
        : ('translate3d(0, ' + ty.toFixed(1) + 'px, 0) scale(' + scale.toFixed(4) + ')');
      el.classList.toggle('is-live', opacity > 0.25);
    }

    jumpBtns.forEach(function (btn, idx) {
      btn.classList.toggle('is-active', idx === activeIdx);
      btn.setAttribute('aria-selected', String(idx === activeIdx));
    });

    return { inJourney: inJourney, activeLabel: ACT_RANGES[activeIdx].label };
  }

  jumpBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (!journeySection) return;
      var idx = parseInt(btn.getAttribute('data-nj-jump') || '0', 10);
      var cfg = ACT_RANGES[idx] || ACT_RANGES[0];
      var vh = Math.max(1, window.innerHeight);
      var range = Math.max(1, journeySection.offsetHeight - vh);
      var top = journeySection.getBoundingClientRect().top + window.scrollY;
      window.scrollTo({
        top: top + cfg.jumpP * range,
        behavior: reduceMotion ? 'auto' : 'smooth'
      });
    });
  });

  // 3. Telemetry HUD (Corner-bracketed 000% Progress & Active Chapter)
  var hudFill = document.getElementById('cine-hud-fill');
  var hudPct = document.getElementById('cine-hud-pct');
  var hudChapter = document.getElementById('cine-hud-chapter');
  var hudEl = document.getElementById('cine-hud');
  var scrollCue = document.getElementById('cine-scroll-cue');

  var chapters = [
    { id: 'reveal',       label: '01 · HERO' },
    { id: 'nova-journey', label: '3D FLIGHT' },
    { id: 'intro',        label: '02 · WHAT IS NOVA' },
    { id: 'modes',        label: '03 · MODES & VOICE' },
    { id: 'capabilities', label: '04 · CAPABILITIES' },
    { id: 'roadmap',      label: '05 · EVOLUTION' },
    { id: 'companions',   label: '06 · COMPANIONS' },
    { id: 'security',     label: '07 · SECURITY' },
    { id: 'setup',        label: '08 · SETUP' },
    { id: 'finale',       label: '09 · GLASS NOVA' }
  ];

  function updateHudAndJourney() {
    var jState = updateJourneyActs();

    var maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    var p = clamp01((window.scrollY || 0) / maxScroll);
    var pct = Math.round(p * 100);

    if (hudFill) hudFill.style.width = (p * 100).toFixed(2) + '%';
    if (hudPct) hudPct.textContent = String(pct).padStart(3, '0') + '%';

    var activeLabel = chapters[0].label;
    if (jState.inJourney && jState.activeLabel) {
      activeLabel = jState.activeLabel;
    } else {
      var offset = (window.scrollY || 0) + window.innerHeight * 0.36;
      for (var i = 0; i < chapters.length; i++) {
        var el = document.getElementById(chapters[i].id);
        if (el && el.offsetTop <= offset) activeLabel = chapters[i].label;
      }
    }
    if (hudChapter) hudChapter.textContent = activeLabel;

    var jBadge = document.querySelector('.nj-badge');
    if (jBadge && jState.inJourney && jState.activeLabel) {
      jBadge.textContent = jState.activeLabel + ' · ' + String(pct).padStart(3, '0') + '%';
    } else if (jBadge) {
      jBadge.textContent = '3D FLIGHT · SCROLL TO TRAVEL';
    }

    if (hudEl) {
      hudEl.classList.toggle('is-hidden', !!window.__novaRoadmapActive || !!jState.inJourney);
    }
    if (scrollCue) {
      scrollCue.classList.toggle('is-hidden', (window.scrollY || 0) > 120);
    }
  }

  window.addEventListener('scroll', updateHudAndJourney, { passive: true });
  window.addEventListener('resize', updateHudAndJourney, { passive: true });
  updateHudAndJourney();

  // 4. Accessible Kinetic Word-Split Utility
  function splitIntoWords(el) {
    if (!el || el.dataset.kineticSplit === 'true') return [];
    var originalText = el.textContent.trim();
    if (!originalText) return [];
    el.setAttribute('aria-label', originalText);
    el.dataset.kineticSplit = 'true';

    var words = [];
    function processNode(node) {
      if (node.nodeType === 3) {
        var text = node.nodeValue;
        if (!text || !text.trim()) return;
        var frag = document.createDocumentFragment();
        var parts = text.split(/(\s+)/);
        parts.forEach(function (part) {
          if (!part) return;
          if (/^\s+$/.test(part)) {
            frag.appendChild(document.createTextNode(part));
          } else {
            var wrap = document.createElement('span');
            wrap.className = 'k-word-wrap';
            wrap.setAttribute('aria-hidden', 'true');
            var inner = document.createElement('span');
            inner.className = 'k-word';
            inner.textContent = part;
            wrap.appendChild(inner);
            frag.appendChild(wrap);
            words.push(inner);
          }
        });
        node.parentNode.replaceChild(frag, node);
      } else if (node.nodeType === 1) {
        Array.prototype.slice.call(node.childNodes).forEach(processNode);
      }
    }
    processNode(el);
    return words;
  }

  // 5. GSAP + ScrollTrigger Choreography (skipped cleanly if reduced motion or CDN unavailable)
  if (reduceMotion) return;

  function initGSAP() {
    var gsap = window.gsap;
    var ScrollTrigger = window.ScrollTrigger;
    if (!gsap || !ScrollTrigger) return;

    gsap.registerPlugin(ScrollTrigger);
    gsap.defaults({ ease: 'power3.out' });

    // A. Hero Kinetic Entrance & Scroll-Out Depth
    var heroTitle = document.querySelector('#reveal h1[data-kinetic]');
    if (heroTitle) {
      var heroWords = splitIntoWords(heroTitle);
      gsap.fromTo(
        heroWords,
        { yPercent: 110, opacity: 0, rotateX: -28 },
        { yPercent: 0, opacity: 1, rotateX: 0, duration: 0.9, stagger: 0.045, delay: 0.16 }
      );
    }

    var heroLead = document.querySelector('#reveal .lead');
    var heroCta = document.querySelector('#reveal .hero-cta');
    var heroMetrics = document.querySelectorAll('#reveal .hero-metric');
    var heroStage = document.querySelector('#reveal .hero-stage-wrap');

    if (heroLead) gsap.fromTo(heroLead, { y: 22, opacity: 0 }, { y: 0, opacity: 1, duration: 0.8, delay: 0.4 });
    if (heroCta) gsap.fromTo(heroCta, { y: 20, opacity: 0 }, { y: 0, opacity: 1, duration: 0.78, delay: 0.52 });
    if (heroMetrics.length) {
      gsap.fromTo(heroMetrics, { y: 18, opacity: 0 }, { y: 0, opacity: 1, duration: 0.68, stagger: 0.08, delay: 0.64 });
    }
    if (heroStage) {
      gsap.fromTo(heroStage, { y: 36, opacity: 0, scale: 0.96 }, { y: 0, opacity: 1, scale: 1, duration: 1.0, delay: 0.28 });
      if (window.innerWidth >= 900) {
        gsap.to(heroStage, {
          y: -58,
          scale: 0.94,
          opacity: 0.25,
          ease: 'none',
          scrollTrigger: { trigger: '#reveal', start: 'top top', end: 'bottom top', scrub: 0.7 }
        });
      }
    }

    // B. Scroll-triggered Kinetic Typography for all other [data-kinetic] headings
    document.querySelectorAll('[data-kinetic]').forEach(function (el) {
      if (el.closest('#reveal')) return;
      var words = splitIntoWords(el);
      if (!words.length) return;
      gsap.fromTo(
        words,
        { yPercent: 105, opacity: 0 },
        {
          yPercent: 0,
          opacity: 1,
          duration: 0.78,
          stagger: 0.036,
          scrollTrigger: { trigger: el, start: 'top 86%', once: true }
        }
      );
    });

    // C. Cinematic Introduction Pillars Stagger
    var introCards = document.querySelectorAll('.intro-pillar');
    if (introCards.length) {
      gsap.fromTo(
        introCards,
        { y: 34, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.78,
          stagger: 0.14,
          scrollTrigger: { trigger: '#intro .intro-pillars', start: 'top 84%', once: true }
        }
      );
    }

    // D. Tactile Keycap Deck Staggers (Companions & Security)
    document.querySelectorAll('.keycap-deck').forEach(function (deck) {
      var keys = deck.querySelectorAll('.keycap');
      if (!keys.length) return;
      gsap.fromTo(
        keys,
        { y: 22, opacity: 0, scale: 0.94 },
        {
          y: 0,
          opacity: 1,
          scale: 1,
          duration: 0.55,
          stagger: 0.055,
          ease: 'power2.out',
          scrollTrigger: { trigger: deck, start: 'top 86%', once: true }
        }
      );
    });

    // E. Finale Scene Dramatic Depth Reveal
    var finaleTitle = document.querySelector('#finale .finale-wordmark');
    if (finaleTitle) {
      gsap.fromTo(
        finaleTitle,
        { scale: 0.86, opacity: 0, letterSpacing: '0.2em' },
        {
          scale: 1,
          opacity: 1,
          letterSpacing: '0.06em',
          duration: 1.2,
          scrollTrigger: { trigger: '#finale', start: 'top 80%', once: true }
        }
      );
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGSAP);
  } else {
    initGSAP();
  }
})();
