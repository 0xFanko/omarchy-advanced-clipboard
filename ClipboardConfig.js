var defaultDeleteEntryShortcut = "Delete"
var defaultClearHistoryShortcut = "Shift+Delete"
var maxExcludedApplications = 100
var maxShortcutLength = 64

function defaultConfig() {
  return {
    shortcuts: {
      deleteEntry: defaultDeleteEntryShortcut,
      clearHistory: defaultClearHistoryShortcut
    },
    excludedApplications: [],
    valid: true
  }
}

function normalizedShortcut(value, fallback) {
  if (typeof value !== "string") return fallback
  var shortcut = value.trim()
  if (!shortcut || shortcut.length > maxShortcutLength) return fallback
  return shortcut
}

function normalizedApplications(value) {
  if (!Array.isArray(value)) return []

  var applications = []
  var seen = {}
  for (var i = 0; i < value.length && applications.length < maxExcludedApplications; i++) {
    if (typeof value[i] !== "string") continue
    var application = value[i].trim()
    var key = application.toLowerCase()
    if (!application || seen[key]) continue
    seen[key] = true
    applications.push(application)
  }
  return applications
}

function parseConfig(raw) {
  var parsed
  try {
    parsed = JSON.parse(String(raw || "{}"))
  } catch (e) {
    var malformed = defaultConfig()
    malformed.valid = false
    return malformed
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    var invalid = defaultConfig()
    invalid.valid = false
    return invalid
  }

  var shortcuts = parsed.shortcuts && typeof parsed.shortcuts === "object" && !Array.isArray(parsed.shortcuts)
    ? parsed.shortcuts : {}
  var config = defaultConfig()
  config.shortcuts.deleteEntry = normalizedShortcut(shortcuts.deleteEntry, defaultDeleteEntryShortcut)
  config.shortcuts.clearHistory = normalizedShortcut(shortcuts.clearHistory, defaultClearHistoryShortcut)
  if (config.shortcuts.clearHistory.toLowerCase() === config.shortcuts.deleteEntry.toLowerCase())
    config.shortcuts.clearHistory = defaultClearHistoryShortcut
  config.excludedApplications = normalizedApplications(parsed.excludedApplications)
  return config
}

function isApplicationExcluded(config, sourceId, sourceApp) {
  var values = config && Array.isArray(config.excludedApplications) ? config.excludedApplications : []
  var id = String(sourceId || "").trim().toLowerCase()
  var app = String(sourceApp || "").trim().toLowerCase()

  for (var i = 0; i < values.length; i++) {
    var candidate = String(values[i] || "").trim().toLowerCase()
    if (candidate && (candidate === id || candidate === app)) return true
  }
  return false
}

if (typeof module !== "undefined") {
  module.exports = {
    defaultConfig: defaultConfig,
    parseConfig: parseConfig,
    isApplicationExcluded: isApplicationExcluded
  }
}
