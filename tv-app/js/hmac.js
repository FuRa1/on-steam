/*
 * HMAC-SHA256 hex signature. Prefers WebCrypto; falls back to the bundled implementation
 * (js/lib/sha256.js) if crypto.subtle is missing or refuses to work in this context.
 * TW.cryptoImpl tells which one was used ("webcrypto" or "js"); the footer shows it.
 */
(function (TW) {
  "use strict";

  var encoder = new TextEncoder();
  var keyPromise = null; // cached CryptoKey promise, or null if WebCrypto is unusable
  var useJs = false;
  TW.cryptoImpl = "?";

  function hex(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    return s;
  }

  function jsHmac(secret, message) {
    TW.cryptoImpl = "js";
    return hex(window.TWSha256.hmacSha256(encoder.encode(secret), encoder.encode(message)));
  }

  TW.hmacHex = function (secret, message) {
    if (useJs || !(window.crypto && window.crypto.subtle)) {
      useJs = true;
      return Promise.resolve(jsHmac(secret, message));
    }
    if (!keyPromise) {
      keyPromise = window.crypto.subtle.importKey(
        "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
      );
    }
    return keyPromise
      .then(function (key) {
        return window.crypto.subtle.sign("HMAC", key, encoder.encode(message));
      })
      .then(function (sig) {
        TW.cryptoImpl = "webcrypto";
        return hex(new Uint8Array(sig));
      })
      .catch(function (err) {
        TW.log("WebCrypto failed, using JS HMAC: " + err);
        useJs = true;
        return jsHmac(secret, message);
      });
  };
})(window.TW = window.TW || {});
