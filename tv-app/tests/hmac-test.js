// Checks the bundled SHA-256/HMAC against Node's crypto and known vectors.
// Run: node tv-app/tests/hmac-test.js
"use strict";
const assert = require("assert");
const crypto = require("crypto");
const path = require("path");
const { sha256, hmacSha256 } = require(path.join(__dirname, "..", "js", "lib", "sha256.js"));

const enc = (s) => new Uint8Array(Buffer.from(s, "utf8"));
const hex = (u8) => Buffer.from(u8).toString("hex");

// FIPS 180-2 vectors
assert.strictEqual(hex(sha256(enc(""))), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
assert.strictEqual(hex(sha256(enc("abc"))), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");

// Many lengths around the 64-byte block / padding boundaries, plus random keys.
for (let n = 0; n < 300; n++) {
  const msg = crypto.randomBytes(n);
  assert.strictEqual(hex(sha256(new Uint8Array(msg))), crypto.createHash("sha256").update(msg).digest("hex"), `sha256 len ${n}`);
  const key = crypto.randomBytes(n % 130); // covers keys shorter and longer than the block
  assert.strictEqual(
    hex(hmacSha256(new Uint8Array(key), new Uint8Array(msg))),
    crypto.createHmac("sha256", key).update(msg).digest("hex"),
    `hmac len ${n}`
  );
}

// Same message format as the agent (agent/tvwake/auth.py).
const secret = "test-secret-0123456789abcdef0123456789";
const message = "POST\n/wake\n1700000000\n";
assert.strictEqual(hex(hmacSha256(enc(secret), enc(message))), crypto.createHmac("sha256", secret).update(message).digest("hex"));

console.log("hmac-test: all OK");
