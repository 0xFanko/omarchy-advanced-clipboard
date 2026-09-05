const assert = require("node:assert/strict")
const ClipboardConfig = require("../ClipboardConfig.js")

const defaults = ClipboardConfig.parseConfig("")
assert.equal(defaults.valid, true)
assert.equal(defaults.shortcuts.deleteEntry, "Delete")
assert.equal(defaults.shortcuts.clearHistory, "Shift+Delete")
assert.deepEqual(defaults.excludedApplications, [])

const custom = ClipboardConfig.parseConfig(JSON.stringify({
  shortcuts: {
    deleteEntry: "Ctrl+X",
    clearHistory: "Ctrl+Shift+X"
  },
  excludedApplications: ["Brave Browser", "org.keepassxc.KeePassXC", "brave browser", "", 42]
}))
assert.equal(custom.shortcuts.deleteEntry, "Ctrl+X")
assert.equal(custom.shortcuts.clearHistory, "Ctrl+Shift+X")
assert.deepEqual(custom.excludedApplications, ["Brave Browser", "org.keepassxc.KeePassXC"])
assert.equal(ClipboardConfig.isApplicationExcluded(custom, "brave-browser", "brave browser"), true)
assert.equal(ClipboardConfig.isApplicationExcluded(custom, "org.keepassxc.KeePassXC", "KeePassXC"), true)
assert.equal(ClipboardConfig.isApplicationExcluded(custom, "firefox", "Firefox"), false)

const conflicting = ClipboardConfig.parseConfig('{"shortcuts":{"deleteEntry":"Ctrl+X","clearHistory":"ctrl+x"}}')
assert.equal(conflicting.shortcuts.deleteEntry, "Ctrl+X")
assert.equal(conflicting.shortcuts.clearHistory, "Shift+Delete")

const malformed = ClipboardConfig.parseConfig("{")
assert.equal(malformed.valid, false)
assert.equal(malformed.shortcuts.deleteEntry, "Delete")

console.log("ClipboardConfig tests: OK")
