const assert = require("node:assert/strict")
const ClipboardHistory = require("../ClipboardHistory.js")

const url = "https://github.com/omacom/omarchy/tree/quattro/default/agents/skills/omarchy"
const link = ClipboardHistory.normalizeEntry({
  type: "text",
  text: url,
  sourceApp: "Brave Browser",
  sourceIcon: "brave-browser",
  sourceTitle: "omarchy/default/agents/skills/omarchy at quattro · omacom/omarchy - Brave",
  capturedAt: "2026-09-04T14:37:52+02:00"
})
const linkRow = ClipboardHistory.displayRows([link], "", 50)[0]

assert.equal(linkRow.typeLabel, "Link")
assert.equal(linkRow.sourceApp, "Brave Browser")
assert.equal(linkRow.sourceIcon, "brave-browser")
assert.equal(linkRow.url, url)
assert.equal(linkRow.title, "omarchy/default/agents/skills/omarchy at quattro · omacom/omarchy")
assert.equal(linkRow.capturedDate, "04/09/2026")
assert.equal(linkRow.capturedTime, "14:37")

const legacyTextRow = ClipboardHistory.displayRows([{ type: "text", text: "not a link" }], "", 50)[0]
assert.equal(legacyTextRow.typeLabel, "Text")
assert.equal(legacyTextRow.sourceApp, "Unknown")
assert.equal(legacyTextRow.url, "")
assert.equal(legacyTextRow.capturedDate, "—")
assert.equal(legacyTextRow.capturedTime, "—")

const imageRow = ClipboardHistory.displayRows([{ type: "image", path: "/tmp/image.png", mime: "image/png" }], "", 50)[0]
assert.equal(imageRow.typeLabel, "Image")

const editableHistory = [{
  type: "text",
  text: "before",
  sourceApp: "Foot",
  sourceIcon: "foot",
  sourceTitle: "Terminal",
  capturedAt: "2026-09-04T14:37:52+02:00"
}]
const editedHistory = ClipboardHistory.updateTextEntry(editableHistory, 0, "after\nsecond line")
assert.equal(editableHistory[0].text, "before")
assert.equal(editedHistory[0].text, "after\nsecond line")
assert.equal(editedHistory[0].sourceApp, "Foot")
assert.equal(editedHistory[0].sourceIcon, "foot")
assert.equal(editedHistory[0].sourceTitle, "Terminal")
assert.equal(editedHistory[0].capturedAt, "2026-09-04T14:37:52+02:00")
assert.deepEqual(ClipboardHistory.updateTextEntry(editableHistory, 0, "   "), editableHistory)
assert.deepEqual(ClipboardHistory.updateTextEntry([{ type: "image", path: "/tmp/image.png" }], 0, "text"), [{ type: "image", path: "/tmp/image.png" }])
assert.deepEqual(ClipboardHistory.updateTextEntry(editableHistory, 4, "text"), editableHistory)

assert.equal(ClipboardHistory.captureDate("Friday 12:30"), "—")
assert.equal(ClipboardHistory.captureTime("Friday 12:30"), "12:30")
assert.equal(ClipboardHistory.imagePreviewText({ type: "image", mime: "image/png", capturedAt: "2026-09-04T14:37:52+02:00" }), "Screenshot from 04/09/2026 14:37")

const retentionNow = "2026-09-06T12:00:00Z"
const retained = ClipboardHistory.retainRecentEntries([
  { type: "text", text: "recent", capturedAt: "2026-09-05T12:00:00Z" },
  { type: "text", text: "boundary", capturedAt: "2026-09-04T12:00:00Z" },
  { type: "text", text: "expired", capturedAt: "2026-09-04T11:59:59Z" },
  { type: "text", text: "legacy" }
], 2, retentionNow)
assert.deepEqual(retained.map(entry => entry.text), ["recent", "boundary", "legacy"])
assert.equal(ClipboardHistory.retainRecentEntries(retained, 0, retentionNow).length, 3)

const longEntry = {
  type: "text",
  text: "x".repeat(9000),
  sourceApp: "Firefox",
  sourceIcon: "firefox",
  sourceTitle: "Long page — Mozilla Firefox"
}
const longRow = ClipboardHistory.displayRows([longEntry], "firefox", 50)[0]
assert.equal(longRow.sourceApp, "Firefox")
assert.equal(longRow.sourceIcon, "firefox")

console.log("ClipboardHistory tests: OK")
