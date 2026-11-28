/* Glass Nova — Cinematic Scroll Choreography, Kinetic Typography & Telemetry HUD
 * Inspired by Codrops Cinematic Scroll Animations (MIT).
 * Uses GSAP + ScrollTrigger when available, with full graceful fallback for offline / reduced-motion environments.
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
      setTimeout(dismissLoader, 160);
    });
    window.addEventListener('load', function () { setTimeout(dismissLoader, 120); }, { once: true });
  }

  // 2. Telemetry HUD (Corner-bracketed 000% Progress & Active Chapter, Codrops Demo 2 inspired)
  var hudFill = document.getElementById('cine-hud-fill');
  var hudPct = document.getElementById('cine-hud-pct');
  var hudChapter = document.getElementById('cine-hud-chapter');
  var hudEl = document.getElementById('cine-hud');
  var scrollCue = document.getElementById('cine-scroll-cue');

  var chapters = [
    { id: 'reveal',       label: '01 · HERO' },
    { id: 'intro',        label: '02 · WHAT IS NOVA' },
    { id: 'modes',        label: '03 · MODES & VOICE' },
    { id: 'capabilities', label: '04 · CAPABILITIES' },
    { id: 'roadmap',      label: '05 · EVOLUTION' },
    { id: 'companions',   label: '06 · COMPANIONS' },
    { id: 'security',     label: '07 · SECURITY' },
    { id: 'setup',        label: '08 · SETUP' },
    { id: 'finale',       label: '09 · GLASS NOVA' }
  ];

  function updateHud() {
    var maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    var p = Math.max(0, Math.min(1, (window.scrollY || 0) / maxScroll));
    var pct = Math.round(p * 100);

    if (hudFill) hudFill.style.width = (p * 100).toFixed(2) + '%';
    if (hudPct) hudPct.textContent = String(pct).padStart(3, '0') + '%';

    var offset = (window.scrollY || 0) + window.innerHeight * 0.36;
    var activeLabel = chapters[0].label;
    for (var i = 0; i < chapters.length; i++) {
      var el = document.getElementById(chapters[i].id);
      if (el && el.offsetTop <= offset) activeLabel = chapters[i].label;
    }
    if (hudChapter) hudChapter.textContent = activeLabel;

    // Hide global HUD while inside 3D Spiral Roadmap so HUDs never overlap
    if (hudEl) {
      hudEl.classList.toggle('is-hidden', !!window.__novaRoadmapActive);
    }
    if (scrollCue) {
      scrollCue.classList.toggle('is-hidden', (window.scrollY || 0) > 120);
    }
  }

  window.addEventListener('scroll', updateHud, { passive: true });
  window.addEventListener('resize', updateHud, { passive: true });
  updateHud();

  // 3. Accessible Kinetic Word-Split Utility (no paid SplitText plugin required)
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
        var children = Array.prototype.slice.call(node.childNodes);
        children.forEach(processNode);
      }
    }
    processNode(el);
    return words;
  }

  // 4. GSAP + ScrollTrigger Choreography (skipped cleanly if reduced motion or CDN unavailable)
  if (reduceMotion) return;

  function initGSAP() {
    var gsap = window.gsap;
    var ScrollTrigger = window.ScrollTrigger;
    if (!gsap || !ScrollTrigger) return;

    gsap.registerPlugin(ScrollTrigger);
    gsap.defaults({ ease: 'power3.out' });

    // A. Hero Kinetic Entrance
    var heroTitle = document.querySelector('#reveal h1[data-kinetic]');
    if (heroTitle) {
      var heroWords = splitIntoWords(heroTitle);
      gsap.fromTo(
        heroWords,
        { yPercent: 110, opacity: 0, rotateX: -28 },
        {
          yPercent: 0,
          opacity: 1,
          rotateX: 0,
          duration: 0.9,
          stagger: 0.045,
          delay: 0.18,
          ease: 'power3.out'
        }
      );
    }

    var heroLead = document.querySelector('#reveal .lead');
    var heroCta = document.querySelector('#reveal .hero-cta');
    var heroMetrics = document.querySelectorAll('#reveal .hero-metric');
    var heroStage = document.querySelector('#reveal .hero-stage-wrap');

    if (heroLead) {
      gsap.fromTo(heroLead, { y: 22, opacity: 0 }, { y: 0, opacity: 1, duration: 0.85, delay: 0.42 });
    }
    if (heroCta) {
      gsap.fromTo(heroCta, { y: 20, opacity: 0 }, { y: 0, opacity: 1, duration: 0.8, delay: 0.56 });
    }
    if (heroMetrics.length) {
      gsap.fromTo(
        heroMetrics,
        { y: 18, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.7, stagger: 0.09, delay: 0.68 }
      );
    }
    if (heroStage) {
      gsap.fromTo(
        heroStage,
        { y: 36, opacity: 0, scale: 0.96 },
        { y: 0, opacity: 1, scale: 1, duration: 1.05, delay: 0.3, ease: 'power2.out' }
      );

      // Subtle scroll-linked 3D depth parallax as user leaves Hero
      if (window.innerWidth >= 900) {
        gsap.to(heroStage, {
          y: -48,
          scale: 0.97,
          ease: 'none',
          scrollTrigger: {
            trigger: '#reveal',
            start: 'top top',
            end: 'bottom top',
            scrub: 0.8
          }
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
          stagger: 0.038,
          ease: 'power3.out',
          scrollTrigger: {
            trigger: el,
            start: 'top 86%',
            once: true
          }
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
          scrollTrigger: {
            trigger: '#intro .intro-pillars',
            start: 'top 84%',
            once: true
          }
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
          scrollTrigger: {
            trigger: deck,
            start: 'top 86%',
            once: true
          }
        }
      );
    });

    // E. Finale Scene Dramatic Reveal
    var finaleTitle = document.querySelector('#finale .finale-wordmark');
    if (finaleTitle) {
      gsap.fromTo(
        finaleTitle,
        { scale: 0.9, opacity: 0, letterSpacing: '0.18em' },
        {
          scale: 1,
          opacity: 1,
          letterSpacing: '0.06em',
          duration: 1.15,
          ease: 'power3.out',
          scrollTrigger: {
            trigger: '#finale',
            start: 'top 80%',
            once: true
          }
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
