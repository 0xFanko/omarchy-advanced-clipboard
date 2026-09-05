function normalizeEntry(value) {
  if (typeof value === "string")
    return value.trim().length > 0 ? { type: "text", text: value } : null

  if (!value || typeof value !== "object") return null

  var type = String(value.type || value.kind || "")
  if (type === "text") {
    var text = String(value.text || "")
    return text.trim().length > 0 ? withSourceMetadata({ type: "text", text: text }, value) : null
  }

  if (type === "image") {
    var path = String(value.path || "")
    if (!path) return null
    var entry = {
      type: "image",
      path: path,
      mime: String(value.mime || "image/png")
    }
    return withSourceMetadata(entry, value)
  }

  return null
}

function withSourceMetadata(entry, value) {
  var sourceApp = String(value.sourceApp || "").trim()
  var sourceIcon = String(value.sourceIcon || "").trim()
  var sourceTitle = String(value.sourceTitle || "").trim()
  var capturedAt = String(value.capturedAt || "").trim()

  if (sourceApp) entry.sourceApp = sourceApp
  if (sourceIcon) entry.sourceIcon = sourceIcon
  if (sourceTitle) entry.sourceTitle = sourceTitle
  if (capturedAt) entry.capturedAt = capturedAt
  return entry
}

function entryKey(entry) {
  if (!entry) return ""
  if (entry.type === "image") return "image:" + String(entry.path || "")
  return "text:" + String(entry.text || "")
}

function parseHistory(raw) {
  try {
    var parsed = JSON.parse(String(raw || "[]"))
    var next = []
    if (!Array.isArray(parsed)) return next

    for (var i = 0; i < parsed.length; i++) {
      var entry = normalizeEntry(parsed[i])
      if (entry) next.push(entry)
    }
    return next
  } catch (e) {
    return []
  }
}

function addEntry(history, entry, limit) {
  var normalized = normalizeEntry(entry)
  var max = limit === undefined || limit === null ? 100 : Number(limit)
  if (isNaN(max)) max = 100
  max = Math.max(0, max)
  if (!normalized) return Array.isArray(history) ? history.slice(0, max) : []
  if (max === 0) return []

  var key = entryKey(normalized)
  var next = [normalized]
  var values = Array.isArray(history) ? history : []

  for (var i = 0; i < values.length && next.length < max; i++) {
    var existing = normalizeEntry(values[i])
    if (!existing || entryKey(existing) === key) continue
    next.push(existing)
  }

  return next
}

function removeEntryAt(history, index) {
  var values = Array.isArray(history) ? history : []
  var target = Number(index)
  if (isNaN(target) || target < 0 || target >= values.length) return values.slice()

  var next = values.slice()
  next.splice(target, 1)
  return next
}

function updateTextEntry(history, index, text) {
  var values = Array.isArray(history) ? history : []
  var target = Number(index)
  var nextText = String(text === undefined || text === null ? "" : text)
  if (isNaN(target) || target < 0 || target >= values.length || !nextText.trim()) return values.slice()

  var existing = normalizeEntry(values[target])
  if (!existing || existing.type !== "text") return values.slice()

  var next = values.slice()
  next[target] = withSourceMetadata({ type: "text", text: nextText }, existing)
  return next
}

function clearHistory() {
  return []
}

function parseEntryJson(line) {
  var raw = String(line || "").trim()
  if (!raw) return null
  try { return normalizeEntry(JSON.parse(raw)) } catch (e) { return null }
}

function searchableText(entry) {
  if (!entry) return ""
  var source = String(entry.sourceApp || "") + " " + String(entry.sourceTitle || "")
  if (entry.type === "image") return "image screenshot " + String(entry.mime || "") + " " + String(entry.capturedAt || "") + " " + source
  return String(entry.text || "") + " " + fileEntryText(entry) + " " + source
}

function linkUrl(entry) {
  if (!entry || entry.type !== "text") return ""
  var value = String(entry.text || "").trim()
  if (/\s/.test(value)) return ""
  return /^(https?|ftp):\/\/[^\s]+$/i.test(value) ? value : ""
}

function cleanSourceTitle(value, sourceApp) {
  var title = String(value || "").trim()
  if (!title) return ""

  var knownSuffixes = [
    " - Brave",
    " - Brave Browser",
    " - Google Chrome",
    " - Chromium",
    " — Mozilla Firefox",
    " - Mozilla Firefox"
  ]
  for (var i = 0; i < knownSuffixes.length; i++) {
    var suffix = knownSuffixes[i]
    if (title.length > suffix.length && title.slice(-suffix.length) === suffix)
      return title.slice(0, -suffix.length).trim()
  }

  var app = String(sourceApp || "").trim()
  if (app) {
    var separators = [" - ", " — ", " – "]
    for (var j = 0; j < separators.length; j++) {
      var genericSuffix = separators[j] + app
      if (title.length > genericSuffix.length && title.slice(-genericSuffix.length).toLowerCase() === genericSuffix.toLowerCase())
        return title.slice(0, -genericSuffix.length).trim()
    }
  }
  return title
}

function displayType(entry, isFile, isImage, url) {
  if (url) return "Link"
  if (isFile) return "File"
  if (isImage) return "Image"
  return "Text"
}

function captureDate(value) {
  var timestamp = String(value || "").trim()
  var match = /^(\d{4})-(\d{2})-(\d{2})T/.exec(timestamp)
  return match ? match[3] + "/" + match[2] + "/" + match[1] : "—"
}

