import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui
import "ClipboardConfig.js" as ClipboardConfig
import "ClipboardHistory.js" as ClipboardHistory

Item {
  id: root

  property string omarchyPath: Quickshell.env("OMARCHY_PATH")
  property var manifest: null
  property bool opened: false
  property string filterText: ""
  property int selectedIndex: 0
  property bool cursorActive: false
  property bool clearConfirmOpen: false
  property bool editMode: false
  property int editingHistoryIndex: -1
  property string editingEntryKey: ""
  property string editDraftText: ""
  property string editError: ""
  property var history: []
  property bool initialized: false
  property var settings: ClipboardConfig.defaultConfig()

  readonly property string stateRoot: Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state"
  property string historyPath: root.stateRoot + "/omarchy/clipboard-history.json"
  property string imageDirectory: root.stateRoot + "/omarchy/clipboard-images"
  readonly property string pluginPath: root.manifest && root.manifest.__sourceDir ? String(root.manifest.__sourceDir) : root.omarchyPath + "/shell/plugins/clipboard"
  readonly property string configPath: root.pluginPath + "/clipboard.json"
  property string captureScript: root.pluginPath + "/capture.sh"
  // Shares the [menu] surface tokens — themes that style the menu also
  // style the clipboard. Selected-row colors composed in the
  // singleton so consumers drop them straight into Rectangle bindings.
  property color background: Color.menu.background
  property color foreground: Color.menu.text
  property color border: Color.menu.border
  property var borderSpec: Border.surfaceSpec("menu", "border", border, Math.max(1, Style.space(2)))
  property color scrim: Color.menu.scrim
  property color selectedBackground: Color.menu.selectedBackground
  property color selectedText: Color.menu.selectedText
  readonly property int cornerRadius: Style.cornerRadius
  property string fontFamily: Style.font.menuFamily
  property int contentMargin: Style.spacing.panelPadding
  property int headerHeight: Math.max(Style.space(34), Style.font.title + Style.spacing.controlPaddingY * 2)
  property int contentSpacing: Style.spacing.md
  property int cardWidth: Math.max(0, Math.min(Style.space(875), panel.width - Style.gapsOut * 2))
  property int cardHeight: Math.max(0, Math.min(Style.space(600), panel.height - Style.gapsOut * 2))
  property int rowHeight: Math.max(Style.space(50), Style.font.body + Style.font.caption + Style.spacing.rowPaddingX * 2)
  property int informationRowHeight: Math.max(Style.space(44), Style.font.title + Style.spacing.controlPaddingY * 2)
  readonly property bool showDetails: root.cardWidth >= Style.space(640)
  property int historyLimit: 500
  readonly property var shortcuts: root.settings.shortcuts

  function initialize() {
    if (root.initialized) return
    root.initialized = true
    initProc.running = true
  }

  function open(payloadJson) {
    root.resetEditState()
    root.applyHistoryRetention(root.history, true)
    root.opened = true
    root.filterText = ""
    root.selectedIndex = 0
    root.cursorActive = true
    root.disarmPointer()
    root.rebuildDisplay()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function close() {
    root.resetEditState()
    root.cancelClearHistory()
    root.opened = false
  }

  function resetEditState() {
    root.editMode = false
    root.editingHistoryIndex = -1
    root.editingEntryKey = ""
    root.editDraftText = ""
    root.editError = ""
  }

  function selectedRow() {
    if (!root.cursorActive || root.selectedIndex < 0 || root.selectedIndex >= displayModel.count) return null
    return displayModel.get(root.selectedIndex)
  }

  function canEditSelected() {
    var row = root.selectedRow()
    return row && row.entryType === "text" && row.typeLabel === "Text"
  }

  function beginEdit() {
    if (!root.canEditSelected()) return

    var row = root.selectedRow()
    var entry = ClipboardHistory.normalizeEntry(root.history[row.historyIndex])
    if (!entry || entry.type !== "text") return

    root.editingHistoryIndex = row.historyIndex
    root.editingEntryKey = ClipboardHistory.entryKey(entry)
    root.editDraftText = entry.text
    root.editError = ""
    root.editMode = true
    Qt.callLater(function() { detailsPane.focusEditor() })
  }

  function currentEditingHistoryIndex() {
    if (!root.editingEntryKey) return -1
    if (root.editingHistoryIndex >= 0 && root.editingHistoryIndex < root.history.length) {
      var originalEntry = ClipboardHistory.normalizeEntry(root.history[root.editingHistoryIndex])
      if (originalEntry && ClipboardHistory.entryKey(originalEntry) === root.editingEntryKey)
        return root.editingHistoryIndex
    }
    for (var i = 0; i < root.history.length; i++) {
      var entry = ClipboardHistory.normalizeEntry(root.history[i])
      if (entry && ClipboardHistory.entryKey(entry) === root.editingEntryKey) return i
    }
    return -1
  }

  function saveEdit() {
    if (!root.editMode) return
    var nextText = detailsPane.editedText
    if (!String(nextText).trim()) {
      root.editError = "Clipboard text cannot be empty."
      return
    }

    var targetIndex = root.currentEditingHistoryIndex()
    if (targetIndex < 0) {
      root.editError = "This clipboard entry no longer exists."
      return
    }

    root.history = ClipboardHistory.updateTextEntry(root.history, targetIndex, nextText)
    root.saveHistory()
    root.resetEditState()
    root.rebuildDisplay()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function cancelEdit() {
    if (!root.editMode) return
    root.resetEditState()
    root.rebuildDisplay()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function clearSearchOrClose() {
    if (root.filterText) root.setFilter("")
    else root.close()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open("{}")
  }

  function normalizeEntry(value) {
    return ClipboardHistory.normalizeEntry(value)
  }

  function entryKey(entry) {
    return ClipboardHistory.entryKey(entry)
  }

  function loadHistory(raw) {
    root.applyHistoryRetention(ClipboardHistory.parseHistory(raw), true)
    if (root.opened && !root.editMode) root.rebuildDisplay()
  }

  function removeExpiredImageFiles(paths) {
    var command = ["rm", "-f", "--"]
    for (var i = 0; i < paths.length; i++) {
      var path = String(paths[i] || "")
      if (ClipboardHistory.isManagedImagePath(path, root.imageDirectory)) command.push(path)
    }
    if (command.length > 3) Quickshell.execDetached(command)
  }

  function applyHistoryRetention(values, persistChanges) {
    var result = ClipboardHistory.historyRetentionResult(values, root.settings.historyRetentionDays)
    var changed = result.entries.length !== values.length
    root.history = result.entries
    root.removeExpiredImageFiles(result.expiredImagePaths)
    if (changed && persistChanges)
      historyFile.setText(JSON.stringify(root.history.slice(0, root.historyLimit), null, 2) + "\n")
    return changed
  }

  function loadSettings(raw) {
    var nextSettings = ClipboardConfig.parseConfig(raw)
    if (!nextSettings.valid) console.warn("Clipboard: invalid config at " + root.configPath + "; using defaults")
    root.settings = nextSettings
    var changed = root.applyHistoryRetention(root.history, true)
    if (changed && root.opened && !root.editMode) root.rebuildDisplay()
  }

  function saveHistory() {
    root.applyHistoryRetention(root.history, false)
    historyFile.setText(JSON.stringify(root.history.slice(0, root.historyLimit), null, 2) + "\n")
  }

  function addClipboardEntry(entry) {
    var normalized = ClipboardHistory.normalizeEntry(entry)
    if (!normalized) return

    root.history = ClipboardHistory.addEntry(root.history, normalized, root.historyLimit)
    root.saveHistory()
    if (root.opened && !root.editMode) root.rebuildDisplay()
  }

  function addClipboardJson(line) {
    root.addClipboardEntry(ClipboardHistory.parseEntryJson(line))
  }

  function requestClearHistory() {
    if (root.history.length === 0) return
    clearConfirm.selectedIndex = 1
    root.clearConfirmOpen = true
  }

  function cancelClearHistory() {
    root.clearConfirmOpen = false
    root.disarmPointer()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function confirmClearHistory() {
    root.history = ClipboardHistory.clearHistory()
    root.saveHistory()
    root.selectedIndex = 0
    root.cursorActive = false
    root.disarmPointer()
    root.clearConfirmOpen = false
    root.rebuildDisplay()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function removeDisplayIndex(index) {
    if (index < 0 || index >= displayModel.count) return

    var row = displayModel.get(index)
    root.history = ClipboardHistory.removeEntryAt(root.history, row.historyIndex)
    root.saveHistory()

    if (displayModel.count <= 1) {
      root.selectedIndex = 0
      root.cursorActive = false
    } else if (root.selectedIndex >= displayModel.count - 1) {
      root.selectedIndex = displayModel.count - 2
    }

    root.disarmPointer()
    root.rebuildDisplay()
  }

  function rebuildDisplay() {
    var rows = ClipboardHistory.displayRows(root.history, root.filterText, 50)

    displayModel.clear()
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i]
      displayModel.append({
        entryType: row.entryType,
        typeLabel: row.typeLabel,
        fullText: row.fullText,
        previewText: row.previewText,
        previewImage: row.previewImage ? Util.fileUrl(row.previewImage) : "",
        path: row.path,
        mime: row.mime,
        sourceApp: row.sourceApp,
        sourceIcon: row.sourceIcon,
        capturedDate: row.capturedDate,
        capturedTime: row.capturedTime,
        url: row.url,
        title: row.title,
        historyIndex: row.index
      })
    }

    if (displayModel.count === 0) selectedIndex = 0
    else if (selectedIndex >= displayModel.count) selectedIndex = displayModel.count - 1
    else if (selectedIndex < 0) selectedIndex = 0

    Qt.callLater(function() {
      if (displayModel.count > 0) resultList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
    })
  }

  function select(delta) {
    if (displayModel.count === 0) return
    root.disarmPointer()
    if (!cursorActive) {
      cursorActive = true
      selectedIndex = delta < 0 ? displayModel.count - 1 : 0
    } else {
      selectedIndex = (selectedIndex + delta + displayModel.count) % displayModel.count
    }
    resultList.positionViewAtIndex(selectedIndex, ListView.Contain)
  }

  function selectAbsolute(index) {
    if (displayModel.count === 0) return
    root.disarmPointer()
    root.cursorActive = true
    root.selectedIndex = Math.max(0, Math.min(index, displayModel.count - 1))
    resultList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
  }

  function setFilter(nextFilter) {
    root.filterText = nextFilter
    root.selectedIndex = 0
    root.cursorActive = true
    root.disarmPointer()
    root.rebuildDisplay()
  }

  function disarmPointer() {
    pointerGate.reset()
  }

  function selectFromPointer(index, item, mouse) {
    if (!pointerGate.moved(item, mouse)) return
    root.cursorActive = true
    root.selectedIndex = index
  }

  function activateIndex(index) {
    if (index < 0 || index >= displayModel.count) return
    var row = displayModel.get(index)
    root.applySelected(row)
  }

  function copyIndex(index) {
    if (index < 0 || index >= displayModel.count) return
    var row = displayModel.get(index)
    root.copySelected(row)
  }

  function openIndex(index) {
    if (index < 0 || index >= displayModel.count) return
    var row = displayModel.get(index)
    root.openSelected(row)
  }

  function pasteCurrentEntry() {
    if (root.cursorActive) root.activateIndex(root.selectedIndex)
    else if (displayModel.count > 0) root.cursorActive = true
  }

  function copyCurrentEntry() {
    if (root.cursorActive) root.copyIndex(root.selectedIndex)
  }

  function openCurrentEntry() {
    if (root.cursorActive) root.openIndex(root.selectedIndex)
  }

  function applySelected(row) {
    if (!row) return
    root.opened = false
    if (row.entryType === "image") {
      Quickshell.execDetached([root.omarchyPath + "/bin/omarchy-clipboard-paste-file", row.mime, row.path])
    } else if (row.fullText) {
      Quickshell.execDetached([root.omarchyPath + "/bin/omarchy-clipboard-paste-text", "--shift-insert", "--history-index", String(row.historyIndex)])
    }
  }

  function copySelected(row) {
    if (!row) return
    root.opened = false
    if (row.entryType === "image") {
      Quickshell.execDetached([root.omarchyPath + "/bin/omarchy-clipboard-paste-file", "--copy-only", row.mime, row.path])
    } else if (row.fullText) {
      Quickshell.execDetached([root.omarchyPath + "/bin/omarchy-clipboard-paste-text", "--copy-only", "--history-index", String(row.historyIndex)])
    }
  }

  function openSelected(row) {
    if (!row) return
    root.opened = false
    Quickshell.execDetached([root.omarchyPath + "/bin/omarchy-clipboard-open", "--history-index", String(row.historyIndex)])
  }

  Component.onCompleted: Qt.callLater(root.initialize)
  onManifestChanged: root.initialize()

  ListModel { id: displayModel }

  PointerMoveGate {
    id: pointerGate
    referenceItem: card
  }

  FileView {
    id: historyFile
    path: root.historyPath
    watchChanges: true
    atomicWrites: true
    printErrors: false
    onLoaded: root.loadHistory(text())
    onLoadFailed: root.loadHistory("[]")
    onFileChanged: reload()
  }

  FileView {
    id: configFile
    path: root.configPath
    watchChanges: true
    printErrors: false
    onLoaded: root.loadSettings(text())
    onLoadFailed: root.loadSettings("{}")
    onFileChanged: reload()
  }

  // Reap watchers left behind by a previous shell instance, then start our
  // own. The pdeathsig on the watchers makes the kernel kill them whenever
  // the shell exits, however it exits, so no further lifecycle management.
  Process {
    id: initProc
    command: ["pkill", "-f", "wl-paste .*--watch .*/plugins/[^/]+/capture\\.sh"]
    onExited: {
      currentProc.running = true
      textWatchProc.running = true
      imageWatchProc.running = true
    }
  }

  Process {
    id: currentProc
    command: [root.captureScript]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.addClipboardJson(text)
    }
  }

  Process {
    id: textWatchProc
    command: ["setpriv", "--pdeathsig", "TERM", "wl-paste", "--type", "text", "--watch", root.captureScript, "text"]
    onExited: watchRestartTimer.restart()
    stdout: SplitParser {
      onRead: function(data) { root.addClipboardJson(data) }
    }
  }

  Process {
    id: imageWatchProc
    command: ["setpriv", "--pdeathsig", "TERM", "wl-paste", "--type", "image/png", "--watch", root.captureScript, "image/png"]
    onExited: watchRestartTimer.restart()
    stdout: SplitParser {
      onRead: function(data) { root.addClipboardJson(data) }
    }
  }

  // A watcher that dies takes clipboard history with it, silently: copying still
  // works, the picker still opens, and the old entries are all still there, so
  // nothing recorded until the next shell reload. Bring it back instead.
  Timer {
    id: watchRestartTimer
    interval: 1000
    repeat: false
    onTriggered: {
      if (!textWatchProc.running) textWatchProc.running = true
      if (!imageWatchProc.running) imageWatchProc.running = true
    }
  }

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "omarchy-clipboard"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    exclusionMode: ExclusionMode.Ignore

    Shortcut {
      sequence: String(root.shortcuts.close)
      enabled: root.opened && !root.clearConfirmOpen
      autoRepeat: false
      onActivated: root.editMode ? root.cancelEdit() : root.clearSearchOrClose()
    }

    Shortcut {
      sequence: String(root.shortcuts.editEntry)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      autoRepeat: false
      onActivated: root.beginEdit()
    }

    Shortcut {
      sequence: String(root.shortcuts.saveEdit)
      enabled: root.opened && !root.clearConfirmOpen && root.editMode
      autoRepeat: false
      onActivated: root.saveEdit()
    }

    Shortcut {
      sequence: String(root.shortcuts.previousEntry)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      onActivated: root.select(-1)
    }

    Shortcut {
      sequence: String(root.shortcuts.nextEntry)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      onActivated: root.select(1)
    }

    Shortcut {
      sequence: String(root.shortcuts.previousPage)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      onActivated: root.select(-6)
    }

    Shortcut {
      sequence: String(root.shortcuts.nextPage)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      onActivated: root.select(6)
    }

    Shortcut {
      sequence: String(root.shortcuts.firstEntry)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      onActivated: root.selectAbsolute(0)
    }

    Shortcut {
      sequence: String(root.shortcuts.lastEntry)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      onActivated: root.selectAbsolute(displayModel.count - 1)
    }

    Shortcut {
      sequence: String(root.shortcuts.pasteEntry)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      autoRepeat: false
      onActivated: root.pasteCurrentEntry()
    }

    Shortcut {
      sequence: String(root.shortcuts.copyEntry)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      autoRepeat: false
      onActivated: root.copyCurrentEntry()
    }

    Shortcut {
      sequence: String(root.shortcuts.openEntry)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      autoRepeat: false
      onActivated: root.openCurrentEntry()
    }

    Shortcut {
      sequence: String(root.shortcuts.deleteEntry)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      autoRepeat: false
      onActivated: root.removeDisplayIndex(root.selectedIndex)
    }

    Shortcut {
      sequence: String(root.shortcuts.clearHistory)
      enabled: root.opened && !root.clearConfirmOpen && !root.editMode
      autoRepeat: false
      onActivated: root.requestClearHistory()
    }

    Rectangle {
      anchors.fill: parent
      color: root.scrim
    }

    MouseArea {
      anchors.fill: parent
      onClicked: root.close()
    }

    BorderSurface {
      id: card
      width: root.cardWidth
      height: root.cardHeight
      radius: root.cornerRadius
      anchors.centerIn: parent
      color: root.background
      borderSpec: root.borderSpec
      padding: root.contentMargin

      MouseArea { anchors.fill: parent; onClicked: {} }

      Item {
        id: keyCatcher
        anchors.fill: parent
        z: root.clearConfirmOpen ? 20 : 0
        focus: true

        Keys.priority: Keys.BeforeItem
        Keys.onPressed: function(event) {
          if (root.clearConfirmOpen) {
            if (clearConfirm.handleKey(event)) event.accepted = true
            return
          }

          if (Util.editsFilter(event, root.filterText)) {
            root.setFilter(Util.editedFilter(event, root.filterText))
            event.accepted = true
          } else if (event.text && event.text.length === 1 && event.text.charCodeAt(0) >= 32 && event.text.charCodeAt(0) !== 127) {
            root.setFilter(root.filterText + event.text)
            event.accepted = true
          }
        }

        ConfirmDialog {
          id: clearConfirm

          anchors.fill: parent
          opened: root.clearConfirmOpen
          z: 10
          message: "Delete entire clipboard history?"
          confirmText: "Delete"
          background: root.background
          foreground: root.foreground
          scrim: root.scrim
          selectedBackground: root.selectedBackground
          selectedText: root.selectedText
          fontFamily: root.fontFamily
          cornerRadius: root.cornerRadius
          onCanceled: root.cancelClearHistory()
          onConfirmed: root.confirmClearHistory()
        }
      }

      Column {
        anchors.fill: parent
        anchors.topMargin: card.contentTopInset
        anchors.rightMargin: card.contentRightInset
        anchors.bottomMargin: card.contentBottomInset
        anchors.leftMargin: card.contentLeftInset
        spacing: root.contentSpacing

        Rectangle {
          width: parent.width
          height: root.headerHeight
          radius: root.cornerRadius
          color: "transparent"

          Text {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: root.filterText || "Dev : Search clipboard…"
            color: root.foreground
            opacity: root.filterText ? 1 : 0.58
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            elide: Text.ElideRight
          }
        }

        Item {
          width: parent.width
          height: Math.max(0, parent.height - root.headerHeight - root.contentSpacing)

          Row {
            anchors.fill: parent
            spacing: 0

            Item {
              width: root.editMode ? 0 : (root.showDetails && displayModel.count > 0 ? parent.width / 2 : parent.width)
              height: parent.height
              clip: true

              ListView {
                id: resultList
                anchors.fill: parent
                anchors.rightMargin: root.contentMargin
                model: displayModel
                clip: true
                interactive: !root.editMode
                spacing: Style.space(4)
                boundsBehavior: Flickable.StopAtBounds

                delegate: Rectangle {
                  id: row
                  required property int index
                  required property string entryType
                  required property string previewText
                  required property string fullText
                  required property string previewImage

                  readonly property bool hasCursor: root.cursorActive && index === root.selectedIndex

                  width: ListView.view.width
                  height: root.rowHeight
                  radius: root.cornerRadius
                  color: hasCursor ? root.selectedBackground : "transparent"

                  Row {
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(12)
                    anchors.rightMargin: Style.space(12)
                    anchors.topMargin: Style.space(8)
                    anchors.bottomMargin: Style.space(8)
                    spacing: Style.space(10)

                    Image {
                      visible: parent.parent.previewImage.length > 0
                      width: visible ? parent.height : 0
                      height: parent.height
                      source: parent.parent.previewImage
                      fillMode: Image.PreserveAspectFit
                      asynchronous: true
                      smooth: true
                    }

                    Text {
                      width: parent.width - (parent.parent.previewImage.length > 0 ? parent.height + parent.spacing : 0)
                      height: parent.height
                      text: parent.parent.previewText
                      color: parent.parent.hasCursor ? root.selectedText : root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.title
                      opacity: parent.parent.entryType === "image" || parent.parent.entryType === "file" ? 0.72 : 1.0
                      elide: Text.ElideRight
                      wrapMode: Text.NoWrap
                      verticalAlignment: Text.AlignVCenter
                    }
                  }

                  MouseArea {
                    anchors.fill: parent
                    enabled: !root.editMode
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onPositionChanged: function(mouse) {
                      root.selectFromPointer(row.index, row, mouse)
                    }
                    onClicked: {
                      root.cursorActive = true
                      root.selectedIndex = row.index
                      root.activateIndex(row.index)
                    }
                  }
                }
              }
            }

            Item {
              id: detailsPane
              visible: (root.showDetails || root.editMode) && displayModel.count > 0
              width: visible ? (root.editMode && !root.showDetails ? parent.width : parent.width / 2) : 0
              height: parent.height
              clip: true

              property var activeRow: displayModel.count > 0 && root.selectedIndex >= 0 && root.selectedIndex < displayModel.count ? displayModel.get(root.selectedIndex) : null
              readonly property string editedText: clipboardInformation.editedText

              function focusEditor() {
                clipboardInformation.focusEditor()
              }

              Rectangle {
                visible: root.showDetails
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: Style.normalBorderWidth
                color: Util.alpha(root.border, 0.28)
              }

              ClipboardInformation {
                id: clipboardInformation
                anchors.fill: parent
                anchors.leftMargin: root.contentMargin
                entry: detailsPane.activeRow
                foreground: root.foreground
                borderColor: root.border
                fontFamily: root.fontFamily
                cornerRadius: root.cornerRadius
                rowHeight: root.informationRowHeight
                editing: root.editMode
                draftText: root.editDraftText
                editError: root.editError
                saveShortcut: String(root.shortcuts.saveEdit)
                cancelShortcut: String(root.shortcuts.close)
              }
            }
          }

          Column {
            anchors.centerIn: parent
            spacing: Style.space(8)
            visible: displayModel.count === 0

            Text {
              text: "󰅌"
              color: root.selectedText
              opacity: 0.8
              font.family: root.fontFamily
              font.pixelSize: Style.font.displayLarge
              horizontalAlignment: Text.AlignHCenter
              width: parent.width
            }

            Text {
              text: root.history.length === 0 ? "Clipboard is empty" : "No matches for “" + root.filterText + "”"
              color: root.foreground
              opacity: 0.7
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              horizontalAlignment: Text.AlignHCenter
              width: parent.width
            }
          }
        }
      }
    }
  }

}
