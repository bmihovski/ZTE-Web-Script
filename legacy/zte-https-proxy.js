#!/usr/bin/env node
/*
 * zte-https-proxy.js - local HTTP -> HTTPS bridge for old ZTE CPE firmware.
 *
 * Why: the MC888 serves only HTTPS with a legacy stack (SHA-1 handshake
 * signatures, unsafe legacy renegotiation, SECLEVEL 0). Python's urllib and
 * WKWebView reject it, so the Mac app shows a blank page. This proxy terminates
 * that legacy TLS in Node and serves plain HTTP on 127.0.0.1, so the app (and
 * any browser) can use http://127.0.0.1:<port>.
 *
 *   node zte-https-proxy.js --target https://192.168.6.5 --port 8080
 *
 * Then put http://127.0.0.1:8080 in the Router Helper app.
 *
 * It forwards methods, bodies, cookies and headers; rewrites Host/Origin/Referer
 * to the device origin (the device's CSRF/session checks care) and rewrites
 * Location back to the proxy. No dependencies.
 */
'use strict';

const http = require('node:http');
const https = require('node:https');
const tls = require('node:tls');
const { constants } = require('node:crypto');

const argv = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : fallback;
};

const TARGET = (opt('target', 'https://192.168.6.5')).replace(/\/+$/, '');
const PORT = Number(opt('port', '8080'));
const target = new URL(TARGET);

const LEGACY =
  constants.SSL_OP_LEGACY_SERVER_CONNECT | constants.SSL_OP_ALLOW_UNSAFE_LEGACY_RENEGOTIATION;
const context = tls.createSecureContext({
  ciphers: 'DEFAULT:@SECLEVEL=0',
  sigalgs:
    'RSA+SHA1:ECDSA+SHA1:RSA+SHA256:RSA+SHA384:RSA+SHA512:' +
    'ECDSA+SHA256:ECDSA+SHA384:ECDSA+SHA512',
  secureOptions: LEGACY,
});
const agent = new https.Agent({
  keepAlive: true,
  rejectUnauthorized: false,
  minVersion: 'TLSv1',
  maxVersion: 'TLSv1.2',
  secureContext: context,
  secureOptions: LEGACY,
});

let ORIGIN = `http://127.0.0.1:${PORT}`;

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    const body = Buffer.concat(chunks);
    const headers = { ...req.headers };
    delete headers.connection;
    headers.host = target.host;
    if (body.length) headers['content-length'] = String(body.length);
    else delete headers['content-length'];

    // The device validates the request origin; the browser's origin is the proxy.
    if (headers.origin) headers.origin = TARGET;
    if (headers.referer) {
      try {
        headers.referer = TARGET + new URL(headers.referer).pathname;
      } catch {
        headers.referer = `${TARGET}/index.html`;
      }
    }

    const upstream = https.request(
      {
        hostname: target.hostname,
        port: target.port || 443,
        path: req.url,
        method: req.method,
        headers,
        agent,
      },
      (up) => {
        const out = [];
        up.on('data', (c) => out.push(c));
        up.on('end', () => {
          const buf = Buffer.concat(out);
          const responseHeaders = { ...up.headers };
          delete responseHeaders['transfer-encoding'];
          delete responseHeaders['content-length'];
          responseHeaders['content-length'] = String(buf.length);
          if (responseHeaders.location) {
            responseHeaders.location = String(responseHeaders.location).replace(TARGET, ORIGIN);
          }
          // The browser is on http://127.0.0.1: cookies must have no Domain and no Secure.
          if (responseHeaders['set-cookie']) {
            responseHeaders['set-cookie'] = responseHeaders['set-cookie'].map((cookie) =>
              cookie.replace(/;\s*domain=[^;]*/gi, '').replace(/;\s*secure/gi, ''),
            );
          }
          res.writeHead(up.statusCode || 502, responseHeaders);
          res.end(buf);
        });
      },
    );
    upstream.on('error', (error) => {
      res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' });
      res.end(`ZTE proxy error: ${error.message}\n`);
    });
    upstream.end(body);
  });
});

server.listen(PORT, '127.0.0.1', () => {
  const actual = server.address().port;
  ORIGIN = `http://127.0.0.1:${actual}`;
  console.log(`ZTE HTTPS proxy: ${TARGET} -> ${ORIGIN}`);
  // Started by the Mac app with a pipe as stdin: exit when the app goes away.
  if (!process.stdin.isTTY) {
    process.stdin.resume();
    process.stdin.on('end', () => {
      server.close();
      process.exit(0);
    });
  }
});