function captureTime(value) {
  var timestamp = String(value || "").trim()
  var match = /(?:T|\s)(\d{2}):(\d{2})(?::\d{2})?/.exec(timestamp)
  return match ? match[1] + ":" + match[2] : "—"
}

function decodeFileUri(uri) {
  var value = String(uri || "").trim()
  if (value.indexOf("file://") !== 0) return ""

  var path = value.substring(7)
  if (path.indexOf("localhost/") === 0) path = path.substring(9)
  if (path.charAt(0) !== "/") return ""

  try { return decodeURIComponent(path) } catch (e) { return path }
}

function filePaths(entry) {
  if (!entry || entry.type !== "text") return []

  var lines = String(entry.text || "").split(/\r?\n/)
  var paths = []
  for (var i = 0; i < lines.length; i++) {
    var path = decodeFileUri(lines[i])
    if (path) paths.push(path)
  }
  return paths
}

function fileName(path) {
  var parts = String(path || "").split("/")
  return parts.length > 0 ? parts[parts.length - 1] : String(path || "")
}

function isImagePath(path) {
  return /\.(png|jpe?g|webp|gif|bmp|tiff?)$/i.test(String(path || ""))
}

function fileEntryText(entry) {
  var paths = filePaths(entry)
  if (paths.length === 0) return ""
  if (paths.length === 1) return fileName(paths[0])
  return paths.length + " files"
}

function imagePreviewText(entry) {
  var timestamp = String(entry && entry.capturedAt || "")
  if (!timestamp) return "Image"

  var label = String(entry && entry.mime || "") === "image/png" ? "Screenshot" : "Image"
  var date = captureDate(timestamp)
  var time = captureTime(timestamp)
  var formattedTimestamp = date !== "—" ? date + (time !== "—" ? " " + time : "") : timestamp
  return label + " from " + formattedTimestamp
}

function previewText(entry) {
  if (!entry) return ""
  if (entry.type === "image") return imagePreviewText(entry)
  var fileText = fileEntryText(entry)
  if (fileText) return fileText
  return String(entry.text || "").replace(/\s+/g, " ")
}

function fullText(entry) {
  if (!entry) return ""
  var paths = filePaths(entry)
  if (paths.length > 0) return paths.join("\n")
  return String(entry.text || "")
}

// The picker only ever searches and renders a prefix of an entry, so scan and
// render just that much. A single huge paste otherwise costs hundreds of
// megabytes of string work on every keystroke and stalls the whole shell.
// Pasting reads the full entry back from history by index, so nothing is lost.
var displayTextLimit = 8192

function cappedEntry(entry) {
  if (!entry || entry.type !== "text" || entry.text.length <= displayTextLimit) return entry

  // Cut on a line break so a file:// URI never truncates into a bogus path.
  var cut = entry.text.lastIndexOf("\n", displayTextLimit)
  return withSourceMetadata({ type: "text", text: entry.text.slice(0, cut > 0 ? cut : displayTextLimit) }, entry)
}

function displayRows(history, query, limit) {
  var values = Array.isArray(history) ? history : []
  var needle = String(query || "").trim().toLowerCase()
  var max = limit === undefined || limit === null ? 50 : Number(limit)
  if (isNaN(max)) max = 50
  max = Math.max(0, max)
  if (max === 0) return []

  var rows = []

  for (var i = 0; i < values.length; i++) {
    var entry = cappedEntry(normalizeEntry(values[i]))
    if (!entry) continue
    if (needle && searchableText(entry).toLowerCase().indexOf(needle) < 0) continue

    var paths = filePaths(entry)
    var isFile = paths.length > 0
    var isImage = entry.type === "image"
    var url = linkUrl(entry)
    var previewPath = isImage ? String(entry.path || "") : (isFile && paths.length === 1 && isImagePath(paths[0]) ? paths[0] : "")
    rows.push({
      entryType: isFile ? "file" : entry.type,
      typeLabel: displayType(entry, isFile, isImage, url),
      fullText: isImage ? "" : fullText(entry),
      previewText: previewText(entry),
      previewImage: previewPath,
      path: isImage ? String(entry.path || "") : (isFile && paths.length === 1 ? paths[0] : ""),
      mime: isImage ? String(entry.mime || "image/png") : "text/plain",
      sourceApp: String(entry.sourceApp || "Unknown"),
      sourceIcon: String(entry.sourceIcon || ""),
      capturedDate: captureDate(entry.capturedAt),
      capturedTime: captureTime(entry.capturedAt),
      url: url,
      title: url ? cleanSourceTitle(entry.sourceTitle, entry.sourceApp) : "",
      index: i
    })
    if (rows.length >= max) break
  }

  return rows
}

if (typeof module !== "undefined") {
  module.exports = {
    normalizeEntry: normalizeEntry,
    entryKey: entryKey,
    parseHistory: parseHistory,
    addEntry: addEntry,
    removeEntryAt: removeEntryAt,
    updateTextEntry: updateTextEntry,
    clearHistory: clearHistory,
    parseEntryJson: parseEntryJson,
    searchableText: searchableText,
    linkUrl: linkUrl,
    cleanSourceTitle: cleanSourceTitle,
    displayType: displayType,
    captureDate: captureDate,
    captureTime: captureTime,
    previewText: previewText,
    imagePreviewText: imagePreviewText,
    filePaths: filePaths,
    fileEntryText: fileEntryText,
    fullText: fullText,
    displayRows: displayRows
  }
}
