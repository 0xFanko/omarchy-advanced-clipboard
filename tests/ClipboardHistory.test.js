const assert = require("node:assert/strict")
const ClipboardHistory = require("../ClipboardHistory.js")

const url = "https://github.com/omacom/omarchy/tree/quattro/default/agents/skills/omarchy"
const link = ClipboardHistory.normalizeEntry({
  type: "text",
  text: url,
  sourceApp: "Brave Browser",
  sourceIcon: "brave-browser",
  sourceTitle: "omarchy/default/agents/skills/omarchy at quattro · omacom/omarchy - Brave"
})
const linkRow = ClipboardHistory.displayRows([link], "", 50)[0]

assert.equal(linkRow.typeLabel, "Link")
assert.equal(linkRow.sourceApp, "Brave Browser")
assert.equal(linkRow.sourceIcon, "brave-browser")
assert.equal(linkRow.url, url)
assert.equal(linkRow.title, "omarchy/default/agents/skills/omarchy at quattro · omacom/omarchy")

const legacyTextRow = ClipboardHistory.displayRows([{ type: "text", text: "not a link" }], "", 50)[0]
assert.equal(legacyTextRow.typeLabel, "Text")
assert.equal(legacyTextRow.sourceApp, "Unknown")
assert.equal(legacyTextRow.url, "")

const imageRow = ClipboardHistory.displayRows([{ type: "image", path: "/tmp/image.png", mime: "image/png" }], "", 50)[0]
assert.equal(imageRow.typeLabel, "Image")

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
