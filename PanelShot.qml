import QtQuick
import QtQuick.Window
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "I18n.js" as I18n

// F12 saves a screenshot of the panel, as F12 does in Uwe's GTK apps (AI Playground, Maprisma).
// Shared copy: ~/Projects/omarchy-plugin-i18n/qml (install_shot.py); don't edit it in a plugin.
//
// Wrap the panel's PanelKeyCatcher in it:
//
//   KeyboardPanel {
//     contentHeight: panel.fittedContentHeight(Math.max(column.implicitHeight, shot.neededHeight))
//     PanelShot {
//       id: shot
//       anchors.fill: parent
//       pluginId: "<plugin id>"; folderName: "<Plugin name>"
//       active: root.screenshotKey; panelOpen: root.opened; returnFocus: keyCatcher
//       lang: root.lang; fg: root.fg; dim: root.dim; fontFamily: root.font; corners: root.corners
//       PanelKeyCatcher { id: keyCatcher; anchors.fill: parent; blocked: shot.overlayOpen || … }
//     }
//   }
//
// Keys: the catcher passes keys it does not handle (F12 has no text) up to its parents, so F12
// arrives here; with a modifier, or while the setting is off, it is left alone. The picture is
// this item drawn by Qt (grabToImage, on the panel's own background), written to
// $XDG_RUNTIME_DIR/<plugin id>-shot/<random>.png (a 0700 folder shot_save.py makes), then moved
// by `shot_save.py save` to ~/Pictures/<Folder name>/. An overlay names the file and takes an
// optional note ("What is wrong?") in a KeyTextField; Enter or Done saves it next to the picture
// through `shot_save.py note` (stdin), Esc skips it, and closing the panel keeps what was typed.
// Nothing is sent anywhere; python3 runs with an argv list, never a shell.
Item {
  id: shot

  property string pluginId: ""
  property string folderName: ""
  property bool active: true            // the "F12 saves a screenshot" setting
  property bool panelOpen: true         // the panel's `opened`: closing it keeps a typed note
  property Item returnFocus: null       // gets the keys back when the overlay closes
  property string helper: decodeURIComponent(Qt.resolvedUrl("shot_save.py").toString().replace(/^file:\/\//, ""))
  property string lang: "en"
  property color fg: Commons.Color.popups.text
  property color dim: Qt.darker(fg, 1.45)
  property string fontFamily: Style.font.family
  property var corners: ({ radius: Style.cornerRadius, control: Math.min(Style.cornerRadius, 8),
                           small: Math.min(Style.cornerRadius, 4), pill: Style.cornerRadius > 0 ? 999 : 0 })

  // "" (nothing), "busy", "note" (saved, asking for a note), "error"
  property string phase: ""
  readonly property bool overlayOpen: phase === "note" || phase === "error"
  readonly property bool busy: phase === "busy"
  // The panel should be at least this tall while the overlay shows.
  readonly property real neededHeight: overlayOpen ? card.implicitHeight + Style.space(24) : 0
  property string savedPath: ""
  property string savedShown: ""
  property string errorText: ""
  property bool grabbing: false
  property var grabbed: null
  property string tmpPath: ""

  readonly property real pixelRatio: Screen.devicePixelRatio

  signal saved(string path)

  function t(key, args) { return I18n.tr(lang, key, args) }

  function take() {
    if (!active || phase !== "" || pluginId === "" || folderName === "") return
    phase = "busy"
    // A frame later: a layout changed in the same turn is only measured when it is drawn.
    grabDelay.restart()
  }

  function grabNow() {
    var width = Math.ceil(shot.width), height = Math.ceil(shot.height)
    if (phase !== "busy") return
    if (width <= 0 || height <= 0) { phase = ""; return }
    var ratio = shot.pixelRatio > 0 ? shot.pixelRatio : 1
    grabbing = true       // the backdrop below, so the picture has the panel's background
    var started = shot.grabToImage(function(result) {
      shot.grabbing = false
      if (!result) { shot.fail("the panel could not be drawn"); return }
      shot.grabbed = result
      prepare.running = true
    }, Qt.size(Math.round(width * ratio), Math.round(height * ratio)))
    if (!started) { grabbing = false; fail("the panel could not be drawn") }
  }

  Timer { id: grabDelay; interval: 40; onTriggered: shot.grabNow() }

  function randomName() {
    var hex = "0123456789abcdef", name = ""
    for (var i = 0; i < 24; i++) name += hex[Math.floor(Math.random() * 16)]
    return name + ".png"
  }

  function answer(text) {
    try { return JSON.parse(String(text).trim().split("\n").pop()) } catch (e) { return null }
  }

  function fail(message) {
    grabbed = null
    errorText = t("shotFailed", [message])
    phase = "error"
  }

  function showNote(result) {
    savedPath = result.path
    savedShown = result.shown || result.path
    noteField.clear()
    phase = "note"
    shot.saved(savedPath)
    Qt.callLater(function() { if (shot.phase === "note") noteField.forceActiveFocus() })
  }

  // Done, Enter or the panel closing: the note (if any) goes next to the picture.
  function finish(keepNote) {
    if (phase === "note") {
      var text = keepNote ? noteField.text.trim() : ""
      phase = ""
      if (text !== "") {
        noteSaver.pendingText = text.slice(0, 4000)
        noteSaver.command = ["python3", shot.helper, "note", shot.savedPath, shot.folderName]
        noteSaver.stdinEnabled = true
        noteSaver.running = true
      }
    } else if (phase === "error") {
      phase = ""
    } else {
      return
    }
    noteField.clear()
    if (returnFocus && panelOpen) returnFocus.forceActiveFocus()
  }

  onPanelOpenChanged: {
    if (!panelOpen) finish(true)
    else if (phase === "note") refocus.restart()   // saved while the panel was closed
  }
  // After the panel's own focus call on opening.
  Timer { id: refocus; interval: 120; onTriggered: if (shot.phase === "note" && shot.panelOpen) noteField.forceActiveFocus() }
  onActiveChanged: if (!active && phase === "busy") phase = ""

  Keys.onPressed: function(event) {
    var mods = Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier
    if (event.key === Qt.Key_F12 && !(event.modifiers & mods) && shot.active && shot.phase === "") {
      shot.take()
      event.accepted = true
      return
    }
    if (!shot.overlayOpen) return
    if (event.key === Qt.Key_Escape) shot.finish(false)
    else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) shot.finish(true)
    event.accepted = true            // nothing typed here reaches the panel behind the overlay
  }

  Process {
    id: prepare
    command: ["python3", shot.helper, "prepare", shot.pluginId]
    stdout: StdioCollector { id: prepareOut }
    onExited: function(code) {
      var result = shot.answer(prepareOut.text)
      if (!result || !result.ok || !result.dir) { shot.fail(result && result.error ? result.error : "python3 did not answer"); return }
      shot.tmpPath = result.dir + "/" + shot.randomName()
      var written = shot.grabbed && shot.grabbed.saveToFile(shot.tmpPath)
      shot.grabbed = null
      if (!written) { shot.fail("the picture could not be written"); return }
      saver.command = ["python3", shot.helper, "save", shot.tmpPath, shot.folderName]
      saver.running = true
    }
  }

  Process {
    id: saver
    stdout: StdioCollector { id: saverOut }
    onExited: function(code) {
      var result = shot.answer(saverOut.text)
      if (!result || !result.ok || !result.path) { shot.fail(result && result.error ? result.error : "python3 did not answer"); return }
      if (shot.phase === "busy") shot.showNote(result)
    }
  }

  Process {
    id: noteSaver
    property string pendingText: ""
    stdinEnabled: true
    stdout: StdioCollector { id: noteOut }
    onStarted: {
      noteSaver.write(noteSaver.pendingText)
      noteSaver.pendingText = ""
      noteSaver.stdinEnabled = false      // closes stdin: the helper reads to the end
    }
    onExited: function(code) {
      var result = shot.answer(noteOut.text)
      if (result && result.ok) return
      shot.errorText = shot.t("shotNoteFailed", [result && result.error ? result.error : "python3 did not answer"])
      if (shot.phase === "") shot.phase = "error"
    }
  }

  // The panel's background, only while the picture is drawn (the card itself belongs to the shell).
  Rectangle {
    anchors.fill: parent
    z: -1
    visible: shot.grabbing
    color: Commons.Color.popups.background
  }

  // ------------------------------------------------------------------ overlay
  Rectangle {
    id: scrim
    anchors.fill: parent
    z: 100
    visible: shot.overlayOpen
    color: Qt.rgba(Commons.Color.popups.background.r, Commons.Color.popups.background.g,
                   Commons.Color.popups.background.b, 0.88)

    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; hoverEnabled: true }

    Rectangle {
      id: card
      anchors.horizontalCenter: parent.horizontalCenter
      y: Math.max(Style.space(12), Math.round((parent.height - height) / 3))
      width: parent.width - Style.space(16)
      height: implicitHeight
      implicitHeight: body.implicitHeight + Style.space(28)
      radius: shot.corners.radius
      color: Commons.Color.popups.background
      border.width: 1
      border.color: Qt.rgba(shot.fg.r, shot.fg.g, shot.fg.b, 0.3)

      Column {
        id: body
        x: Style.space(14)
        y: Style.space(14)
        width: parent.width - Style.space(28)
        spacing: Style.space(10)

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: shot.phase === "error" ? shot.errorText : shot.t("shotSaved")
          wrapMode: Text.Wrap
          color: shot.phase === "error" ? Commons.Color.urgent : shot.fg
          font.family: shot.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
        }
        Text {
          visible: shot.phase === "note"
          width: parent.width
          textFormat: Text.PlainText
          text: shot.t("shotSavedAs", [shot.savedShown])
          wrapMode: Text.WrapAnywhere
          color: shot.dim
          font.family: shot.fontFamily
          font.pixelSize: Style.font.caption
        }
        KeyTextField {
          id: noteField
          visible: shot.phase === "note"
          width: parent.width
          maximumLength: 4000
          placeholderText: shot.t("shotNote")
          foreground: shot.fg
          radius: shot.corners.control
          font: Qt.font({ family: shot.fontFamily, pixelSize: Style.font.bodySmall })
          onAccepted: shot.finish(true)
        }
        Item {
          width: parent.width
          height: doneButton.implicitHeight
          Button {
            id: doneButton
            anchors.right: parent.right
            text: shot.t("shotDone")
            bordered: true
            radius: shot.corners.control
            foreground: shot.fg
            fontFamily: shot.fontFamily
            onClicked: shot.finish(true)
          }
        }
      }
    }
  }
}
