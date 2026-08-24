'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const { installSafeSignalLogging } = require('./safe-signal-logging');

test('oculta el SessionEntry sensible de libsignal', () => {
  const calls = [];
  const fakeConsole = { info: (...args) => calls.push(args) };
  installSafeSignalLogging(fakeConsole);

  fakeConsole.info('Closing session:', {
    currentRatchet: { ephemeralKeyPair: { privKey: 'SECRETO' } },
  });

  assert.deepEqual(calls, [[
    '[wa-bot] Sesión criptográfica anterior cerrada.',
  ]]);
  assert.equal(JSON.stringify(calls).includes('SECRETO'), false);
});

test('conserva los demás mensajes informativos', () => {
  const calls = [];
  const fakeConsole = { info: (...args) => calls.push(args) };
  installSafeSignalLogging(fakeConsole);

  fakeConsole.info('mensaje normal', { ok: true });

  assert.deepEqual(calls, [['mensaje normal', { ok: true }]]);
});
