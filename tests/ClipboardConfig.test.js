const assert = require("node:assert/strict")
const ClipboardConfig = require("../ClipboardConfig.js")

const defaults = ClipboardConfig.parseConfig("")
assert.equal(defaults.valid, true)
assert.equal(defaults.shortcuts.close, "Escape")
assert.equal(defaults.shortcuts.previousEntry, "Up")
assert.equal(defaults.shortcuts.nextEntry, "Down")
assert.equal(defaults.shortcuts.previousPage, "PgUp")
assert.equal(defaults.shortcuts.nextPage, "PgDown")
assert.equal(defaults.shortcuts.firstEntry, "Home")
assert.equal(defaults.shortcuts.lastEntry, "End")
assert.equal(defaults.shortcuts.pasteEntry, "Return")
assert.equal(defaults.shortcuts.copyEntry, "Shift+Return")
assert.equal(defaults.shortcuts.openEntry, "Alt+Return")
assert.equal(defaults.shortcuts.editEntry, "Ctrl+E")
assert.equal(defaults.shortcuts.saveEdit, "Ctrl+S")
assert.equal(defaults.shortcuts.deleteEntry, "Delete")
assert.equal(defaults.shortcuts.clearHistory, "Shift+Delete")
assert.equal(defaults.historyRetentionDays, 0)

const custom = ClipboardConfig.parseConfig(JSON.stringify({
  shortcuts: {
    close: "Ctrl+Q",
    previousEntry: "Ctrl+K",
    nextEntry: "Ctrl+J",
    previousPage: "Ctrl+PgUp",
    nextPage: "Ctrl+PgDown",
    firstEntry: "Ctrl+Home",
    lastEntry: "Ctrl+End",
    pasteEntry: "Ctrl+P",
    copyEntry: "Ctrl+C",
    openEntry: "Ctrl+O",
    editEntry: "Ctrl+I",
    saveEdit: "Ctrl+Shift+S",
    deleteEntry: "Ctrl+X",
    clearHistory: "Ctrl+Shift+X"
  },
  historyRetentionDays: 30
}))
assert.equal(custom.shortcuts.deleteEntry, "Ctrl+X")
assert.equal(custom.shortcuts.clearHistory, "Ctrl+Shift+X")
assert.equal(custom.shortcuts.close, "Ctrl+Q")
assert.equal(custom.shortcuts.pasteEntry, "Ctrl+P")
assert.equal(custom.shortcuts.editEntry, "Ctrl+I")
assert.equal(custom.shortcuts.saveEdit, "Ctrl+Shift+S")
assert.equal(custom.historyRetentionDays, 30)

const conflicting = ClipboardConfig.parseConfig('{"shortcuts":{"deleteEntry":"Ctrl+X","clearHistory":"ctrl+x"}}')
assert.equal(conflicting.valid, false)
assert.equal(conflicting.shortcuts.deleteEntry, "Delete")
assert.equal(conflicting.shortcuts.clearHistory, "Shift+Delete")

const malformed = ClipboardConfig.parseConfig("{")
assert.equal(malformed.valid, false)
assert.equal(malformed.shortcuts.deleteEntry, "Delete")

assert.equal(ClipboardConfig.parseConfig('{"historyRetentionDays":-1}').historyRetentionDays, 0)
assert.equal(ClipboardConfig.parseConfig('{"historyRetentionDays":-1}').valid, false)
assert.equal(ClipboardConfig.parseConfig('{"historyRetentionDays":1.5}').historyRetentionDays, 0)
assert.equal(ClipboardConfig.parseConfig('{"historyRetentionDays":1.5}').valid, false)
assert.equal(ClipboardConfig.parseConfig('{"historyRetentionDays":"30"}').historyRetentionDays, 0)
assert.equal(ClipboardConfig.parseConfig('{"historyRetentionDays":"30"}').valid, false)
assert.equal(ClipboardConfig.parseConfig('{"historyRetentionDays":999999}').historyRetentionDays, 36500)
assert.equal(ClipboardConfig.parseConfig('{"historyRetentionDays":999999}').valid, false)

console.log("ClipboardConfig tests: OK")
