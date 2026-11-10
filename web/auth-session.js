/* Shared Cognito token refresh. A failed request is retried only after an explicit
   HTTP 401; transport failures never replay a planning POST. */
(function (root) {
  'use strict';
  function failure(code, message) { const e = new Error(message); e.code = code; return e; }
  function decode(token) {
    try { return JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))); }
    catch (_) { return {}; }
  }
  function create(config, store, transport = root.fetch.bind(root)) {
    let refreshing = null;
    function clearTokens() {
      ['id_token', 'access_token', 'refresh_token', 'expires_at'].forEach(store.del);
    }
    function acceptTokens(tokens) {
      const owner = decode(tokens.id_token).sub;
      const previous = store.get('auth_owner') || decode(store.get('id_token') || '').sub;
      if (owner && previous && owner !== previous) {
        ['session_id', 'transcript', 'pending_run'].forEach(store.del);
      }
      if (owner) store.set('auth_owner', owner);
      for (const key of ['id_token', 'access_token', 'refresh_token']) {
        if (tokens[key]) store.set(key, tokens[key]);
      }
      store.set('expires_at', String(Date.now() + Number(tokens.expires_in || 0) * 1000));
    }
    function expiresAt() {
      return Math.min(...['id_token', 'access_token'].map(key =>
        Number(decode(store.get(key) || '').exp || 0) * 1000), Number(store.get('expires_at') || 0));
    }
    async function ensureFresh(force = false) {
      if (!force && expiresAt() > Date.now() + 60000) return;
      if (refreshing) return refreshing;
      if (!store.get('refresh_token')) throw failure('SESSION_EXPIRED', 'Your sign-in needs renewal. Sign in again to check your saved request.');
      refreshing = (async () => {
        let response;
        try {
          response = await transport(config.COGNITO_DOMAIN + '/oauth2/token', {
            method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: new URLSearchParams({grant_type: 'refresh_token', client_id: config.COGNITO_CLIENT_ID,
              refresh_token: store.get('refresh_token')})
          });
        } catch (_) { throw failure('CONNECTION_LOST', 'Could not reach the sign-in service. Check your connection and try again.'); }
        const tokens = await response.json().catch(() => ({}));
        if (!response.ok) {
          if (tokens.error === 'invalid_grant') {
            clearTokens();
            throw failure('SESSION_EXPIRED', 'Your sign-in expired. Sign in again to check your saved request.');
          }
          throw failure('AUTH_UNAVAILABLE', 'The sign-in service could not renew your session. Please try again.');
        }
        if (!tokens.id_token || !tokens.access_token) throw failure('AUTH_UNAVAILABLE', 'The sign-in service returned an incomplete response.');
        acceptTokens(tokens);
      })();
      try { return await refreshing; } finally { refreshing = null; }
    }
    async function authorizedFetch(url, options = {}, tokenType = 'id_token') {
      await ensureFresh();
      const request = () => {
        const headers = new Headers(options.headers || {});
        headers.set('Authorization', (tokenType === 'access_token' ? 'Bearer ' : '') + store.get(tokenType));
        return transport(url, {...options, headers});
      };
      let response = await request();
      if (response.status === 401) {
        await ensureFresh(true);
        response = await request();
        if (response.status === 401) throw failure('SESSION_EXPIRED', 'Your sign-in was rejected. Sign in again to check your saved request.');
      }
      return response;
    }
    return {acceptTokens, ensureFresh, fetch: authorizedFetch, clearTokens, expiresAt};
  }
  root.TripAuth = {create, failure};
})(typeof window === 'undefined' ? globalThis : window);
