/*
 * Luna service calls (webOS system services). Uses LG's webOSTV.js (webOS.service.request)
 * when it is bundled, otherwise the PalmServiceBridge that webOS gives every web app.
 * Every call has a timeout so the UI can never hang on it.
 */
(function (TW) {
  "use strict";

  var bridges = []; // keep PalmServiceBridge objects alive until they answer

  function call(uri, method, params, timeoutMs) {
    return new Promise(function (resolve, reject) {
      var done = false;
      var timer = setTimeout(function () {
        finish(new Error("No answer from " + uri + "/" + method));
      }, timeoutMs || 8000);

      function finish(err, res) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        if (err) reject(err); else resolve(res);
      }
      function onResult(res) {
        if (res && res.returnValue === false) {
          finish(new Error(res.errorText || ("error " + res.errorCode)));
        } else {
          finish(null, res);
        }
      }

      try {
        if (window.webOS && window.webOS.service && window.webOS.service.request) {
          window.webOS.service.request(uri, {
            method: method,
            parameters: params || {},
            onSuccess: onResult,
            onFailure: function (e) { onResult(e && e.returnValue === undefined ? { returnValue: false, errorText: e.errorText } : e); }
          });
        } else if (typeof window.PalmServiceBridge !== "undefined") {
          var bridge = new window.PalmServiceBridge();
          bridges.push(bridge);
          bridge.onservicecallback = function (msg) {
            bridges.splice(bridges.indexOf(bridge), 1);
            var res;
            try { res = JSON.parse(msg); } catch (e) { res = { returnValue: false, errorText: "bad reply" }; }
            onResult(res);
          };
          bridge.call(uri.replace(/\/+$/, "") + "/" + method, JSON.stringify(params || {}));
        } else {
          finish(new Error("Not running on a webOS TV (no Luna bridge)."));
        }
      } catch (e) {
        finish(e);
      }
    });
  }

  TW.luna = call;

  TW.launchApp = function (appId, params) {
    var p = { id: appId };
    if (params && Object.keys(params).length) p.params = params;
    return call("luna://com.webos.applicationManager", "launch", p, 10000);
  };

  TW.exitApp = function () {
    try {
      window.close();
    } catch (e) {
      TW.log("window.close failed: " + e);
    }
  };
})(window.TW = window.TW || {});
