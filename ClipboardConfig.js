var maxShortcutLength = 64
var maxHistoryRetentionDays = 36500

function defaultShortcuts() {
  return {
    close: "Escape",
    previousEntry: "Up",
    nextEntry: "Down",
    previousPage: "PgUp",
    nextPage: "PgDown",
    firstEntry: "Home",
    lastEntry: "End",
    pasteEntry: "Return",
    copyEntry: "Shift+Return",
    openEntry: "Alt+Return",
    editEntry: "Ctrl+E",
    saveEdit: "Ctrl+S",
    deleteEntry: "Delete",
    clearHistory: "Shift+Delete"
  }
}

function defaultConfig() {
  return {
    shortcuts: defaultShortcuts(),
    historyRetentionDays: 0,
    valid: true
  }
}

function normalizedHistoryRetentionDays(value) {
  if (value === undefined || value === null) return { value: 0, valid: true }
  if (typeof value !== "number" || !isFinite(value) || Math.floor(value) !== value || value < 0)
    return { value: 0, valid: false }
  return { value: Math.min(maxHistoryRetentionDays, value), valid: value <= maxHistoryRetentionDays }
}

function shortcutsAreUnique(shortcuts) {
  var seen = {}
  for (var key in shortcuts) {
    var sequence = String(shortcuts[key] || "").trim().toLowerCase()
    if (seen[sequence]) return false
    seen[sequence] = true
  }
  return true
}

function normalizedShortcut(value, fallback) {
  if (typeof value !== "string") return fallback
  var shortcut = value.trim()
  if (!shortcut || shortcut.length > maxShortcutLength) return fallback
  return shortcut
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
  var defaults = defaultShortcuts()
  for (var key in defaults)
    config.shortcuts[key] = normalizedShortcut(shortcuts[key], defaults[key])
  if (!shortcutsAreUnique(config.shortcuts)) {
    config.shortcuts = defaults
    config.valid = false
  }
  var retention = normalizedHistoryRetentionDays(parsed.historyRetentionDays)
  config.historyRetentionDays = retention.value
  if (!retention.valid) config.valid = false
  return config
}

if (typeof module !== "undefined") {
  module.exports = {
    defaultConfig: defaultConfig,
    parseConfig: parseConfig
  }
}
