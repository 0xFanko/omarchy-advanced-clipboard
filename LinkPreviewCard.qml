import Quickshell
import Quickshell.Io
import QtQuick
import qs.Commons

Rectangle {
  id: root

  required property var entry
  required property string helper
  required property color foreground
  required property color borderColor
  required property string fontFamily
  required property int cornerRadius
  property bool previewEnabled: true

  property string state: "idle"
  property string title: ""
  property string description: ""
  property string imageSource: ""
  property string site: ""
  property string errorMessage: ""
  property int requestSerial: 0

  readonly property bool hasLink: root.entry && root.entry.typeLabel === "Link"
    && /^https?:\/\//i.test(String(root.entry.url || ""))

  visible: root.previewEnabled && root.hasLink && root.state !== "idle"
  width: parent ? parent.width : 0
  height: visible ? content.implicitHeight + Style.space(24) : 0
  radius: root.cornerRadius
  color: Util.alpha(root.borderColor, 0.10)

  function reset() {
    root.requestSerial++
    debounce.stop()
    request.running = false
    timeout.stop()
    root.state = "idle"
    root.title = ""
    root.description = ""
    root.imageSource = ""
    root.site = ""
    root.errorMessage = ""
  }

  function schedule() {
    root.reset()
    if (root.previewEnabled && root.hasLink && root.helper) debounce.restart()
  }

  function start() {
    if (!root.previewEnabled || !root.hasLink || !root.helper) return
    root.state = "loading"
    timeout.restart()
    request.command = [root.helper, String(root.entry.url), String(root.requestSerial)]
    request.running = true
  }

  function receive(raw) {
    if (!String(raw || "").trim()) return
    var payload
    try { payload = JSON.parse(raw) } catch (error) { return }
    if (!payload || Number(payload.serial) !== root.requestSerial) return

    timeout.stop()
    root.title = String(payload.title || "")
    root.description = String(payload.description || "")
    root.imageSource = payload.image ? Util.fileUrl(String(payload.image)) : ""
    root.site = String(payload.site || "")
    root.errorMessage = String(payload.error || "")
    root.state = payload.state === "ready" || payload.state === "empty" || payload.state === "error"
      ? payload.state : "error"
  }

  onEntryChanged: Qt.callLater(root.schedule)
  onPreviewEnabledChanged: {
    if (root.previewEnabled) Qt.callLater(root.schedule)
    else root.reset()
  }
  Component.onDestruction: root.reset()

  Process {
    id: request
    command: []
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.receive(text)
    }
  }

  Timer {
    id: debounce
    interval: 400
    repeat: false
    onTriggered: root.start()
  }

  Timer {
    id: timeout
    interval: 15000
    repeat: false
    onTriggered: {
      root.requestSerial++
      request.running = false
      root.state = "error"
      root.errorMessage = "The preview helper timed out."
    }
  }

  Column {
    id: content
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.margins: Style.space(12)
    spacing: Style.space(6)

    Rectangle {
      visible: root.state === "ready" && root.imageSource.length > 0
      width: parent.width
      height: visible ? Math.round(width * 9 / 16) : 0
      radius: Math.max(0, root.cornerRadius - Style.space(2))
      color: Util.alpha(root.foreground, 0.06)
      clip: true

      Image {
        anchors.fill: parent
        source: root.imageSource
        sourceSize.width: width * Screen.devicePixelRatio
        sourceSize.height: height * Screen.devicePixelRatio
        fillMode: Image.PreserveAspectCrop
        verticalAlignment: Image.AlignTop
        asynchronous: true
        cache: false
        smooth: true
      }
    }

    Text {
      visible: root.state === "loading"
      width: parent.width
      text: "Loading page preview…"
      textFormat: Text.PlainText
      color: root.foreground
      opacity: 0.68
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
    }

    Text {
      visible: root.state === "ready" && root.title.length > 0
      width: parent.width
      text: root.title
      textFormat: Text.PlainText
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.heading
      font.weight: Font.Medium
      wrapMode: Text.Wrap
      maximumLineCount: 2
      elide: Text.ElideRight
    }

    Text {
      visible: root.state === "ready" && root.description.length > 0
      width: parent.width
      text: root.description
      textFormat: Text.PlainText
      color: root.foreground
      opacity: 0.72
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
      maximumLineCount: 4
      elide: Text.ElideRight
    }

    Text {
      visible: root.state === "ready" && root.site.length > 0
      width: parent.width
      text: root.site
      textFormat: Text.PlainText
      color: root.foreground
      opacity: 0.58
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      elide: Text.ElideRight
    }

    Text {
      visible: root.state === "empty" || root.state === "error"
      width: parent.width
      text: root.state === "empty" ? "No page details are available." : "Preview unavailable. " + root.errorMessage
      textFormat: Text.PlainText
      color: root.foreground
      opacity: 0.68
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
    }
  }
}
