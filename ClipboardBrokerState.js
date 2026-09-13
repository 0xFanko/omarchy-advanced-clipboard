function readiness(payload, hasPendingSave) {
  var history = payload && Array.isArray(payload.history) ? payload.history : []
  return {
    ready: true,
    shouldLoadHistory: !hasPendingSave,
    history: history,
    replayPendingSave: hasPendingSave,
    degraded: Boolean(payload && payload.degraded),
    message: payload && payload.message ? String(payload.message) : "",
    quarantine: payload && payload.quarantine ? String(payload.quarantine) : ""
  }
}

function acknowledges(pendingSave, payload) {
  return Boolean(pendingSave && payload
    && Number(payload.requestId) === Number(pendingSave.requestId))
}

if (typeof module !== "undefined") {
  module.exports = {
    readiness: readiness,
    acknowledges: acknowledges
  }
}
