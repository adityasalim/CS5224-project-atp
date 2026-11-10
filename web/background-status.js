/* Poll saved results; never replay a planning POST after losing a connection. */
(function (root) {
  'use strict';
  function create(options) {
    let inflight = null, stopped = null;
    function check() {
      if (inflight) return inflight;
      const pending = options.pending();
      if (!pending || !pending.background || stopped === pending.requestId || !options.visible()) return Promise.resolve();
      inflight = (async () => {
        try {
          const result = await options.read(pending);
          const current = options.pending();
          if (!current || current.requestId !== pending.requestId || current.sessionId !== pending.sessionId) return;
          if (!result.run) throw new Error('No saved request status is available yet.');
          options.update(result, current);
          if (["failed", "interrupted"].includes(result.run.status)) stopped = pending.requestId;
        } catch (error) {
          const current = options.pending();
          if (current && current.requestId === pending.requestId && current.sessionId === pending.sessionId) options.error(error);
        }
      })().finally(() => { inflight = null; });
      return inflight;
    }
    return {check};
  }
  root.TripJobs = {create};
})(typeof window === 'undefined' ? globalThis : window);
