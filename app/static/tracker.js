/* Facility report · anonymous event tracker
 *
 * Buffers interaction events and flushes them in one batch, so a wall-mounted
 * tablet does not fire a request per field. Only names on the server allowlist
 * survive; nothing here sends form content, email addresses or free text.
 *
 * Usage:  frTrack("report_form_started")
 *         frTrack("report_submit_error", {reason: "http_500"})
 *         frTrackOnce("report_ai_shown", {group: "HVAC"})
 */
(function () {
  "use strict";

  var ENDPOINT = "/api/events";
  var BATCH = 10;
  var CAP = 40;
  var queue = [];
  var seen = {};
  var timer = null;

  function pageName() {
    var path = (location.pathname || "/").replace(/\/+$/, "");
    if (path === "" || path === "/") return "index";
    if (path === "/track") return "track";
    if (path === "/officer") return "officer";
    if (path === "/dashboard") return "dashboard";
    return path.slice(1, 25).replace(/[^a-zA-Z0-9_]/g, "_").toLowerCase() || "unknown";
  }

  function codeFromUrl() {
    try {
      return new URLSearchParams(location.search).get("code") || "";
    } catch (e) {
      return "";
    }
  }

  function flush(useBeacon) {
    if (!queue.length) return;
    var batch = queue.splice(0, BATCH);
    var body = JSON.stringify({ events: batch });
    if (useBeacon && navigator.sendBeacon) {
      try {
        if (navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }))) return;
      } catch (e) { /* fall through to fetch */ }
    }
    try {
      fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        keepalive: true
      }).catch(function () { /* analytics must never surface an error */ });
    } catch (e) { /* offline: drop the batch, keep the page working */ }
  }

  function push(name, props) {
    if (!name) return;
    var bag = {};
    var code = codeFromUrl();
    if (props) {
      for (var key in props) {
        if (Object.prototype.hasOwnProperty.call(props, key) && key !== "ticket_code") bag[key] = props[key];
      }
      if (props.ticket_code) code = props.ticket_code;
    }
    if (queue.length >= CAP) queue = queue.slice(-CAP);
    queue.push({ event: name, page: pageName(), ticket_code: code, props: bag });
    if (queue.length >= BATCH) {
      flush(false);
      return;
    }
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { flush(false); }, 1500);
  }

  window.frTrack = function (name, props) {
    try { push(name, props); } catch (e) { /* never throw into page code */ }
  };

  window.frTrackOnce = function (name, props) {
    try {
      if (seen[name]) return;
      seen[name] = true;
      push(name, props);
    } catch (e) { /* never throw into page code */ }
  };

  try { window.frTrack("page_view"); } catch (e) { /* noop */ }

  window.addEventListener("pagehide", function () { flush(true); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") flush(true);
  });
})();
