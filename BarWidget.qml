pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "I18n.js" as I18n

// Bar indicator for Philips Hue. Lit bulb in the theme's green while any light is on,
// dim bulb when all are off or no bridge is paired. Left click opens the panel; right
// click switches every light off. All bridge traffic goes through scripts/hue-lights.py,
// which receives JSON commands on stdin and streams JSON snapshots on stdout.
Ui.BarWidget {
  id: root
  moduleName: "ubruckhaus.hue-lights"

  // Interface language: the system's unless one is picked in the panel's
  // settings (stored in this widget's own shell.json entry).
  readonly property string systemLang: I18n.systemLanguage(Quickshell.env("LC_ALL"), Quickshell.env("LC_MESSAGES"), Quickshell.env("LANG"))
  readonly property string languageSetting: String(root.setting("language", "auto"))
  readonly property string lang: I18n.resolve(languageSetting, systemLang)
  function t(key, args) { return I18n.tr(lang, key, args) }
  function setLanguage(code) {
    if (!root.bar || !root.bar.shell || typeof root.bar.shell.updateEntryInline !== "function") return
    var next = Object.assign({}, root.settings || {})
    if (code === "auto") delete next.language
    else next.language = code
    root.bar.shell.updateEntryInline(root.moduleName, next)
  }

  property var stats: ({})
  // Push every snapshot into the panel; never let it read from an older widget instance.
  onStatsChanged: if (panelLoader.item && "stats" in panelLoader.item) panelLoader.item.stats = root.stats
  readonly property int onCount: Number(stats.on_count || 0)
  readonly property int lightCount: Number(stats.light_count || 0)
  readonly property bool anyOn: onCount > 0
  // The theme's green (colors.toml), like the other running indicators in the bar.
  property color green: "#66c98b"
  FileView {
    path: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/omarchy/current/theme/colors.toml"
    watchChanges: true
    onFileChanged: reload()
    onLoaded: {
      var match = text().match(/^\s*green\s*=\s*"(#[0-9a-fA-F]{6})"/m)
      if (match) root.green = match[1]
    }
  }
  readonly property string scriptDir: Qt.resolvedUrl("scripts").toString().replace(/^file:\/\//, "")

  readonly property string summary: {
    if (stats.phase === "unpaired" || stats.phase === "pairing") return "Hue Lights\n" + t("tipUnpaired")
    if (stats.phase === "error") return "Hue Lights\n" + (errorText() || t("tipUnreachable"))
    var lines = ["Hue Lights · " + t("ofOn", [onCount, lightCount])]
    var rooms = stats.rooms || []
    for (var i = 0; i < rooms.length; i++) {
      if (rooms[i].kind === "room" || rooms[i].kind === "other")
        lines.push((rooms[i].on ? "● " : "○ ") + (rooms[i].kind === "other" ? t("otherLights") : rooms[i].name) + (rooms[i].on ? "  " + Math.round(rooms[i].bri) + "%" : ""))
    }
    if (anyOn) lines.push(t("rightClickOff"))
    return lines.join("\n")
  }

  // The helper's error in the interface language (older helpers send text only).
  function errorText() {
    if (!stats.error) return ""
    return stats.error_code ? t(stats.error_code, stats.error_args || []) : String(stats.error)
  }

  function send(obj) {
    if (helper.running) helper.write(JSON.stringify(obj) + "\n")
  }

  Process {
    id: helper
    command: ["python3", root.scriptDir + "/hue-lights.py"]
    running: true
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(line) {
        try { root.stats = JSON.parse(line) } catch (e) { /* partial line during shutdown */ }
      }
    }
    onRunningChanged: if (running && root.opened) Qt.callLater(function() { root.send({ cmd: "open" }) })
    onExited: restart.start()
  }
  Timer { id: restart; interval: 5000; onTriggered: helper.running = true }
  onOpenedChanged: send({ cmd: root.opened ? "open" : "closed" })

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
    if ("stats" in target) target.stats = root.stats
  }

  // Shape contract for shell summon/hide/toggle routing.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function toggle() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml") + "?load=" + Date.now()
    visible: false
    onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
  }

  Ui.WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.anyOn ? "󰛨" : "󰌵"
    dimmed: !root.anyOn
    foreground: root.anyOn ? root.green : (root.bar ? root.bar.barForeground : Commons.Color.foreground)
    tooltipText: root.opened ? "" : root.summary
    onPressed: function(code) {
      if (code === Qt.LeftButton) root.toggle()
      else if (code === Qt.RightButton && root.anyOn) root.send({ cmd: "all_off" })
    }
  }
}
