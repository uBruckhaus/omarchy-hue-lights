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
  readonly property real maxRoomListHeight: Style.space(520)
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

  // Room type (Hue archetype) to a Material Design glyph.
  function roomIcon(room) {
    if (room.kind === "zone") return "󰌨"
    if (room.kind === "other") return "󰌵"
    // Many rooms keep the Hue app's default archetype, so the name wins when it is telling.
    var name = String(room.name || "").toLowerCase()
    var byName = [
      [/schlaf|bed|kinder|gäste|guest|nursery/, "󰋣"],
      [/küche|kueche|kitchen|ess|dining/, "󰩰"],
      [/bad|bath|wc|toilet|dusche/, "󰦠"],
      [/büro|buero|office|arbeit|studio/, "󰌢"],
      [/flur|diele|treppe|hall|stair|eingang/, "󰓍"],
      [/garage|carport/, "󰛙"],
      [/garten|terrasse|balkon|garden|terrace|balcony|patio|außen|outdoor/, "󰔱"],
      [/wohn|living|lounge|tv/, "󰒹"]
    ]
    for (var i = 0; i < byName.length; i++) if (byName[i][0].test(name)) return byName[i][1]
    switch (room.archetype) {
    case "living_room": case "lounge": case "tv": return "󰒹"
    case "kitchen": case "dining": return "󰩰"
    case "bedroom": case "kids_bedroom": case "guest_room": case "nursery": return "󰋣"
    case "bathroom": case "toilet": return "󰦠"
    case "office": case "computer": case "studio": return "󰌢"
    case "hallway": case "staircase": case "top_floor": case "upstairs": case "downstairs": return "󰓍"
    case "garage": case "carport": case "driveway": return "󰛙"
    case "garden": case "terrace": case "balcony": case "porch": case "barbecue": case "pool": case "front_door": return "󰔱"
    default: return "󰋜"
    }
  }
  function roomStatus(room, unreachable) {
    var total = room.lights.length
    var parts = []
    if (room.kind === "zone") parts.push("Zone")
    parts.push(room.on ? room.on_count + " of " + total + " on" : (total === 1 ? "Off · 1 light" : "Off · " + total + " lights"))
    if (unreachable > 0) parts.push(unreachable + " unreachable")
    return parts.join(" · ")
  }
  // Dark or light glyph on top of a light colour, whichever reads better.
  function onTint(c) {
    var luma = 0.299 * c.r + 0.587 * c.g + 0.114 * c.b
    return luma > 0.6 ? "#1a1a1a" : "#ffffff"
  }

  // The shell slider changes its value on mouse wheel; inside a scrolling list that
  // would dim (or switch on) lights while scrolling, so sliders forward the wheel here.
  function scrollRooms(wheel) {
    var f = roomScroller
    if (f.contentHeight <= f.height) return
    f.contentY = Math.max(0, Math.min(f.contentHeight - f.height, f.contentY - wheel.angleDelta.y * 0.6))
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
    contentWidth: panel.fittedContentWidth(Style.space(430))
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
              spacing: Style.space(8)

              Repeater {
                model: root.rooms.length
                delegate: Rectangle {
                  id: roomBlock
                  required property int index
                  readonly property var room: root.roomAt(index)
                  readonly property bool isOpen: root.expanded[root.roomKey(room)] === true
                  property bool allScenes: false
                  readonly property bool lit: room.on === true
                  readonly property color tint: lit ? (room.color || Color.accent) : root.fg
                  readonly property real pad: Style.space(10)
                  readonly property int unreachable: {
                    var n = 0
                    for (var i = 0; i < room.lights.length; i++) if (room.lights[i].reachable === false) n++
                    return n
                  }
                  width: roomColumn.width
                  height: cardColumn.implicitHeight + 2 * pad
                  radius: Style.cornerRadius
                  color: lit
                    ? Qt.rgba(tint.r, tint.g, tint.b, cardHover.hovered ? 0.17 : 0.12)
                    : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, cardHover.hovered ? 0.07 : 0.04)
                  border.width: 1
                  border.color: lit
                    ? Qt.rgba(tint.r, tint.g, tint.b, 0.4)
                    : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08)
                  Behavior on color { ColorAnimation { duration: 200 } }

                  HoverHandler { id: cardHover }

                  Column {
                    id: cardColumn
                    x: roomBlock.pad
                    y: roomBlock.pad
                    width: roomBlock.width - 2 * roomBlock.pad
                    spacing: Style.space(8)

                    // Header: room icon, name + status, expand chevron, switch.
                    Item {
                      width: parent.width
                      height: Math.max(badge.height, roomSwitch.implicitHeight)

                      Rectangle {
                        id: badge
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: Style.space(34)
                        height: width
                        radius: Style.cornerRadius > 0 ? width / 2 : 0
                        color: roomBlock.lit ? roomBlock.tint : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08)
                        Behavior on color { ColorAnimation { duration: 200 } }
                        Text {
                          textFormat: Text.PlainText
                          anchors.centerIn: parent
                          text: root.roomIcon(roomBlock.room)
                          color: roomBlock.lit ? root.onTint(roomBlock.tint) : root.dim
                          font.family: root.font
                          font.pixelSize: Style.font.body * 1.15
                        }
                      }

                      Column {
                        anchors.left: badge.right
                        anchors.leftMargin: Style.space(10)
                        anchors.right: chevron.left
                        anchors.rightMargin: Style.space(6)
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Style.space(1)
                        Text {
                          textFormat: Text.PlainText
                          width: parent.width
                          elide: Text.ElideRight
                          text: roomBlock.room.name
                          color: roomBlock.lit ? root.fg : Qt.lighter(root.dim, 1.15)
                          font.family: root.font
                          font.pixelSize: Style.font.body
                          font.bold: true
                        }
                        Text {
                          textFormat: Text.PlainText
                          width: parent.width
                          elide: Text.ElideRight
                          text: root.roomStatus(roomBlock.room, roomBlock.unreachable)
                          color: root.dim
                          font.family: root.font
                          font.pixelSize: Style.font.caption
                        }
                      }

                      Text {
                        id: chevron
                        textFormat: Text.PlainText
                        anchors.right: roomSwitch.left
                        anchors.rightMargin: Style.space(4)
                        anchors.verticalCenter: parent.verticalCenter
                        text: roomBlock.isOpen ? "󰅀" : "󰅂"
                        color: headerMouse.containsMouse ? root.fg : root.dim
                        font.family: root.font
                        font.pixelSize: Style.font.body
                      }

                      MouseArea {
                        id: headerMouse
                        anchors.left: parent.left
                        anchors.right: chevron.right
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.toggleExpanded(roomBlock.room)
                      }

                      ToggleSwitch {
                        id: roomSwitch
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        cursorRing: false
                        checked: roomBlock.lit
                        foreground: root.fg
                        accent: roomBlock.lit ? roomBlock.tint : Color.accent
                        onToggled: root.setRoom(roomBlock.room, { on: !roomBlock.lit })
                      }
                    }

                    // Brightness, only while the room is on.
                    Item {
                      visible: roomBlock.lit
                      width: parent.width
                      height: roomSlider.implicitHeight
                      PanelSlider {
                        id: roomSlider
                        anchors.left: parent.left
                        anchors.right: roomPct.left
                        anchors.rightMargin: Style.space(10)
                        anchors.verticalCenter: parent.verticalCenter
                        bar: root.bar
                        minimum: 1
                        maximum: 100
                        step: 5
                        fillColor: roomBlock.tint
                        value: Math.max(1, roomBlock.room.bri)
                        onMoved: function(v) { root.setRoom(roomBlock.room, { bri: v }) }
                      }
                      MouseArea {
                        anchors.fill: roomSlider
                        acceptedButtons: Qt.NoButton
                        onWheel: function(wheel) { root.scrollRooms(wheel) }
                      }
                      Text {
                        id: roomPct
                        textFormat: Text.PlainText
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        width: Style.space(38)
                        horizontalAlignment: Text.AlignRight
                        text: Math.round(roomSlider.liveValue) + "%"
                        color: root.fg
                        font.family: root.font
                        font.pixelSize: Style.font.caption
                      }
                    }

                    Rectangle {
                      visible: roomBlock.isOpen
                      width: parent.width
                      height: 1
                      color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.1)
                    }

                    Repeater {
                      model: roomBlock.isOpen ? roomBlock.room.lights.length : 0
                      delegate: Item {
                        id: lightRow
                        required property int index
                        readonly property var light: roomBlock.room.lights[index] || ({})
                        readonly property bool lightOn: light.on === true
                        width: cardColumn.width
                        height: lightSwitch.implicitHeight
                        Rectangle {
                          id: swatch
                          anchors.left: parent.left
                          anchors.leftMargin: Style.space(12)
                          anchors.verticalCenter: parent.verticalCenter
                          width: Style.space(10); height: width; radius: width / 2
                          color: lightRow.lightOn ? lightRow.light.color : "transparent"
                          opacity: lightRow.light.reachable === false ? 0.4 : 1
                          border.width: 1
                          border.color: lightRow.lightOn ? lightRow.light.color : root.dim
                        }
                        Text {
                          textFormat: Text.PlainText
                          anchors.left: swatch.right
                          anchors.leftMargin: Style.space(14)
                          anchors.right: lightSlider.left
                          anchors.rightMargin: Style.space(8)
                          anchors.verticalCenter: parent.verticalCenter
                          elide: Text.ElideRight
                          text: lightRow.light.name + (lightRow.light.reachable === false ? " · unreachable" : "")
                          color: lightRow.lightOn && lightRow.light.reachable !== false ? root.fg : root.dim
                          font.family: root.font
                          font.pixelSize: Style.font.caption
                        }
                        PanelSlider {
                          id: lightSlider
                          anchors.right: lightSwitch.left
                          anchors.rightMargin: Style.space(6)
                          anchors.verticalCenter: parent.verticalCenter
                          width: Style.space(110)
                          visible: lightRow.light.dimmable === true
                          bar: root.bar
                          minimum: 1
                          maximum: 100
                          step: 5
                          knobSize: Style.space(12)
                          fillColor: lightRow.lightOn ? lightRow.light.color : root.dim
                          value: lightRow.lightOn ? Math.max(1, lightRow.light.bri) : 1
                          opacity: lightRow.lightOn ? 1 : 0.45
                          onMoved: function(v) { root.send({ cmd: "light", id: lightRow.light.id, bri: v }) }
                        }
                        MouseArea {
                          anchors.fill: lightSlider
                          acceptedButtons: Qt.NoButton
                          onWheel: function(wheel) { root.scrollRooms(wheel) }
                        }
                        ToggleSwitch {
                          id: lightSwitch
                          anchors.right: parent.right
                          anchors.verticalCenter: parent.verticalCenter
                          cursorRing: false
                          trackHeight: Style.space(16)
                          checked: lightRow.lightOn
                          foreground: root.fg
                          accent: lightRow.lightOn ? lightRow.light.color : Color.accent
                          onToggled: root.send({ cmd: "light", id: lightRow.light.id, on: !lightRow.lightOn })
                        }
                      }
                    }
                    Flow {
                      visible: roomBlock.isOpen && roomBlock.room.scenes.length > 0
                      width: parent.width
                      topPadding: Style.space(2)
                      spacing: Style.space(6)
                      readonly property int limit: 6
                      readonly property int total: roomBlock.room.scenes.length
                      Repeater {
                        model: roomBlock.isOpen
                          ? (roomBlock.allScenes ? parent.total : Math.min(parent.total, parent.limit)) : 0
                        delegate: Button {
                          required property int index
                          readonly property var scene: roomBlock.room.scenes[index] || ({})
                          text: scene.name || ""
                          bordered: true
                          selected: scene.active === true
                          foreground: root.fg
                          fontFamily: root.font
                          fontSize: Style.font.caption
                          horizontalPadding: Style.space(8)
                          verticalPadding: Style.space(2)
                          tooltipText: "Recall scene"
                          onClicked: root.send({ cmd: "scene", id: scene.id })
                        }
                      }
                      Button {
                        visible: parent.total > parent.limit
                        text: roomBlock.allScenes ? "less" : "+" + (parent.total - parent.limit) + " more"
                        foreground: root.dim
                        fontFamily: root.font
                        fontSize: Style.font.caption
                        horizontalPadding: Style.space(8)
                        verticalPadding: Style.space(2)
                        onClicked: roomBlock.allScenes = !roomBlock.allScenes
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
