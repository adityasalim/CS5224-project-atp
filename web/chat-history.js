/* Chat history drawer. Loaded after the main script in index.html:
   <script src="chat-history.js"></script>
   Lists the signed-in user's past chats (GET /chats) and reopens one (GET /chats/{sessionId}) by writing it into
   the page's own sessionStorage keys (session_id, transcript) and reloading, so the existing restore code shows it. */
(function () {
  'use strict';
  if (typeof CONFIG === 'undefined' || !CONFIG.API_URL) return;

  var base = CONFIG.API_URL.replace(/\/$/, '');
  function token() { try { return sessionStorage.getItem('id_token'); } catch (e) { return null; } }
  function api(path) {
    var t = token();
    if (!t) return Promise.reject(new Error('Please sign in to see your history.'));
    return fetch(base + path, { headers: { Authorization: t } }).then(function (r) {
      if (r.status === 401) throw new Error('Your sign-in expired. Sign in again.');
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

  function toTranscript(messages) {
    var out = [];
    messages.forEach(function (m) {
      if (m.role === 'user') out.push({ kind: 'me', data: m.text });
      else out.push({ kind: 'bot', data: m.text });
    });
    return out.slice(-60);
  }

  function openSession(id, btn) {
    btn.disabled = true;
    msg.textContent = 'Opening chat...';
    api('/chats/' + encodeURIComponent(id)).then(function (res) {
      sessionStorage.setItem('session_id', id);
      sessionStorage.setItem('transcript', JSON.stringify(toTranscript(res.messages || [])));
      location.reload();
    }).catch(function (e) {
      btn.disabled = false;
      msg.textContent = e.message;
    });
  }

  function load() {
    list.textContent = '';
    msg.textContent = 'Loading...';
    api('/chats').then(function (res) {
      var sessions = res.sessions || [];
      msg.textContent = sessions.length ? '' : 'No earlier chats yet.';
      sessions.forEach(function (s) {
        var b = mk('button', 'item');
        b.type = 'button';
        b.append(mk('div', 't', s.title || 'Conversation'), mk('div', 'd', fmtDate(s.createdAt)));
        b.onclick = function () { openSession(s.sessionId, b); };
        list.append(b);
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
