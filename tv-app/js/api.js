/*
 * Client for the helper's HTTP API. Every request has a timeout. Authenticated requests are
 * signed: HMAC-SHA256(secret, METHOD \n PATH \n TIMESTAMP \n BODY).
 *
 * If the TV clock is off, the helper answers 401 with its own time; we remember the offset
 * and retry once, so a wrong TV clock does not break the app.
 */
(function (TW) {
  "use strict";

  function ApiError(kind, message, status) {
    this.name = "ApiError";
    this.kind = kind; // "network" | "timeout" | "auth" | "http" | "notfound"
    this.message = message;
    this.status = status || 0;
  }
  ApiError.prototype = Object.create(Error.prototype);
  TW.ApiError = ApiError;

  function Api(baseUrl, secret) {
    this.base = baseUrl.replace(/\/+$/, "");
    this.secret = secret;
    this.clockOffset = 0; // seconds to add to the TV clock to match the helper
  }

  Api.prototype.setServerTime = function (serverTime) {
    if (typeof serverTime !== "number") return;
    var offset = serverTime - Date.now() / 1000;
    // Only correct noticeable differences; small ones are fine (±60 s window).
    this.clockOffset = Math.abs(offset) > 5 ? offset : 0;
  };

  Api.prototype._fetch = function (method, path, headers, timeoutMs) {
    var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = null;
    var timeout = new Promise(function (_, reject) {
      timer = setTimeout(function () {
        if (ctrl) ctrl.abort();
        reject(new ApiError("timeout", "The helper did not answer in time."));
      }, timeoutMs);
    });
    var req = fetch(this.base + path, {
      method: method,
      headers: headers,
      cache: "no-store",
      signal: ctrl ? ctrl.signal : undefined
    }).then(
      function (resp) {
        return resp.text().then(function (text) {
          var data = null;
          try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
          return { status: resp.status, data: data };
        });
      },
      function (err) {
        throw new ApiError("network", "Cannot reach the helper (" + (err && err.message ? err.message : err) + ").");
      }
    );
    return Promise.race([req, timeout]).then(
      function (r) { clearTimeout(timer); return r; },
      function (e) { clearTimeout(timer); throw e; }
    );
  };

  Api.prototype.request = function (method, path, opts) {
    var self = this;
    opts = opts || {};
    var timeoutMs = opts.timeoutMs || 5000;

    function attempt(retried) {
      var headersP;
      if (opts.auth === false) {
        headersP = Promise.resolve({});
      } else {
        var ts = String(Math.floor(Date.now() / 1000 + self.clockOffset));
        headersP = TW.hmacHex(self.secret, method + "\n" + path + "\n" + ts + "\n").then(function (sig) {
          // X-Client is informational only (shows up in the helper's log).
          return { "X-Timestamp": ts, "X-Signature": sig, "X-Client": "tvwake-tv crypto=" + TW.cryptoImpl };
        });
      }
      return headersP.then(function (headers) {
        return self._fetch(method, path, headers, timeoutMs);
      }).then(function (r) {
        if (r.status >= 200 && r.status < 300) return r.data;
        var detail = r.data && r.data.detail ? r.data.detail : {};
        if (r.status === 401) {
          if (!retried && detail.server_time) {
            self.setServerTime(detail.server_time);
            return attempt(true);
          }
          throw new ApiError("auth", "The helper rejected this app (" + (detail.error || "unauthorized") +
            "). Rebuild and reinstall the TV app if SHARED_SECRET changed.", 401);
        }
        if (r.status === 404) throw new ApiError("notfound", "Not found.", 404);
        throw new ApiError("http", "The helper answered with an error (HTTP " + r.status + "): " +
          (detail.error || "unknown"), r.status);
      });
    }
    return attempt(false);
  };

  Api.prototype.health = function () {
    var self = this;
    return this.request("GET", "/health", { auth: false, timeoutMs: 4000 }).then(function (d) {
      if (!d || d.ok !== true) throw new ApiError("http", "The helper answered, but not like the agent.");
      self.setServerTime(d.server_time);
      return d;
    });
  };
  Api.prototype.wake = function () { return this.request("POST", "/wake"); };
  Api.prototype.job = function (id) { return this.request("GET", "/jobs/" + encodeURIComponent(id)); };
  Api.prototype.devmode = function () { return this.request("GET", "/devmode"); };

  TW.Api = Api;
})(window.TW = window.TW || {});
