pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Hue Lights popup: pairing flow while no bridge is paired; afterwards one block per
// room / zone with an on/off switch and a brightness slider, expandable to its scenes
// and single lights. Repeaters are driven by counts and read the current snapshot by
// index, so a refresh never rebuilds rows (a slider drag survives every snapshot).
Panel {
  id: root
  moduleName: "ubruckhaus.hue-lights"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root
  property var stats: ({})  // pushed by BarWidget on every snapshot
  readonly property string phase: String(stats.phase || "connecting")
  readonly property bool paired: phase === "ok" || phase === "error" || phase === "connecting"
  readonly property var rooms: stats.rooms || []
  readonly property var candidates: stats.candidates || []
  readonly property var bridge: stats.bridge || ({})
  property var expanded: ({})   // room id -> true while its lights are shown
  property bool confirmUnpair: false
  readonly property real maxRoomListHeight: Style.space(420)
  readonly property color fg: Color.popups.text
  readonly property color dim: Qt.darker(fg, 1.45)
  readonly property string font: root.bar ? root.bar.fontFamily : Style.font.family

  function send(obj) { if (root.hostWidget) root.hostWidget.send(obj) }
  function roomAt(i) { return i < rooms.length ? rooms[i] : ({ lights: [], scenes: [] }) }
  function roomKey(room) { return room.id || room.kind }
  function toggleExpanded(room) {
    var next = Object.assign({}, expanded)
    next[roomKey(room)] = !next[roomKey(room)]
    expanded = next
  }
  // Rooms with a grouped_light are switched as one bridge command; "Other lights" per light.
  function setRoom(room, fields) {
    if (room.grouped_light) {
      send(Object.assign({ cmd: "group", id: room.grouped_light }, fields))
    } else {
      for (var i = 0; i < room.lights.length; i++)
        send(Object.assign({ cmd: "light", id: room.lights[i].id }, fields))
    }
  }

  function open() { root.controller.show() }
  function close() { root.controller.hide(); root.confirmUnpair = false }
  function toggle() { if (root.opened) root.close(); else root.open() }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(410))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        id: scroller
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: column
          width: scroller.width
          spacing: Style.space(10)

          PanelHero {
            foreground: root.fg
            fontFamily: root.font
            title: "Hue Lights"
            meta: root.paired
              ? (root.bridge.name || "Hue Bridge") + (root.bridge.ip ? " · " + root.bridge.ip : "")
              : "No bridge paired"
            detail: root.phase === "ok" ? Number(root.stats.on_count || 0) + " of " + Number(root.stats.light_count || 0) + " on" : ""
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: Number(root.stats.on_count || 0) > 0 ? "󰛨" : "󰌵"
                color: Number(root.stats.on_count || 0) > 0 ? Color.accent : root.dim
                font.family: root.font
                font.pixelSize: Style.font.display
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: text !== ""
            width: parent.width
            wrapMode: Text.WordWrap
            text: String(root.stats.error || "")
            color: Color.urgent
            font.family: root.font
            font.pixelSize: Style.font.caption
          }

          // ---------------------------------------------------------------- pairing
          Column {
            visible: !root.paired
            width: parent.width
            spacing: Style.space(10)

            PanelSeparator { width: parent.width; foreground: root.fg }
            PanelSectionHeader { text: "PAIR A HUE BRIDGE"; foreground: root.fg; fontFamily: root.font }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.phase === "pairing"
                ? "Press the round link button on top of the bridge now. Waiting " + Number(root.stats.pair_remaining || 0) + " s for " + root.stats.pair_ip + " …"
                : "Click Pair, then press the round link button on top of the bridge within 30 seconds."
              color: root.phase === "pairing" ? root.fg : root.dim
              font.family: root.font
              font.pixelSize: Style.font.caption
            }

            Repeater {
              model: root.phase === "pairing" ? 0 : root.candidates.length
              delegate: Item {
                id: cand
                required property int index
                readonly property var bridgeInfo: root.candidates[index] || ({})
                width: parent.width
                height: pairButton.implicitHeight
                Text {
                  textFormat: Text.PlainText
                  anchors.left: parent.left
                  anchors.right: pairButton.left
                  anchors.rightMargin: Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter
                  elide: Text.ElideRight
                  text: (cand.bridgeInfo.name || "Hue Bridge") + "  ·  " + cand.bridgeInfo.ip
                  color: root.fg
                  font.family: root.font
                  font.pixelSize: Style.font.body
                }
                Button {
                  id: pairButton
                  anchors.right: parent.right
                  text: "Pair"
                  iconText: "󰌷"
                  bordered: true
                  foreground: root.fg
                  fontFamily: root.font
                  onClicked: root.send({ cmd: "pair", ip: cand.bridgeInfo.ip })
                }
              }
            }

            Text {
              textFormat: Text.PlainText
              visible: root.phase !== "pairing" && root.candidates.length === 0
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.stats.discovering ? "Searching the network for Hue bridges …" : "No bridge found automatically. Enter its IP address:"
              color: root.dim
              font.family: root.font
              font.pixelSize: Style.font.caption
            }

            Item {
              visible: root.phase !== "pairing"
              width: parent.width
              height: manualPair.implicitHeight
              TextField {
                id: manualIp
                anchors.left: parent.left
                anchors.right: manualPair.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                placeholderText: "Bridge IP, e.g. 192.168.178.106"
                foreground: root.fg
                onAccepted: root.send({ cmd: "pair", ip: text })
              }
              Button {
                id: manualPair
                anchors.right: parent.right
                text: "Pair"
                iconText: "󰌷"
                bordered: true
                foreground: root.fg
                fontFamily: root.font
                enabled: manualIp.text.trim() !== ""
                onClicked: root.send({ cmd: "pair", ip: manualIp.text })
              }
            }

            Flow {
              width: parent.width
              spacing: Style.space(8)
              Button {
                visible: root.phase !== "pairing"
                text: "Search again"
                iconText: "󰍉"
                iconSpinning: root.stats.discovering === true
                bordered: true
                foreground: root.fg
                fontFamily: root.font
                enabled: root.stats.discovering !== true
                onClicked: root.send({ cmd: "discover" })
              }
              Button {
                visible: root.phase === "pairing"
                text: "Cancel"
                bordered: true
                foreground: root.fg
                fontFamily: root.font
                onClicked: root.send({ cmd: "cancel_pair" })
              }
            }
          }

          // ---------------------------------------------------------------- rooms
          PanelSeparator { visible: root.paired; width: parent.width; foreground: root.fg }

          Text {
            textFormat: Text.PlainText
            visible: root.paired && root.rooms.length === 0
            text: root.phase === "ok" ? "No lights on this bridge yet." : "Connecting to the bridge …"
            color: root.dim
            font.family: root.font
            font.pixelSize: Style.font.caption
          }

          Flickable {
            id: roomScroller
            visible: root.paired && root.rooms.length > 0
            width: parent.width
            height: Math.min(roomColumn.implicitHeight, root.maxRoomListHeight)
            contentWidth: width
            contentHeight: roomColumn.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height

            Column {
              id: roomColumn
              width: roomScroller.width
              spacing: Style.space(12)

              Repeater {
                model: root.rooms.length
                delegate: Column {
                  id: roomBlock
                  required property int index
                  readonly property var room: root.roomAt(index)
                  readonly property bool isOpen: root.expanded[root.roomKey(room)] === true
                  width: roomColumn.width
                  spacing: Style.space(4)

                  Item {
                    width: parent.width
                    height: roomSwitch.implicitHeight
                    Text {
                      id: chevron
                      textFormat: Text.PlainText
                      anchors.left: parent.left
                      anchors.verticalCenter: parent.verticalCenter
                      width: Style.space(18)
                      text: roomBlock.isOpen ? "󰅀" : "󰅂"
                      color: root.dim
                      font.family: root.font
                      font.pixelSize: Style.font.body
                    }
                    Text {
                      id: roomName
                      textFormat: Text.PlainText
                      anchors.left: chevron.right
                      anchors.right: roomCount.left
                      anchors.rightMargin: Style.space(8)
                      anchors.verticalCenter: parent.verticalCenter
                      elide: Text.ElideRight
                      text: roomBlock.room.name
                      color: roomBlock.room.on ? root.fg : root.dim
                      font.family: root.font
                      font.pixelSize: Style.font.body
                      font.bold: true
                    }
                    Text {
                      id: roomCount
                      textFormat: Text.PlainText
                      anchors.right: roomSwitch.left
                      anchors.rightMargin: Style.space(6)
                      anchors.verticalCenter: parent.verticalCenter
                      text: roomBlock.room.on_count + "/" + roomBlock.room.lights.length
                        + (roomBlock.room.kind === "zone" ? " · zone" : "")
                      color: root.dim
                      font.family: root.font
                      font.pixelSize: Style.font.caption
                    }
                    MouseArea {
                      anchors.left: parent.left
                      anchors.right: roomCount.right
                      anchors.top: parent.top
                      anchors.bottom: parent.bottom
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.toggleExpanded(roomBlock.room)
                    }
                    ToggleSwitch {
                      id: roomSwitch
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      checked: roomBlock.room.on === true
                      foreground: root.fg
                      onToggled: root.setRoom(roomBlock.room, { on: !roomBlock.room.on })
                    }
                  }

                  PanelSlider {
                    width: parent.width
                    bar: root.bar
                    minimum: 1
                    maximum: 100
                    step: 5
                    value: roomBlock.room.on ? Math.max(1, roomBlock.room.bri) : 1
                    opacity: roomBlock.room.on ? 1 : 0.5
                    onMoved: function(v) { root.setRoom(roomBlock.room, { bri: v }) }
                  }

                  Flow {
                    visible: roomBlock.isOpen && roomBlock.room.scenes.length > 0
                    width: parent.width
                    leftPadding: Style.space(18)
                    spacing: Style.space(6)
                    Repeater {
                      model: roomBlock.isOpen ? roomBlock.room.scenes.length : 0
                      delegate: Button {
                        required property int index
                        readonly property var scene: roomBlock.room.scenes[index] || ({})
                        text: scene.name || ""
                        iconText: "󰏘"
                        bordered: true
                        selected: scene.active === true
                        foreground: root.fg
                        fontFamily: root.font
                        fontSize: Style.font.caption
                        iconSize: Style.font.caption
                        onClicked: root.send({ cmd: "scene", id: scene.id })
                      }
                    }
                  }

                  Repeater {
                    model: roomBlock.isOpen ? roomBlock.room.lights.length : 0
                    delegate: Item {
                      id: lightRow
                      required property int index
                      readonly property var light: roomBlock.room.lights[index] || ({})
                      width: roomBlock.width
                      height: lightSwitch.implicitHeight
                      Rectangle {
                        id: swatch
                        x: Style.space(20)
                        anchors.verticalCenter: parent.verticalCenter
                        width: Style.space(10); height: width; radius: width / 2
                        color: lightRow.light.on ? lightRow.light.color : "transparent"
                        border.width: 1
                        border.color: lightRow.light.on ? lightRow.light.color : root.dim
                      }
                      Text {
                        textFormat: Text.PlainText
                        anchors.left: swatch.right
                        anchors.leftMargin: Style.space(8)
                        anchors.right: lightSlider.left
                        anchors.rightMargin: Style.space(8)
                        anchors.verticalCenter: parent.verticalCenter
                        elide: Text.ElideRight
                        text: lightRow.light.name + (lightRow.light.reachable === false ? " (unreachable)" : "")
                        color: lightRow.light.on && lightRow.light.reachable !== false ? root.fg : root.dim
                        font.family: root.font
                        font.pixelSize: Style.font.caption
                      }
                      PanelSlider {
                        id: lightSlider
                        anchors.right: lightSwitch.left
                        anchors.rightMargin: Style.space(4)
                        anchors.verticalCenter: parent.verticalCenter
                        width: Style.space(120)
                        visible: lightRow.light.dimmable === true
                        bar: root.bar
                        minimum: 1
                        maximum: 100
                        step: 5
                        knobSize: Style.space(12)
                        value: lightRow.light.on ? Math.max(1, lightRow.light.bri) : 1
                        opacity: lightRow.light.on ? 1 : 0.5
                        onMoved: function(v) { root.send({ cmd: "light", id: lightRow.light.id, bri: v }) }
                      }
                      ToggleSwitch {
                        id: lightSwitch
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        trackHeight: Style.space(18)
                        checked: lightRow.light.on === true
                        foreground: root.fg
                        onToggled: root.send({ cmd: "light", id: lightRow.light.id, on: !lightRow.light.on })
                      }
                    }
                  }
                }
              }
            }
          }

          PanelSeparator { visible: root.paired; width: parent.width; foreground: root.fg }

          Flow {
            visible: root.paired
            width: parent.width
            spacing: Style.space(8)
            Button {
              text: "All off"
              iconText: "󰐥"
              bordered: true
              foreground: root.fg
              fontFamily: root.font
              enabled: Number(root.stats.on_count || 0) > 0
              tooltipText: "Switch every light off"
              onClicked: root.send({ cmd: "all_off" })
            }
            Button {
              text: "Refresh"
              iconText: "󰑐"
              bordered: true
              foreground: root.fg
              fontFamily: root.font
              onClicked: root.send({ cmd: "refresh" })
            }
            Button {
              text: root.confirmUnpair ? "Really unpair?" : "Unpair"
              iconText: "󰌸"
              bordered: true
              foreground: root.fg
              fontFamily: root.font
              tooltipText: "Forget this bridge and its app key on this PC"
              onClicked: {
                if (root.confirmUnpair) { root.confirmUnpair = false; root.send({ cmd: "unpair" }) }
                else root.confirmUnpair = true
              }
            }
          }
        }
      }
    }
  }
}
