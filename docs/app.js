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

    var waveEl = document.getElementById('voice-wave');
    var waveState = 'is-silent';
    if (audioState.textMode) {
      waveState = 'is-locked';
      simReadout.innerHTML = '<strong>Text Mode Locked:</strong> Wake-word detection and conversational microphone listening stay paused indefinitely until you manually switch back. Chat stays active, and Spoken Replies are ' + (audioState.tts ? 'ON (optional audio readback).' : 'OFF (quiet text-only chat).');
    } else if (audioState.mic && audioState.tts) {
      waveState = 'is-full';
      simReadout.innerHTML = '<strong>Full Voice Mode:</strong> Microphone listening and spoken replies are both active across Online (Gemini Live), Offline (Vosk + Windows SAPI), and Hybrid modes.';
    } else if (audioState.mic && !audioState.tts) {
      waveState = 'is-listen';
      simReadout.innerHTML = '<strong>Speak-to-Text Only:</strong> Nova listens to your voice commands and displays silent written replies in chat without speaking aloud.';
    } else if (!audioState.mic && audioState.tts) {
      waveState = 'is-speak';
      simReadout.innerHTML = '<strong>Type-to-Speak:</strong> Microphone is muted while you type, and Nova reads responses aloud.';
    } else {
      waveState = 'is-silent';
      simReadout.innerHTML = '<strong>Silent Chat:</strong> Both microphone listening and spoken replies are muted for quiet text interaction.';
    }
    if (waveEl) {
      waveEl.className = 'voice-wave ' + waveState;
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

  var capVisuals = {
    screen:
      '<div class="cap-viz cap-viz-screen" aria-hidden="true">' +
        '<svg viewBox="0 0 360 150" fill="none" xmlns="http://www.w3.org/2000/svg">' +
          '<rect x="24" y="16" width="312" height="118" rx="12" stroke="rgba(56,232,255,0.35)" stroke-width="1.2" fill="rgba(8,14,26,0.65)"/>' +
          '<rect x="40" y="34" width="118" height="32" rx="6" fill="rgba(255,255,255,0.05)" stroke="rgba(255,255,255,0.1)"/>' +
          '<rect x="40" y="76" width="118" height="42" rx="6" fill="rgba(255,255,255,0.04)" stroke="rgba(255,255,255,0.08)"/>' +
          '<rect class="viz-target-box" x="174" y="34" width="146" height="84" rx="8" fill="rgba(56,232,255,0.08)" stroke="#38e8ff" stroke-width="1.5" stroke-dasharray="5 4"/>' +
          '<line class="viz-scan-line" x1="176" y1="42" x2="318" y2="42" stroke="#38e8ff" stroke-width="2" stroke-opacity="0.85"/>' +
          '<circle class="viz-pulse-dot" cx="247" cy="76" r="14" stroke="#9b7bff" stroke-width="1.5"/>' +
          '<circle cx="247" cy="76" r="3.5" fill="#38e8ff"/>' +
          '<text x="184" y="50" fill="#9beeff" font-size="9" font-family="monospace">REGION · Ctrl+Shift+P</text>' +
          '<text x="46" y="54" fill="#9ca7be" font-size="8.5" font-family="monospace">screen_view: OK</text>' +
        '</svg>' +
      '</div>',
    automation:
      '<div class="cap-viz cap-viz-auto" aria-hidden="true">' +
        '<svg viewBox="0 0 360 150" fill="none" xmlns="http://www.w3.org/2000/svg">' +
          '<path class="viz-flow-path" d="M68 75 H150 M210 75 H292" stroke="#38e8ff" stroke-width="1.8" stroke-dasharray="6 5"/>' +
          '<rect x="20" y="47" width="84" height="56" rx="10" fill="rgba(56,232,255,0.1)" stroke="rgba(56,232,255,0.5)"/>' +
          '<text x="62" y="72" text-anchor="middle" fill="#e6faff" font-size="9.5" font-family="monospace">UIA / WEB</text>' +
          '<text x="62" y="88" text-anchor="middle" fill="#7fdfff" font-size="8" font-family="monospace">ACTION</text>' +
          '<rect x="138" y="42" width="84" height="66" rx="12" fill="rgba(155,123,255,0.14)" stroke="rgba(155,123,255,0.6)"/>' +
          '<text x="180" y="72" text-anchor="middle" fill="#efeaff" font-size="9.5" font-family="monospace">GATE</text>' +
          '<text x="180" y="88" text-anchor="middle" fill="#c7b6ff" font-size="8" font-family="monospace">FAIL-CLOSED</text>' +
          '<rect class="viz-verified-box" x="256" y="47" width="84" height="56" rx="10" fill="rgba(52,211,153,0.14)" stroke="#34d399"/>' +
          '<text x="298" y="72" text-anchor="middle" fill="#d1fae5" font-size="9.5" font-family="monospace">VERIFIED</text>' +
          '<text x="298" y="88" text-anchor="middle" fill="#34d399" font-size="8.5" font-family="monospace">ok: true ✓</text>' +
        '</svg>' +
      '</div>',
    media:
      '<div class="cap-viz cap-viz-media" aria-hidden="true">' +
        '<svg viewBox="0 0 360 150" fill="none" xmlns="http://www.w3.org/2000/svg">' +
          '<rect x="34" y="24" width="96" height="102" rx="10" fill="rgba(14,22,38,0.85)" stroke="rgba(56,232,255,0.45)"/>' +
          '<line x1="48" y1="46" x2="112" y2="46" stroke="#38e8ff" stroke-width="2" stroke-opacity="0.7"/>' +
          '<line x1="48" y1="62" x2="104" y2="62" stroke="#9ca7be" stroke-width="1.5" stroke-opacity="0.5"/>' +
          '<line x1="48" y1="76" x2="114" y2="76" stroke="#9ca7be" stroke-width="1.5" stroke-opacity="0.5"/>' +
          '<line x1="48" y1="90" x2="96" y2="90" stroke="#9b7bff" stroke-width="1.5" stroke-opacity="0.7"/>' +
          '<text x="82" y="114" text-anchor="middle" fill="#7eeaff" font-size="8.5" font-family="monospace">PDF · DOCX · CSV</text>' +
          '<rect x="156" y="28" width="174" height="94" rx="10" fill="rgba(12,18,32,0.82)" stroke="rgba(155,123,255,0.45)"/>' +
          '<rect x="170" y="44" width="42" height="36" rx="5" fill="rgba(56,232,255,0.14)" stroke="rgba(56,232,255,0.4)"/>' +
          '<rect x="222" y="44" width="42" height="36" rx="5" fill="rgba(155,123,255,0.16)" stroke="rgba(155,123,255,0.5)"/>' +
          '<rect x="274" y="44" width="42" height="36" rx="5" fill="rgba(52,211,153,0.14)" stroke="rgba(52,211,153,0.4)"/>' +
          '<line x1="170" y1="98" x2="316" y2="98" stroke="rgba(255,255,255,0.18)" stroke-width="2"/>' +
          '<circle class="viz-playhead" cx="215" cy="98" r="5" fill="#38e8ff"/>' +
          '<text x="243" y="115" text-anchor="middle" fill="#c7b6ff" font-size="8.5" font-family="monospace">OPENCV KEYFRAMES</text>' +
        '</svg>' +
      '</div>',
    knowledge:
      '<div class="cap-viz cap-viz-knowledge" aria-hidden="true">' +
        '<svg viewBox="0 0 360 150" fill="none" xmlns="http://www.w3.org/2000/svg">' +
          '<g stroke="rgba(56,232,255,0.32)" stroke-width="1.2">' +
            '<line x1="180" y1="75" x2="86" y2="40"/><line x1="180" y1="75" x2="72" y2="108"/>' +
            '<line x1="180" y1="75" x2="274" y2="42"/><line x1="180" y1="75" x2="288" y2="106"/>' +
            '<line x1="86" y1="40" x2="72" y2="108"/><line x1="274" y1="42" x2="288" y2="106"/>' +
          '</g>' +
          '<circle class="viz-pulse-dot" cx="180" cy="75" r="26" fill="rgba(56,232,255,0.12)" stroke="#38e8ff" stroke-width="1.6"/>' +
          '<text x="180" y="78" text-anchor="middle" fill="#e6faff" font-size="9" font-family="monospace">AST/IDX</text>' +
          '<circle cx="86" cy="40" r="8" fill="#0d1b2e" stroke="#34d399"/><circle cx="72" cy="108" r="8" fill="#0d1b2e" stroke="#9b7bff"/>' +
          '<circle cx="274" cy="42" r="8" fill="#0d1b2e" stroke="#38e8ff"/><circle cx="288" cy="106" r="8" fill="#0d1b2e" stroke="#34d399"/>' +
          '<text x="180" y="134" text-anchor="middle" fill="#89e2ff" font-size="8.5" font-family="monospace">OPT-IN LOCAL TF-IDF · SAFE PYTHON SANDBOX</text>' +
        '</svg>' +
      '</div>',
    workflows:
      '<div class="cap-viz cap-viz-workflows" aria-hidden="true">' +
        '<svg viewBox="0 0 360 150" fill="none" xmlns="http://www.w3.org/2000/svg">' +
          '<path class="viz-flow-path" d="M56 75 H304" stroke="#9b7bff" stroke-width="2" stroke-dasharray="6 5"/>' +
          '<circle cx="56" cy="75" r="16" fill="rgba(56,232,255,0.16)" stroke="#38e8ff" stroke-width="1.5"/>' +
          '<circle cx="138" cy="75" r="16" fill="rgba(79,140,255,0.18)" stroke="#4f8cff" stroke-width="1.5"/>' +
          '<circle cx="222" cy="75" r="16" fill="rgba(155,123,255,0.18)" stroke="#9b7bff" stroke-width="1.5"/>' +
          '<circle class="viz-pulse-dot" cx="304" cy="75" r="16" fill="rgba(52,211,153,0.18)" stroke="#34d399" stroke-width="1.5"/>' +
          '<text x="56" y="79" text-anchor="middle" fill="#fff" font-size="9.5" font-family="monospace">01</text>' +
          '<text x="138" y="79" text-anchor="middle" fill="#fff" font-size="9.5" font-family="monospace">02</text>' +
          '<text x="222" y="79" text-anchor="middle" fill="#fff" font-size="9.5" font-family="monospace">03</text>' +
          '<text x="304" y="79" text-anchor="middle" fill="#fff" font-size="9.5" font-family="monospace">OK</text>' +
          '<text x="180" y="124" text-anchor="middle" fill="#c7b6ff" font-size="8.5" font-family="monospace">ALLOW-LISTED SEMANTIC REPLAY · PER-STEP CONFIRM</text>' +
        '</svg>' +
      '</div>',
    gestures:
      '<div class="cap-viz cap-viz-gestures" aria-hidden="true">' +
        '<svg viewBox="0 0 360 150" fill="none" xmlns="http://www.w3.org/2000/svg">' +
          '<g stroke="rgba(155,123,255,0.65)" stroke-width="1.6" stroke-linecap="round">' +
            '<path d="M120 120 L102 88 L90 66"/><path d="M120 120 L114 78 L112 42"/>' +
            '<path d="M120 120 L128 74 L130 34"/><path d="M120 120 L142 80 L148 44"/>' +
            '<path d="M120 120 L154 90 L164 64"/>' +
          '</g>' +
          '<g fill="#38e8ff">' +
            '<circle cx="120" cy="120" r="3.5"/><circle cx="90" cy="66" r="3"/><circle cx="112" cy="42" r="3.5"/>' +
            '<circle cx="130" cy="34" r="3.5"/><circle cx="148" cy="44" r="3"/><circle cx="164" cy="64" r="3"/>' +
          '</g>' +
          '<path class="viz-flow-path" d="M168 62 Q212 46 248 70" stroke="#38e8ff" stroke-width="1.4" stroke-dasharray="4 4"/>' +
          '<circle class="viz-pulse-dot" cx="268" cy="76" r="18" stroke="#38e8ff" stroke-width="1.4" fill="rgba(56,232,255,0.1)"/>' +
          '<polygon points="264,68 264,86 270,81 276,88 279,86 273,79 279,78" fill="#ffffff"/>' +
          '<text x="268" y="118" text-anchor="middle" fill="#9beeff" font-size="8.5" font-family="monospace">MEDIAPIPE 21-PT</text>' +
        '</svg>' +
      '</div>'
  };

  var capTitle = document.getElementById('cap-title');
  var capDesc = document.getElementById('cap-desc');
  var capPills = document.getElementById('cap-pills');
  var capRef = document.getElementById('cap-ref');
  var capVisual = document.getElementById('cap-visual');
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
    if (capVisual && capVisuals[key]) {
      capVisual.innerHTML = capVisuals[key];
    }
    capBtns.forEach(function (b) {
      b.setAttribute('aria-selected', String(b.getAttribute('data-cap') === key));
    });
  }
  capBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      selectCap(btn.getAttribute('data-cap'));
    });
  });
  selectCap('screen');

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
