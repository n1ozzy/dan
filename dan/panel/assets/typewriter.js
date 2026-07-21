(function () {
  if (window.__danTypewriterLoaded) return;
  window.__danTypewriterLoaded = true;

  var BASE = 'http://127.0.0.1:41741';
  var MIN_STEP = 12, MAX_STEP = 110;
  var EVT_POLL = 250, Q_POLL = 700, TICK = 40, META_TICK = 600, GAP_MS = 1200;
  var GLIDE_MS = 180;

  var perChar = 58 / 1.29;

  var afterId = 0;
  var queue = {};
  var prefix = '';
  var live = null;
  var turnId = null;
  var el = null, mine = null, endTimer = null;

  function bubbles() {
    var all = document.querySelectorAll('#turnList .chat-bubble.dan');
    var out = [];
    for (var i = 0; i < all.length; i++) {
      if (mine && mine.contains(all[i])) continue;
      out.push(all[i]);
    }
    return out;
  }

  function findBubble() {
    if (el && el.isConnected) return el;
    var real = bubbles();
    var last = real.length ? real[real.length - 1] : null;
    if (last && last.classList.contains('placeholder')) { el = last; mine = null; return el; }
    return makeBubble();
  }

  function makeBubble() {
    var list = document.getElementById('turnList');
    if (!list) return null;
    if (mine && mine.isConnected) { el = mine.querySelector('.chat-bubble'); return el; }
    var turn = document.createElement('div');
    turn.className = 'chat-turn';
    turn.setAttribute('data-dan-live', '1');
    var b = document.createElement('div');
    b.className = 'chat-bubble dan';
    var m = document.createElement('div');
    m.className = 'chat-meta';
    m.setAttribute('data-dan-meta', '1');
    turn.appendChild(b);
    turn.appendChild(m);
    list.appendChild(turn);
    list.scrollTop = list.scrollHeight;
    mine = turn; el = b;
    return el;
  }

  function paint(text) {
    var b = findBubble();
    if (!b) return;
    b.classList.remove('placeholder');
    if (!b.classList.contains('dan')) b.classList.add('dan');
    b.textContent = text;
    var list = document.getElementById('turnList');
    if (list) list.scrollTop = list.scrollHeight;
  }

  function dropMine() {
    if (mine && mine.isConnected && mine.parentNode) {
      try { mine.parentNode.removeChild(mine); } catch (e) {}
    }
    mine = null; el = null;
  }

  /* ---------- meta line: status (English, matches panel) + clock ---------- */

  var STATUS = [
    [/error|failed|cancel/i, 'error'],
    [/tool_(requested|started)|tool\./i, 'searching'],
    [/tool_(finished|completed)/i, 'found'],
    [/speak|voice|spoken/i, 'speaking'],
    [/brain_requested|thinking/i, 'thinking'],
    [/brain_responded|responded|delivered/i, 'responding'],
    [/listen|heard|transcri/i, 'listening'],
    [/queued|pending/i, 'waiting']
  ];

  function statusFor(raw) {
    for (var i = 0; i < STATUS.length; i++) {
      if (STATUS[i][0].test(raw)) return STATUS[i][1];
    }
    return '';
  }

  function clock(ms) {
    var d = new Date(ms);
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function rewriteMeta() {
    var metas = document.querySelectorAll('#turnList .chat-meta');
    for (var i = 0; i < metas.length; i++) {
      var m = metas[i];
      if (m.getAttribute('data-dan-meta') === '1') continue;
      var raw = m.getAttribute('data-dan-raw');
      if (raw === null) {
        raw = m.textContent || '';
        m.setAttribute('data-dan-raw', raw);
        m.setAttribute('data-dan-ts', String(Date.now()));
      }
      var st = statusFor(raw);
      var ts = Number(m.getAttribute('data-dan-ts')) || Date.now();
      var want = (st ? st + ' \u00b7 ' : '') + clock(ts);
      if (m.textContent !== want) m.textContent = want;
    }
    if (mine && mine.isConnected) {
      var own = mine.querySelector('.chat-meta');
      if (own) {
        var sEl = document.getElementById('stateLabel');
        var gEl = document.getElementById('activityStage');
        var sTxt = sEl ? (sEl.textContent || '').trim() : '';
        var gTxt = gEl ? (gEl.textContent || '').trim() : '';
        var parts = [];
        if (sTxt && sTxt !== '\u2026' && sTxt.toLowerCase() !== 'unknown') parts.push(sTxt);
        if (gTxt && gTxt !== sTxt && gTxt.toLowerCase().indexOf('connecting') !== 0) parts.push(gTxt);
        var label = parts.length ? parts.join(' \u00b7 ') : (live ? 'speaking' : 'done');
        var w = label + ' \u00b7 ' + clock(Date.now());
        if (own.textContent !== w) {
          own.textContent = w;
          own.classList.add('dan-pulse');
          window.setTimeout(function () { own.classList.remove('dan-pulse'); }, 250);
        }
      }
    }
  }

  /* ---------- collapse the duplicate top state/stage row into the bubble meta ---------- */

  function hideChrome() {
    var tb = document.querySelector('.chat-toolbar.single-dan-toolbar');
    if (tb && tb.style.display !== 'none') tb.style.display = 'none';
    var strip = document.getElementById('activityStrip');
    if (strip && strip.style.display !== 'none') strip.style.display = 'none';
  }

  var pulseStyleAdded = false;
  function ensurePulseStyle() {
    if (pulseStyleAdded) return;
    pulseStyleAdded = true;
    var sty = document.createElement('style');
    sty.textContent = '.dan-pulse{transition:opacity .25s ease;opacity:.55}';
    document.head.appendChild(sty);
  }

  /* ---------- typing ---------- */

  function joinText(a, b) {
    if (!a) return b;
    if (!b) return a;
    return a.replace(/\s+$/, '') + ' ' + b;
  }

  function startSentence(reqId) {
    var item = queue[reqId];
    if (!item || item.kind === 'filler' || !item.text) return;
    if (endTimer) { window.clearTimeout(endTimer); endTimer = null; }

    if (item.turn_id && item.turn_id !== turnId) {
      turnId = item.turn_id;
      prefix = '';
      el = null; mine = null;
    }
    if (live && live.id !== reqId) commit();

    live = {
      id: reqId,
      text: item.text,
      shown: 0,
      step: Math.max(MIN_STEP, Math.min(MAX_STEP, perChar)),
      at: Date.now(),
      closing: false
    };
    paint(prefix);
  }

  function calibrate() {
    if (!live || live.closing) return;
    var len = live.text.length;
    var actual = Date.now() - live.at;
    if (len < 25 || actual < 400) return;
    var sample = actual / len;
    if (sample < 15 || sample > 150) return;
    perChar = perChar * 0.6 + sample * 0.4;
  }

  function commit() {
    if (!live) return;
    prefix = joinText(prefix, live.text);
    live = null;
    paint(prefix);
  }

  function finishSentence(reqId) {
    if (!live || (reqId && live.id !== reqId)) return;
    calibrate();
    var rem = live.text.length - live.shown;
    if (rem <= 0) {
      commit();
    } else {
      live.closing = true;
      live.step = Math.max(5, GLIDE_MS / rem);
      live.at = Date.now() - live.shown * live.step;
    }
    if (endTimer) window.clearTimeout(endTimer);
    endTimer = window.setTimeout(endTurn, GAP_MS);
  }

  function endTurn() {
    endTimer = null;
    commit();
    var real = bubbles();
    for (var i = 0; i < real.length; i++) {
      if (prefix.length > 0 && (real[i].textContent || '').length >= prefix.length - 4) { dropMine(); break; }
    }
    prefix = ''; turnId = null; el = null;
  }

  window.setInterval(function () {
    if (!live) return;
    var due = Math.floor((Date.now() - live.at) / live.step);
    if (due > live.shown) {
      live.shown = Math.min(due, live.text.length);
      paint(joinText(prefix, live.text.slice(0, live.shown)));
    }
    if (live.closing && live.shown >= live.text.length) commit();
  }, TICK);

  window.setInterval(rewriteMeta, META_TICK);
  window.setInterval(hideChrome, META_TICK);

  /* ---------- daemon ---------- */

  function get(path, cb) {
    try {
      var x = new XMLHttpRequest();
      x.open('GET', BASE + path, true);
      x.timeout = 4000;
      x.onload = function () {
        if (x.status !== 200) return;
        try { cb(JSON.parse(x.responseText)); } catch (e) {}
      };
      x.send();
    } catch (e) {}
  }

  function pollQueue() {
    get('/voice/queue?limit=40', function (d) {
      var items = d && d.voice_queue;
      if (!items) return;
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (!it || !it.id) continue;
        queue[it.id] = { text: it.text_preview || '', kind: it.kind, turn_id: it.turn_id };
      }
      var keys = Object.keys(queue);
      if (keys.length > 200) { for (var j = 0; j < keys.length - 200; j++) delete queue[keys[j]]; }
    });
  }

  function pollEvents() {
    var q = afterId ? ('/events?after_id=' + afterId + '&limit=40')
                    : '/events?after_id=0&limit=1&latest=true';
    get(q, function (d) {
      var evs = (d && d.events) || [];
      if (!afterId) { afterId = d.latest_event_id || 0; return; }
      evs = evs.slice().sort(function (a, b) { return a.id - b.id; });
      for (var i = 0; i < evs.length; i++) {
        var e = evs[i];
        if (e.id > afterId) afterId = e.id;
        var rid = e.payload && e.payload.request_id;
        if (e.type === 'voice.speak.queued') pollQueue();
        else if (e.type === 'voice.speak.started') startSentence(rid);
        else if (e.type === 'voice.speak.finished') finishSentence(rid);
        else if (e.type === 'voice.speak.cancelled' || e.type === 'voice.cancelled') finishSentence(rid || (live && live.id));
      }
    });
  }

  window.setInterval(pollEvents, EVT_POLL);
  window.setInterval(pollQueue, Q_POLL);
  ensurePulseStyle();
  hideChrome();
  rewriteMeta();
  pollQueue();
  pollEvents();
  try { console.log('[dan-typewriter] v10 english-only + collapsed chrome'); } catch (e) {}
})();
