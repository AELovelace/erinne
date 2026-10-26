#!/usr/bin/env node
/* Mnemerynne relay for Claude Desktop.

   Claude Desktop talks MCP over stdio (one JSON-RPC message per line); Mnemerynne
   serves MCP over HTTPS at <MNEMERYNNE_URL>/mcp (stateless, JSON responses). This
   forwards each message with the user's token and writes the reply back.
   No dependencies: Node's own https module, so Claude Desktop's built-in
   Node runs it as is. A ca.pem next to this file (or MNEMERYNNE_CA_FILE) is
   trusted in addition to the system's CAs, for Mnemerynne's internal certificate.
   Logs go to stderr, which Claude Desktop keeps in its MCP log files. */
"use strict";

const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");
const tls = require("tls");

const VERSION = "0.6.0";   // keep in step with manifest.json
const base = (process.env.MNEMERYNNE_URL || "").trim().replace(/\/+$/, "");
const token = (process.env.MNEMERYNNE_TOKEN || "").trim();

function log(message) {
  process.stderr.write("[mnemerynne] " + message + "\n");
}

function loadCa() {
  const file = process.env.MNEMERYNNE_CA_FILE || path.join(__dirname, "..", "ca.pem");
  try {
    const extra = fs.readFileSync(file, "utf8");
    log("trusting the extra CA certificate in " + file);
    return tls.rootCertificates.concat([extra]);
  } catch (e) {
    return undefined; // system CAs only
  }
}

const endpoint = base ? new URL(base + "/mcp") : null;
const agentOptions = { keepAlive: true, ca: loadCa() };
const agent = endpoint && endpoint.protocol === "http:" ? new http.Agent(agentOptions) : new https.Agent(agentOptions);
let protocolVersion = null;

function write(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

function fail(request, code, message) {
  // Notifications (no id) get no reply, even on failure.
  if (request && request.id !== undefined && request.id !== null) {
    write({ jsonrpc: "2.0", id: request.id, error: { code: code, message: message } });
  }
}

function post(body) {
  return new Promise(function (resolve, reject) {
    const headers = {
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
      "Authorization": "Bearer " + token,
      "User-Agent": "mnemerynne-claude-desktop/" + VERSION,
      "Content-Length": Buffer.byteLength(body),
    };
    if (protocolVersion) headers["MCP-Protocol-Version"] = protocolVersion;
    const lib = endpoint.protocol === "http:" ? http : https;
    const req = lib.request(endpoint, { method: "POST", headers: headers, agent: agent, timeout: 120000 }, function (res) {
      const chunks = [];
      res.on("data", function (c) { chunks.push(c); });
      res.on("end", function () {
        resolve({ status: res.statusCode, type: res.headers["content-type"] || "", text: Buffer.concat(chunks).toString("utf8") });
      });
    });
    req.on("timeout", function () { req.destroy(new Error("Mnemerynne took too long to answer")); });
    req.on("error", reject);
    req.end(body);
  });
}

/* JSON-RPC messages out of a response body: plain JSON, or SSE "data:" lines. */
function messagesIn(response) {
  if (!response.text.trim()) return [];
  if (response.type.indexOf("text/event-stream") !== -1) {
    return response.text.split(/\r?\n/)
      .filter(function (line) { return line.indexOf("data:") === 0; })
      .map(function (line) { return JSON.parse(line.slice(5)); });
  }
  const parsed = JSON.parse(response.text);
  return Array.isArray(parsed) ? parsed : [parsed];
}

async function relay(line) {
  let request;
  try {
    request = JSON.parse(line);
  } catch (e) {
    log("ignoring a line that isn't JSON");
    return;
  }
  if (!endpoint || !token) {
    fail(request, -32002, "The Mnemerynne extension isn't set up: open Claude's extension settings and enter your Mnemerynne token.");
    return;
  }
  let response;
  try {
    response = await post(line);
  } catch (e) {
    log("request failed: " + e.message);
    fail(request, -32003, "Couldn't reach Mnemerynne at " + base + " (" + e.message + "). Are you on the office network or Tailscale?");
    return;
  }
  if (response.status === 401) {
    fail(request, -32001, "Mnemerynne didn't accept the token: it may have expired or been revoked. Create a new one under Claude in Mnemerynne.");
    return;
  }
  if (response.status === 429) {
    fail(request, -32004, "Too many Mnemerynne searches in the last minute; wait a moment and try again.");
    return;
  }
  if (response.status >= 400) {
    log("HTTP " + response.status + ": " + response.text.slice(0, 300));
    fail(request, -32005, "Mnemerynne answered with an error (HTTP " + response.status + ").");
    return;
  }
  try {
    messagesIn(response).forEach(function (message) {
      if (request.method === "initialize" && message.result && message.result.protocolVersion) {
        protocolVersion = message.result.protocolVersion;
      }
      write(message);
    });
  } catch (e) {
    log("unreadable reply: " + e.message);
    fail(request, -32005, "Mnemerynne's reply couldn't be read.");
  }
}

let buffered = "";
let pending = 0;
let ended = false;

function finish() {
  if (ended && pending === 0) agent.destroy();
}

process.stdin.setEncoding("utf8");
process.stdin.on("data", function (chunk) {
  buffered += chunk;
  let newline;
  while ((newline = buffered.indexOf("\n")) !== -1) {
    const line = buffered.slice(0, newline).trim();
    buffered = buffered.slice(newline + 1);
    if (line) {
      pending += 1;
      relay(line).finally(function () { pending -= 1; finish(); });
    }
  }
});
// Claude Desktop closing stdin: answer what's in flight, then stop.
process.stdin.on("end", function () { ended = true; finish(); });

log("relaying to " + (endpoint ? endpoint.href : "(no MNEMERYNNE_URL set)"));
