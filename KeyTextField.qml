import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Single-line text field driven by key events only (copied from the hardened field in
// Video Trans / Screen Narrator; uses the live shell Commons and BorderSurface).
//
// Deliberately NOT a Qt Quick TextInput/TextField: those watch the Wayland clipboard and
// read foreign clipboard text whenever it changes, with no size or time limit, so a
// streaming clipboard owner could stall the shared shell while a field merely exists.
// This field never touches the clipboard by itself. Paste is explicit (Ctrl+V or
// Shift+Insert) and goes through `pasteHandler`, which must read the clipboard bounded
// and then call `field.insert(text)`. Without a handler, paste does nothing.
//
// API kept from the previous TextField: text, placeholderText, password, foreground,
// accent, font, horizontalPadding, verticalPadding, hasCursor, hovered, accepted(),
// editingFinished(), forceActiveFocus(). Enter emits accepted() and is left unhandled so
// a parent can react to it too; Escape, Up and Down also pass through to the parent.
FocusScope {
  id: root

  property string text: ""
  property string placeholderText: ""
  property bool password: false
  property int maximumLength: 4096
  property int cursorPosition: text.length
  property var pasteHandler: null
  property color foreground: Commons.Color.foreground
  property color accent: Commons.Color.accent
  property real horizontalPadding: Style.spacing.controlPaddingX
  property real verticalPadding: Style.spacing.inputPaddingY
  property bool hasCursor: false
  property font font: Qt.font({family: Style.font.family, pixelSize: Style.font.body})
  readonly property bool hovered: hover.hovered

  signal accepted()
  signal editingFinished()

  readonly property bool _focused: editor.activeFocus
  readonly property bool _hot: hovered || hasCursor
  readonly property var _borderSpec: Border.controlSpec(_focused ? "focus" : (_hot ? "hover-cursor" : "normal"), root.foreground, root.accent)
  readonly property string _shown: password ? "•".repeat(text.length) : text

  implicitWidth: Style.space(160)
  implicitHeight: Math.ceil(metrics.height) + verticalPadding * 2 + Border.top(_borderSpec) + Border.bottom(_borderSpec)
  activeFocusOnTab: true

  // Edits must not cut a caller's binding such as `text: root.config.value` (QML assignments
  // from JavaScript would). While editing, a Binding element drives `text` from `_draft`;
  // when focus leaves, the caller's binding comes back (RestoreBinding). A field without a
  // binding keeps the typed value (e.g. a token waiting for Apply).
  property string _draft: ""
  property bool _editing: false
  Binding {
    target: root
    property: "text"
    value: root._draft
    when: root._editing
    restoreMode: Binding.RestoreBinding
  }
  function _set(value) {
    if (!_editing) { _draft = text; _editing = true }
    _draft = value
  }

  onTextChanged: {
    // Someone set `text` from outside while editing (e.g. clearing a filter): follow it.
    if (_editing && text !== _draft) _draft = text
    if (cursorPosition > text.length) cursorPosition = text.length
  }

  // Insert text at the cursor: control characters are dropped and the length is capped.
  function insert(value) {
    var clean = String(value).replace(/[\u0000-\u001f\u007f]/g, "")
    var room = Math.max(0, maximumLength - text.length)
    clean = clean.slice(0, room)
    if (!clean) return
    var at = Math.min(cursorPosition, text.length)
    _set(text.slice(0, at) + clean + text.slice(at))
    cursorPosition = at + clean.length
  }

  function clear() {
    if (_editing) _draft = ""
    else text = ""
    cursorPosition = 0
  }

  BorderSurface {
    anchors.fill: parent
    color: Style.controlFill(root._focused, root._hot, root.foreground, root.accent)
    borderSpec: root._borderSpec
    radius: Style.cornerRadius
  }

  TextMetrics { id: metrics; font: root.font; text: "Ag" }
  TextMetrics { id: beforeCursor; font: root.font; text: root._shown.slice(0, root.cursorPosition) }

  Item {
    id: viewport
    anchors.fill: parent
    anchors.leftMargin: root.horizontalPadding + Border.left(root._borderSpec)
    anchors.rightMargin: root.horizontalPadding + Border.right(root._borderSpec)
    clip: true

    // Scroll so the cursor stays visible in long values.
    readonly property real offset: Math.max(0, beforeCursor.advanceWidth - width + 2)

    Text {
      x: -viewport.offset
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: root._shown
      color: root.foreground
      font: root.font
    }
    Text {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.text === ""
      textFormat: Text.PlainText
      text: root.placeholderText
      color: Qt.darker(root.foreground, 1.6)
      font: root.font
      elide: Text.ElideRight
      width: parent.width
    }
    Rectangle {
      x: beforeCursor.advanceWidth - viewport.offset
      anchors.verticalCenter: parent.verticalCenter
      width: Math.max(1, Style.space(1))
      height: metrics.height
      color: root.accent
      visible: root._focused && blink.on
    }
  }

  Timer {
    id: blink
    property bool on: true
    interval: 530
    repeat: true
    running: root._focused
    onRunningChanged: on = true
    onTriggered: on = !on
  }

  HoverHandler { id: hover; cursorShape: Qt.IBeamCursor }

  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton   // no middle-click primary-selection paste
    onClicked: { editor.forceActiveFocus(); root.cursorPosition = root.text.length }
  }

  Item {
    id: editor
    anchors.fill: parent
    focus: true
    onActiveFocusChanged: {
      if (activeFocus) return
      root.editingFinished()
      root._editing = false
    }

    Keys.onPressed: function(event) {
      var ctrl = (event.modifiers & Qt.ControlModifier) !== 0
      var at = Math.min(root.cursorPosition, root.text.length)
      if ((ctrl && event.key === Qt.Key_V) || ((event.modifiers & Qt.ShiftModifier) && event.key === Qt.Key_Insert)) {
        if (typeof root.pasteHandler === "function") root.pasteHandler(root)
        event.accepted = true
      } else if (event.key === Qt.Key_Backspace) {
        if (at > 0) {
          root._set(root.text.slice(0, at - 1) + root.text.slice(at))
          root.cursorPosition = at - 1
        }
        event.accepted = true
      } else if (event.key === Qt.Key_Delete) {
        if (at < root.text.length) root._set(root.text.slice(0, at) + root.text.slice(at + 1))
        event.accepted = true
      } else if (event.key === Qt.Key_Left) {
        root.cursorPosition = Math.max(0, at - 1); event.accepted = true
      } else if (event.key === Qt.Key_Right) {
        root.cursorPosition = Math.min(root.text.length, at + 1); event.accepted = true
      } else if (event.key === Qt.Key_Home) {
        root.cursorPosition = 0; event.accepted = true
      } else if (event.key === Qt.Key_End) {
        root.cursorPosition = root.text.length; event.accepted = true
      } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
        root.accepted()
        root.editingFinished()
      } else if (!ctrl && event.text.length > 0 && event.text.charCodeAt(0) >= 32) {
        root.insert(event.text)
        event.accepted = true
      }
    }
  }
}
