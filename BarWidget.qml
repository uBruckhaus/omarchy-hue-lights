pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

// Bar indicator for Philips Hue. Lit bulb in the accent colour while any light is on,
// dim bulb when all are off or no bridge is paired. Left click opens the panel; right
// click switches every light off. All bridge traffic goes through scripts/hue-lights.py,
// which receives JSON commands on stdin and streams JSON snapshots on stdout.
Ui.BarWidget {
  id: root
  moduleName: "ubruckhaus.hue-lights"

  property var stats: ({})
  // Push every snapshot into the panel; never let it read from an older widget instance.
  onStatsChanged: if (panelLoader.item && "stats" in panelLoader.item) panelLoader.item.stats = root.stats
  readonly property int onCount: Number(stats.on_count || 0)
  readonly property int lightCount: Number(stats.light_count || 0)
  readonly property bool anyOn: onCount > 0
  readonly property string scriptDir: Qt.resolvedUrl("scripts").toString().replace(/^file:\/\//, "")

  readonly property string summary: {
    if (stats.phase === "unpaired" || stats.phase === "pairing") return "Hue Lights\nNo bridge paired yet: click to pair"
    if (stats.phase === "error") return "Hue Lights\n" + (stats.error || "Bridge not reachable")
    var lines = ["Hue Lights · " + onCount + " of " + lightCount + " on"]
    var rooms = stats.rooms || []
    for (var i = 0; i < rooms.length; i++) {
      if (rooms[i].kind === "room" || rooms[i].kind === "other")
        lines.push((rooms[i].on ? "● " : "○ ") + rooms[i].name + (rooms[i].on ? "  " + Math.round(rooms[i].bri) + "%" : ""))
    }
    if (anyOn) lines.push("Right click: all off")
    return lines.join("\n")
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
    foreground: root.anyOn ? Color.accent : (root.bar ? root.bar.barForeground : Color.foreground)
    tooltipText: root.opened ? "" : root.summary
    onPressed: function(code) {
      if (code === Qt.LeftButton) root.toggle()
      else if (code === Qt.RightButton && root.anyOn) root.send({ cmd: "all_off" })
    }
  }
}
