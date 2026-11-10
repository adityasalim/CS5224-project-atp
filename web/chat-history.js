/* Chat history drawer. Loaded after the main script in index.html:
   <script src="chat-history.js"></script>
   Lists the signed-in user's past chats (GET /chats) and reopens one (GET /chats/{sessionId}) by writing it into
   the page's own sessionStorage keys (session_id, transcript) and reloading, so the existing restore code shows it. */
(function () {
  'use strict';
  if (typeof CONFIG === 'undefined' || !CONFIG.API_URL) return;

  var base = CONFIG.API_URL.replace(/\/$/, '');
  function api(path) {
    return apiFetch(base + path).then(function (r) {
      if (!r.ok) throw new Error('Could not load history (' + r.status + ').');
      return r.json();
    });
  }

  function mk(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function fmtDate(iso) {
    var d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }

  var style = mk('style');
  style.textContent =
    '#chPanel{position:fixed;top:0;right:0;bottom:0;width:min(380px,100%);background:var(--surface,#fff);' +
    'border-left:1px solid var(--line,#ddd);z-index:50;display:flex;flex-direction:column;box-shadow:-8px 0 24px rgba(0,0,0,.15)}' +
    '#chPanel header{padding:12px 16px;display:flex;justify-content:space-between;align-items:center}' +
    '#chList{overflow-y:auto;padding:8px 12px;flex:1}' +
    '#chList button.item{display:block;width:100%;text-align:left;border-radius:12px;margin:0 0 8px;padding:10px 12px}' +
    '#chList .t{font-weight:600;font-size:14px}#chList .d{font-size:12px;color:var(--muted,#777)}' +
    '#chMsg{padding:12px 16px;font-size:14px;color:var(--muted,#777)}';
  document.head.appendChild(style);

  var panel = mk('aside');
  panel.id = 'chPanel';
  panel.className = 'hidden';
  var head = mk('header');
  head.append(mk('strong', null, 'Chat history'));
  var closeBtn = mk('button', null, 'Close');
  closeBtn.type = 'button';
  head.append(closeBtn);
  var msg = mk('div');
  msg.id = 'chMsg';
  var list = mk('div');
  list.id = 'chList';
  panel.append(head, msg, list);
  document.body.appendChild(panel);

  function close() { panel.classList.add('hidden'); }
  closeBtn.onclick = close;

  function toTranscript(messages, itineraries) {
    var out = [];
    messages.forEach(function (m) {
      if (m.role === 'user') out.push({ kind: 'me', data: m.text });
      else out.push({ kind: 'bot', data: m.text });
    });
    (itineraries || []).forEach(function (it) { out.push({kind: "itinerary", data: it}); });
    return out.slice(-60);
  }

  function pdfButton(it) {
    var button = mk('button', null, 'Download PDF'); button.type = 'button';
    button.onclick = function () {
      var popup = window.open('', '_blank');
      button.disabled = true; msg.textContent = 'Preparing PDF…';
      api('/trips/' + encodeURIComponent(it.tripId) + '/pdf' +
          (it.version ? '?version=' + encodeURIComponent(it.version) : '')).then(function (res) {
        var url = safeUrl(res.url);
        if (!url) throw new Error('No PDF download link is available for this trip.');
        if (popup) popup.location = url;
        else throw new Error('Allow pop-ups for this website to open the PDF.');
        msg.textContent = '';
      }).catch(function (e) {
        if (popup) popup.close(); msg.textContent = e.message;
      }).finally(function () { button.disabled = false; });
    };
    return button;
  }

  function preview(res) {
    list.replaceChildren();
    var back = mk('button', null, 'Back to history'); back.type = 'button'; back.onclick = load;
    list.append(back);
    msg.textContent = 'Viewing saved history. Close to return to your current chat.';
    (res.messages || []).forEach(function (message) {
      var text = mk('p', null, (message.role === 'user' ? 'You: ' : 'Planner: ') + message.text);
      text.style.whiteSpace = 'pre-wrap'; list.append(text);
    });
    (res.itineraries || []).forEach(function (it) {
      var card = mk('article'); card.append(mk('h3', null, it.title || 'Saved plan'));
      if (it.summary) card.append(mk('p', null, it.summary));
      if (it.hasPdf !== false) card.append(pdfButton(it));
      (it.days || []).forEach(function (day) {
        card.append(mk('h4', null, day.title));
        (day.stops || []).forEach(function (stop) {
          card.append(mk('p', null, [stop.time, stop.name, stop.note].filter(Boolean).join(' · ')));
        });
      });
      list.append(card);
    });
  }

  function openSession(id, btn) {
    btn.disabled = true; msg.textContent = 'Opening chat…';
    api('/chats/' + encodeURIComponent(id)).then(function (res) {
      // Viewing history must never cancel a stream or replace its pending request.
      if (busy || pendingRun()) { preview(res); return; }
      sessionStorage.setItem('session_id', id);
      sessionStorage.setItem('transcript', JSON.stringify(toTranscript(res.messages || [], res.itineraries || [])));
      location.reload();
    }).catch(function (e) { msg.textContent = e.message; })
      .finally(function () { btn.disabled = false; });
  }

  function load() {
    list.textContent = '';
    msg.textContent = 'Loading...';
    Promise.allSettled([api('/chats'), api('/trips')]).then(function (results) {
      if (results.every(r => r.status === 'rejected')) throw results[0].reason;
      var res = results[0].status === 'fulfilled' ? results[0].value : {};
      var trips = results[1].status === 'fulfilled' ? results[1].value.trips || [] : [];
      var sessions = res.sessions || [];
      msg.textContent = sessions.length || trips.length ? '' : 'No earlier chats yet.';
      if (results.some(r => r.status === 'rejected')) msg.textContent = 'Some history could not be loaded. Available saved entries are shown below.';
      if (res.memoryUnavailable) msg.textContent = 'Some older chat messages are temporarily unavailable. Saved requests and plans are shown below.';
      if (trips.length) list.append(mk('h3', null, 'Saved plans & PDFs'));
      trips.forEach(function (trip) {
        var row = mk('article');
        var b = mk('button', 'item', trip.title || trip.destination || 'Saved plan'); b.type = 'button';
        b.onclick = function () {
          b.disabled = true;
          api('/trips/' + encodeURIComponent(trip.tripId)).then(function (result) {
            if (!result.webItinerary) throw new Error('No saved itinerary is available for this trip.');
            preview({itineraries: [result.webItinerary]});
          }).catch(function (e) { msg.textContent = e.message; }).finally(function () { b.disabled = false; });
        };
        row.append(b, pdfButton(trip)); list.append(row);
      });
      if (sessions.length) list.append(mk('h3', null, 'Conversations'));
      sessions.forEach(function (session) {
        var b = mk('button', 'item'); b.type = 'button';
        b.append(mk('div', 't', session.title || 'Conversation'),
          mk('div', 'd', fmtDate(session.createdAt) + (session.run ? ' · ' + session.run.status : '')));
        b.onclick = function () { openSession(session.sessionId, b); }; list.append(b);
      });
    }).catch(function (e) { msg.textContent = e.message; });
  }

  var btn = mk('button', null, 'History');
  btn.id = 'historyBtn';
  btn.type = 'button';
  btn.title = 'Reopen an earlier chat';
  btn.onclick = function () { panel.classList.remove('hidden'); load(); };
  var anchor = document.getElementById('newTripBtn');
  if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(btn, anchor);
  else document.body.appendChild(btn);
})();
