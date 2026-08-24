'use strict';

/**
 * Evita que libsignal vuelque SessionEntry (incluye claves privadas) en logs.
 * La dependencia usa console.info directamente y no respeta el logger pino
 * configurado para Baileys, por eso el filtro debe instalarse en console.
 */
function installSafeSignalLogging(targetConsole) {
  const originalInfo = targetConsole.info.bind(targetConsole);

  targetConsole.info = (...args) => {
    if (args[0] === 'Closing session:') {
      originalInfo('[wa-bot] Sesión criptográfica anterior cerrada.');
      return;
    }
    originalInfo(...args);
  };

  return () => {
    targetConsole.info = originalInfo;
  };
}

module.exports = { installSafeSignalLogging };
