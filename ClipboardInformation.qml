import Quickshell
import QtQuick
import qs.Commons

Item {
  id: root

  property var entry: null
  property color foreground: "white"
  property color borderColor: "transparent"
  property string fontFamily: ""
  property int cornerRadius: 0
  property int rowHeight: Style.space(44)
  property bool editing: false
  property string draftText: ""
  property string editError: ""
  property string saveShortcut: "Ctrl+S"
  property string cancelShortcut: "Escape"
  property alias editedText: editField.text

  function focusEditor() {
    if (root.editing) editField.forceActiveFocus()
  }

  function iconSource(icon) {
    var value = String(icon || "")
    if (!value) return ""
    if (value.indexOf("file://") === 0 || value.indexOf("image://") === 0) return value
    if (value.charAt(0) === "/") return Util.fileUrl(value)
    return Quickshell.iconPath(value, true)
  }

  onEntryChanged: informationFlick.contentY = 0
  onEditingChanged: {
    if (!root.editing) return
    editField.text = root.draftText
    informationFlick.contentY = 0
    Qt.callLater(function() { root.focusEditor() })
  }

  Flickable {
    id: informationFlick
    anchors.fill: parent
    contentWidth: width
    contentHeight: informationColumn.implicitHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds

    Column {
      id: informationColumn
      width: informationFlick.width
      spacing: Style.space(6)

      Text {
        visible: root.entry && !root.entry.previewImage && !root.editing
        width: parent.width
        text: visible ? root.entry.fullText : ""
        textFormat: Text.PlainText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.title
        wrapMode: Text.WrapAnywhere
        elide: Text.ElideRight
        maximumLineCount: 12
      }

      Image {
        visible: root.entry && root.entry.previewImage.length > 0 && !root.editing
        width: parent.width
        height: visible ? Style.space(260) : 0
        source: visible ? root.entry.previewImage : ""
        fillMode: Image.PreserveAspectFit
        verticalAlignment: Image.AlignTop
        asynchronous: true
        smooth: true
      }

      Text {
        visible: root.editing
        width: parent.width
        height: visible ? implicitHeight : 0
        text: "Edit clipboard text"
        textFormat: Text.PlainText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.heading
        font.weight: Font.Medium
        bottomPadding: Style.space(6)
      }

      Rectangle {
        visible: root.editing
        width: parent.width
        height: visible ? Math.max(Style.space(220), editField.contentHeight + Style.space(24)) : 0
        radius: root.cornerRadius
        color: Util.alpha(root.borderColor, 0.10)
        border.width: Style.normalBorderWidth
        border.color: Util.alpha(root.borderColor, 0.45)

        TextEdit {
          id: editField
          anchors.fill: parent
          anchors.margins: Style.space(12)
          color: root.foreground
          selectionColor: Util.alpha(root.foreground, 0.28)
          selectedTextColor: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          textFormat: TextEdit.PlainText
          wrapMode: TextEdit.Wrap
          selectByMouse: true
          persistentSelection: true
          cursorVisible: activeFocus
        }
      }

      Text {
        visible: root.editing
        width: parent.width
        height: visible ? implicitHeight : 0
        text: root.saveShortcut + " to save · " + root.cancelShortcut + " to cancel"
        textFormat: Text.PlainText
        color: root.foreground
        opacity: 0.58
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
      }

      Text {
        visible: root.editing && root.editError.length > 0
        width: parent.width
        height: visible ? implicitHeight : 0
        text: root.editError
        textFormat: Text.PlainText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.weight: Font.Medium
        wrapMode: Text.Wrap
      }

      Text {
        width: parent.width
        text: "Information"
        textFormat: Text.PlainText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.heading
        font.weight: Font.Medium
        topPadding: Style.space(12)
        bottomPadding: Style.space(6)
        elide: Text.ElideRight
      }

      Rectangle {
        id: sourceRow
        width: parent.width
        height: root.rowHeight
        radius: root.cornerRadius
        color: Util.alpha(root.borderColor, 0.10)

        Text {
          id: sourceLabel
          anchors.left: parent.left
          anchors.leftMargin: Style.space(12)
          anchors.verticalCenter: parent.verticalCenter
          text: "Source"
          textFormat: Text.PlainText
          color: root.foreground
          opacity: 0.68
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
        }

        Row {
          anchors.left: sourceLabel.right
          anchors.leftMargin: Style.space(12)
          anchors.right: parent.right
          anchors.rightMargin: Style.space(12)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(8)
          layoutDirection: Qt.RightToLeft

          Text {
            width: Math.max(0, Math.min(implicitWidth, parent.width - sourceIcon.width - parent.spacing))
            text: root.entry ? root.entry.sourceApp : "Unknown"
            textFormat: Text.PlainText
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignRight
          }

          Image {
            id: sourceIcon
            readonly property string resolvedSource: root.entry ? root.iconSource(root.entry.sourceIcon) : ""
            visible: resolvedSource.length > 0 && status !== Image.Error
            width: visible ? Style.font.iconLarge : 0
            height: Style.font.iconLarge
            source: resolvedSource
            sourceSize.width: width * Screen.devicePixelRatio
            sourceSize.height: height * Screen.devicePixelRatio
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            smooth: true
          }
        }
      }

      InformationRow {
        label: "Type"
        value: root.entry ? root.entry.typeLabel : "—"
        valueRightAligned: true
      }

      InformationRow {
        label: "Date"
        value: root.entry ? root.entry.capturedDate : "—"
        valueRightAligned: true
        shaded: true
      }

      InformationRow {
        label: "Time"
        value: root.entry ? root.entry.capturedTime : "—"
        valueRightAligned: true
      }

      InformationRow {
        visible: root.entry && root.entry.url.length > 0
        label: "URL"
        value: visible ? root.entry.url : ""
        shaded: true
      }

      InformationRow {
        visible: root.entry && root.entry.url.length > 0
        label: "Title"
        value: visible && root.entry.title.length > 0 ? root.entry.title : "—"
      }

    }
  }

  component InformationRow: Rectangle {
    required property string label
    required property string value
    property bool valueRightAligned: false
    property bool shaded: false

    width: parent.width
    height: root.rowHeight
    radius: root.cornerRadius
    color: shaded ? Util.alpha(root.borderColor, 0.10) : "transparent"

    Text {
      id: informationLabel
      anchors.left: parent.left
      anchors.leftMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(76)
      text: parent.label
      textFormat: Text.PlainText
      color: root.foreground
      opacity: 0.68
      font.family: root.fontFamily
      font.pixelSize: Style.font.title
      elide: Text.ElideRight
    }

    Text {
      anchors.left: informationLabel.right
      anchors.leftMargin: Style.space(12)
      anchors.right: parent.right
      anchors.rightMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      text: parent.value
      textFormat: Text.PlainText
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.title
      elide: Text.ElideMiddle
      horizontalAlignment: parent.valueRightAligned ? Text.AlignRight : Text.AlignLeft
    }
  }
}
