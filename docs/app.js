/* Glass Nova — Streamlined 3D Interactions & Code-Verified Explorers */
(function () {
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var isCoarse = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;

  // 1. Scroll progress rail & active navigation link
  var railFill = document.getElementById('scroll-rail-fill');
  var navLinks = Array.prototype.slice.call(document.querySelectorAll('.nav-links a[href^="#"]'));
  var sections = navLinks.map(function (a) {
    return document.getElementById(a.getAttribute('href').slice(1));
  }).filter(Boolean);

  function onScroll() {
    var maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    var pct = Math.min(100, Math.max(0, (window.scrollY / maxScroll) * 100));
    if (railFill) railFill.style.width = pct.toFixed(2) + '%';

    var currentId = '';
    var offset = window.scrollY + window.innerHeight * 0.34;
    sections.forEach(function (sec) {
      if (sec.offsetTop <= offset) currentId = sec.id;
    });
    navLinks.forEach(function (a) {
      a.classList.toggle('active', a.getAttribute('href') === '#' + currentId);
    });
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  // 2. Responsive navigation menu toggle
  var menuBtn = document.getElementById('menu-toggle');
  var navList = document.getElementById('primary-nav');
  if (menuBtn && navList) {
    menuBtn.addEventListener('click', function () {
      var isOpen = navList.classList.toggle('open');
      menuBtn.setAttribute('aria-expanded', String(isOpen));
    });
    navList.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function () {
        navList.classList.remove('open');
        menuBtn.setAttribute('aria-expanded', 'false');
      });
    });
  }

  // 3. Liquid Glass specular cursor tracking + 3D spatial tilt stages
  if (!reduceMotion && !isCoarse) {
    document.querySelectorAll('.liquid-glass').forEach(function (el) {
      el.addEventListener('pointermove', function (e) {
        var r = el.getBoundingClientRect();
        var x = ((e.clientX - r.left) / Math.max(1, r.width)) * 100;
        var y = ((e.clientY - r.top) / Math.max(1, r.height)) * 100;
        el.style.setProperty('--mx', x.toFixed(1) + '%');
        el.style.setProperty('--my', y.toFixed(1) + '%');
      }, { passive: true });
    });

    document.querySelectorAll('[data-tilt-stage]').forEach(function (stage) {
      var wrap = stage.parentElement || stage;
      var maxTilt = parseFloat(stage.getAttribute('data-tilt-max') || '7');
      wrap.addEventListener('pointermove', function (e) {
        var r = wrap.getBoundingClientRect();
        var nx = ((e.clientX - r.left) / Math.max(1, r.width) - 0.5) * 2;
        var ny = ((e.clientY - r.top) / Math.max(1, r.height) - 0.5) * 2;
        stage.style.transform = 'rotateX(' + (-ny * maxTilt).toFixed(2) + 'deg) rotateY(' + (nx * maxTilt).toFixed(2) + 'deg)';
      }, { passive: true });
      wrap.addEventListener('pointerleave', function () {
        stage.style.transform = 'rotateX(0deg) rotateY(0deg)';
      });
    });
  }

  // 4. Operating Mode Console Tabs (Online / Offline / Hybrid)
  var modeTabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"][data-mode-tab]'));
  modeTabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      modeTabs.forEach(function (t) {
        var selected = t === tab;
        t.setAttribute('aria-selected', String(selected));
        var panel = document.getElementById(t.getAttribute('aria-controls'));
        if (panel) panel.hidden = !selected;
      });
    });
  });

  // 5. Independent Voice & Text Mode Lock Web Preview
  var micBtn = document.getElementById('btn-mic');
  var ttsBtn = document.getElementById('btn-tts');
  var textBtn = document.getElementById('btn-textmode');
  var simReadout = document.getElementById('control-readout');
  var audioState = { mic: true, tts: true, textMode: false };

  function renderAudioSim() {
    if (!micBtn || !ttsBtn || !textBtn || !simReadout) return;
    micBtn.setAttribute('aria-pressed', String(audioState.mic));
    micBtn.textContent = 'Mic Listening: ' + (audioState.mic ? 'ON' : 'OFF');
    ttsBtn.setAttribute('aria-pressed', String(audioState.tts));
    ttsBtn.textContent = 'Spoken Replies: ' + (audioState.tts ? 'ON' : 'OFF');
    textBtn.setAttribute('aria-pressed', String(audioState.textMode));
    textBtn.textContent = 'Text Mode Lock: ' + (audioState.textMode ? 'ON' : 'OFF');

    if (audioState.textMode) {
      simReadout.innerHTML = '<strong>Text Mode Locked:</strong> Wake-word detection and conversational microphone listening stay paused indefinitely until you manually switch back. Chat stays active, and Spoken Replies are ' + (audioState.tts ? 'ON (optional audio readback).' : 'OFF (quiet text-only chat).');
    } else if (audioState.mic && audioState.tts) {
      simReadout.innerHTML = '<strong>Full Voice Mode:</strong> Microphone listening and spoken replies are both active across Online (Gemini Live), Offline (Vosk + Windows SAPI), and Hybrid modes.';
    } else if (audioState.mic && !audioState.tts) {
      simReadout.innerHTML = '<strong>Speak-to-Text Only:</strong> Nova listens to your voice commands and displays silent written replies in chat without speaking aloud.';
    } else if (!audioState.mic && audioState.tts) {
      simReadout.innerHTML = '<strong>Type-to-Speak:</strong> Microphone is muted while you type, and Nova reads responses aloud.';
    } else {
      simReadout.innerHTML = '<strong>Silent Chat:</strong> Both microphone listening and spoken replies are muted for quiet text interaction.';
    }
  }

  if (micBtn && ttsBtn && textBtn) {
    micBtn.addEventListener('click', function () {
      audioState.mic = !audioState.mic;
      if (audioState.mic) audioState.textMode = false;
      renderAudioSim();
    });
    ttsBtn.addEventListener('click', function () {
      audioState.tts = !audioState.tts;
      renderAudioSim();
    });
    textBtn.addEventListener('click', function () {
      audioState.textMode = !audioState.textMode;
      if (audioState.textMode) {
        audioState.mic = false;
        audioState.tts = false;
      }
      renderAudioSim();
    });
    renderAudioSim();
  }

  // 6. Interactive Capability Theater
  var capabilities = {
    screen: {
      title: 'Screen Inspection & Point at Screen',
      desc: 'Capture and inspect your active screen or a pointed region to explain errors, UI layouts, or content. Every capture checks the fail-closed screen_view permission first.',
      pills: [
        { cls: 'pill-cyan', text: 'Online: Gemini Vision' },
        { cls: 'pill-emerald', text: 'Offline: Ollama moondream' },
        { cls: 'pill-amber', text: 'Requires: screen_view' }
      ],
      ref: 'desktop_agent.py · nova_offline_ai.py · permissions.py'
    },
    automation: {
      title: 'Verified Desktop & Browser Automation',
      desc: 'Interact with Windows UI controls via UIAutomation / pywinauto and run web tasks through Playwright. Actions are verified and gated behind explicit mouse_keyboard and browser_action permissions.',
      pills: [
        { cls: 'pill-cyan', text: 'Windows UIAutomation' },
        { cls: 'pill-violet', text: 'Playwright Browser' },
        { cls: 'pill-amber', text: 'Requires: mouse_keyboard / browser_action' }
      ],
      ref: 'desktop_agent.py · tools_browser.py · permissions.py'
    },
    media: {
      title: 'Document, PDF & Video Analysis',
      desc: 'Extract text locally from PDF, DOCX, TXT, CSV, XLSX, and PPTX files, or sample video keyframes via OpenCV and analyze them with Gemini online or local Ollama offline.',
      pills: [
        { cls: 'pill-emerald', text: 'Local File Extraction' },
        { cls: 'pill-cyan', text: 'OpenCV Keyframes' },
        { cls: 'pill-amber', text: 'Requires: file_read' }
      ],
      ref: 'tools_files.py · video_agent.py · nova_offline_ai.py'
    },
    knowledge: {
      title: 'Opt-in Local Knowledge & Safe Python Sandbox',
      desc: 'Index folders you explicitly opt into for local semantic search (using Ollama nomic-embed-text or local TF-IDF fallback) and execute math/data expressions inside an AST-restricted Safe Python sandbox.',
      pills: [
        { cls: 'pill-emerald', text: '100% Offline Capable' },
        { cls: 'pill-violet', text: 'AST-Restricted Sandbox' },
        { cls: 'pill-cyan', text: 'Bounded 5-Step OfflineAgent' }
      ],
      ref: 'nova_local_knowledge.py · nova_offline_agent.py · nova_offline_ai.py'
    },
    workflows: {
      title: 'Guided Walkthroughs & Semantic Workflows',
      desc: 'Generate step-by-step on-screen guided walkthroughs (with offline Ollama fallback) and save reusable semantic workflows for recurring desktop sequences.',
      pills: [
        { cls: 'pill-cyan', text: 'Step-by-Step Guidance' },
        { cls: 'pill-emerald', text: 'Offline Ollama Fallback' },
        { cls: 'pill-violet', text: 'Reusable Workflows' }
      ],
      ref: 'walkthrough_engine.py · workflow_engine.py'
    },
    gestures: {
      title: 'Hand-Gesture Mouse & Clean Lifecycle',
      desc: 'Optional MediaPipe + OpenCV webcam hand-gesture mouse control gated by both media_access and mouse_keyboard, backed by a centralized lifecycle manager so clicking × shuts down audio, wake listener, and threads cleanly.',
      pills: [
        { cls: 'pill-violet', text: 'MediaPipe + OpenCV' },
        { cls: 'pill-amber', text: 'Requires: media_access + mouse_keyboard' },
        { cls: 'pill-emerald', text: 'Zero-Orphan Shutdown' }
      ],
      ref: 'hand_gesture_mouse.py · nova_lifecycle.py'
    }
  };

  var capTitle = document.getElementById('cap-title');
  var capDesc = document.getElementById('cap-desc');
  var capPills = document.getElementById('cap-pills');
  var capRef = document.getElementById('cap-ref');
  var capBtns = Array.prototype.slice.call(document.querySelectorAll('[data-cap]'));

  function selectCap(key) {
    var item = capabilities[key];
    if (!item || !capTitle || !capDesc || !capPills || !capRef) return;
    capTitle.textContent = item.title;
    capDesc.textContent = item.desc;
    capRef.textContent = item.ref;
    capPills.innerHTML = item.pills.map(function (p) {
      return '<span class="pill ' + p.cls + '">' + p.text + '</span>';
    }).join('');
    capBtns.forEach(function (b) {
      b.setAttribute('aria-selected', String(b.getAttribute('data-cap') === key));
    });
  }
  capBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      selectCap(btn.getAttribute('data-cap'));
    });
  });

  // 7. Interactive 3D Companion Orbit Stage (Click, Prev/Next & Touch Swipe)
  var compOrder = ['robot', 'ghost', 'pet', 'cloudy'];
  var companions = {
    robot: {
      name: 'NOVA Robot',
      src: 'assets/companion-robot.png',
      alt: 'Original NOVA Robot animated desktop companion',
      glow: 'rgba(56, 232, 255, 0.24)',
      desc: 'The flagship PySide6 desktop companion with glowing visor feedback on a frameless transparent Windows overlay.'
    },
    ghost: {
      name: 'Ghost',
      src: 'assets/companion-ghost.png',
      alt: 'Ghost animated desktop companion for Glass Nova',
      glow: 'rgba(155, 123, 255, 0.26)',
      desc: 'A lightweight floating spirit overlay rendered with smooth transparent hover motion and expressive eye states.'
    },
    pet: {
      name: 'NOVA Pet',
      src: 'assets/companion-pet.png',
      alt: 'NOVA Painter Pet animated desktop companion',
      glow: 'rgba(52, 211, 153, 0.22)',
      desc: 'A sprite-animated desktop pet featuring multi-frame desktop character animations right over your workspace.'
    },
    cloudy: {
      name: 'Cloudy',
      src: 'assets/companion-cloudy.png',
      alt: 'Cloudy blue mini-bot animated desktop companion',
      glow: 'rgba(79, 140, 255, 0.26)',
      desc: 'A playful blue mini-bot companion with glowing cyan eyes and smooth frame-based desktop animations.'
    }
  };

  var currentCompIdx = 0;
  var pedStage = document.getElementById('pedestal-stage');
  var pedSprite = document.getElementById('pedestal-sprite');
  var pedName = document.getElementById('comp-name');
  var pedDesc = document.getElementById('comp-desc');
  var rosterBtns = Array.prototype.slice.call(document.querySelectorAll('[data-companion]'));
  var prevCompBtn = document.getElementById('comp-prev');
  var nextCompBtn = document.getElementById('comp-next');

  function selectCompanion(key) {
    var c = companions[key];
    if (!c || !pedSprite || !pedName || !pedDesc || !pedStage) return;
    currentCompIdx = compOrder.indexOf(key);
    if (currentCompIdx < 0) currentCompIdx = 0;

    pedSprite.classList.add('is-switching');
    setTimeout(function () {
      pedSprite.src = c.src;
      pedSprite.alt = c.alt;
      pedName.textContent = c.name;
      pedDesc.textContent = c.desc;
      pedStage.style.setProperty('--comp-glow', c.glow);
      pedSprite.classList.remove('is-switching');
    }, reduceMotion ? 0 : 140);

    rosterBtns.forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-companion') === key));
    });
  }

  function stepCompanion(delta) {
    var nextIdx = (currentCompIdx + delta + compOrder.length) % compOrder.length;
    selectCompanion(compOrder[nextIdx]);
  }

  rosterBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      selectCompanion(btn.getAttribute('data-companion'));
    });
  });
  if (prevCompBtn) prevCompBtn.addEventListener('click', function () { stepCompanion(-1); });
  if (nextCompBtn) nextCompBtn.addEventListener('click', function () { stepCompanion(1); });

  // Touch swipe & drag support on the 3D companion stage for phones and laptops
  if (pedStage) {
    var startX = 0;
    pedStage.addEventListener('pointerdown', function (e) { startX = e.clientX; }, { passive: true });
    pedStage.addEventListener('pointerup', function (e) {
      var dx = e.clientX - startX;
      if (Math.abs(dx) > 40) stepCompanion(dx < 0 ? 1 : -1);
    }, { passive: true });
  }

  // 8. 9-Category Fail-Closed Permission Inspector
  var permInfo = {
    screen_view: '<strong>screen_view:</strong> Required before any single-frame screenshot or visual screen inspection in both Online and Offline modes.',
    continuous_screen: '<strong>continuous_screen (UI Button Click Required):</strong> Always requires an explicit UI click — never voice-only approval — before continuous screen observation.',
    mouse_keyboard: '<strong>mouse_keyboard:</strong> Required before simulated clicks, typing, UIAutomation actions, or hand-gesture mouse control.',
    file_read: '<strong>file_read:</strong> Required before reading local files or analyzing documents, PDFs, and videos.',
    file_write: '<strong>file_write:</strong> Required before creating or modifying files on disk.',
    file_delete: '<strong>file_delete (UI Button Click Required):</strong> High-impact destructive action; always requires an explicit UI button click, never voice-only approval.',
    browser_action: '<strong>browser_action:</strong> Required before Playwright-driven browser navigation or page interaction.',
    form_purchase: '<strong>form_purchase (UI Button Click Required):</strong> High-impact form submission or checkout step; always requires an explicit UI button click.',
    media_access: '<strong>media_access:</strong> Required alongside mouse_keyboard before enabling webcam-based MediaPipe hand-gesture control.'
  };

  var permDetail = document.getElementById('perm-detail');
  var permBtns = Array.prototype.slice.call(document.querySelectorAll('[data-perm]'));
  permBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var key = btn.getAttribute('data-perm');
      if (permDetail && permInfo[key]) permDetail.innerHTML = permInfo[key];
      permBtns.forEach(function (b) {
        b.setAttribute('aria-pressed', String(b === btn));
      });
    });
  });

  // 9. Copy PowerShell setup commands
  var copyBtn = document.getElementById('copy-install');
  var codeEl = document.getElementById('install-code');
  if (copyBtn && codeEl) {
    copyBtn.addEventListener('click', function () {
      var text = codeEl.textContent || '';
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          copyBtn.textContent = 'Copied';
          setTimeout(function () { copyBtn.textContent = 'Copy Commands'; }, 1800);
        });
      }
    });
  }

  // 10. Reusable 3D Mechanical Keycap Interactive Physics (Tilt, Specular, Press & Arrow Nav)
  var keycaps = Array.prototype.slice.call(document.querySelectorAll('.keycap'));
  keycaps.forEach(function (kc) {
    if (!reduceMotion && !isCoarse) {
      kc.addEventListener('pointermove', function (e) {
        var r = kc.getBoundingClientRect();
        var px = (e.clientX - r.left) / Math.max(1, r.width);
        var py = (e.clientY - r.top) / Math.max(1, r.height);
        var rx = ((0.5 - py) * 10).toFixed(2) + 'deg';
        var ry = ((px - 0.5) * 12).toFixed(2) + 'deg';
        kc.style.setProperty('--rx', rx);
        kc.style.setProperty('--ry', ry);
        kc.style.setProperty('--kx', (px * 100).toFixed(1) + '%');
        kc.style.setProperty('--ky', (py * 100).toFixed(1) + '%');
      }, { passive: true });
      kc.addEventListener('pointerleave', function () {
        kc.style.setProperty('--rx', '0deg');
        kc.style.setProperty('--ry', '0deg');
      });
    }
    kc.addEventListener('click', function () {
      if (reduceMotion) return;
      kc.classList.add('is-pressed');
      setTimeout(function () { kc.classList.remove('is-pressed'); }, 150);
    });
  });

  document.querySelectorAll('.keycap-deck').forEach(function (deck) {
    var keys = Array.prototype.slice.call(deck.querySelectorAll('.keycap'));
    deck.addEventListener('keydown', function (e) {
      var idx = keys.indexOf(document.activeElement);
      if (idx < 0) return;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault();
        keys[(idx + 1) % keys.length].focus();
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault();
        keys[(idx - 1 + keys.length) % keys.length].focus();
      }
    });
  });

  // 11. Graceful fallback if Three.js CDN or WebGL is unavailable for #roadmap
  setTimeout(function () {
    if (!window.__novaSpiralReady) {
      var rmSection = document.getElementById('roadmap');
      var fb = document.getElementById('sr-fallback');
      if (rmSection && fb) {
        rmSection.classList.add('is-fallback');
        fb.hidden = false;
      }
    }
  }, 3500);

  // 12. Scroll choreography reveals
  var reveals = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window && !reduceMotion) {
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          obs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08 });
    reveals.forEach(function (el) { obs.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add('is-visible'); });
  }
})();
