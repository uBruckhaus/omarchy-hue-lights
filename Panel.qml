pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "I18n.js" as I18n

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
  // Settings page (gear in the header): language and the bridge. Back with
  // the button, Backspace or Esc.
  property bool settingsOpen: false
  property bool infoOpen: false
  readonly property string lang: hostWidget ? hostWidget.lang : "en"
  readonly property string languageSetting: hostWidget ? hostWidget.languageSetting : "auto"
  readonly property string systemLang: hostWidget ? hostWidget.systemLang : "en"
  function t(key, args) { return I18n.tr(lang, key, args) }
  function tn(key, n, args) { return I18n.trn(lang, key, n, args) }
  readonly property real maxRoomListHeight: Style.space(520)
  readonly property color fg: Commons.Color.popups.text
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
    if (room.kind === "zone") parts.push(t("zone"))
    parts.push(room.on ? t("ofOn", [room.on_count, total]) : tn("offLights", total))
    if (unreachable > 0) parts.push(t("unreachableN", [unreachable]))
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
  function close() { root.infoOpen = false; root.controller.hide(); root.confirmUnpair = false; root.settingsOpen = false }
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
      // The IP field gets its own keys (j/k/Backspace would drive the panel otherwise).
      blocked: manualIp.activeFocus
      onCloseRequested: { if (root.infoOpen) root.infoOpen = false; else if (root.settingsOpen) root.settingsOpen = false; else root.close() }
      onTextKey: function(text) {
        if (text === "\b" && root.infoOpen) { root.infoOpen = false; return } if (text === "\b" && root.settingsOpen) root.settingsOpen = false }
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
              : root.t("noBridge")
            detail: root.phase === "ok" ? root.t("ofOn", [Number(root.stats.on_count || 0), Number(root.stats.light_count || 0)]) : ""
            trailingControl: Component {
              // As tall as the hero text, with the gear centred on the title
              // row so it lines up with the detail pill.
              Item {
                visible: !root.settingsOpen && !root.infoOpen
                implicitWidth: gearButton.x + gearButton.width
                implicitHeight: heroTitle.implicitHeight + Style.space(2) + heroMeta.implicitHeight
                Text { id: heroTitle; visible: false; textFormat: Text.PlainText; text: "W"; font.family: root.font; font.pixelSize: Style.font.title; font.bold: true }
                Text { id: heroBody; visible: false; textFormat: Text.PlainText; text: "W"; font.family: root.font; font.pixelSize: Style.font.body; font.bold: true }
                Text { id: heroMeta; visible: false; textFormat: Text.PlainText; text: "W"; font.family: root.font; font.pixelSize: Style.font.caption; font.bold: true }
                Button {
                  id: infoButton
                  x: 0
                  y: gearButton.y
                  height: gearButton.height
                  width: height
                  iconText: "\u{F02FC}"
                  bordered: true
                  iconSize: Style.font.body
                  horizontalPadding: Style.space(4)
                  verticalPadding: Style.space(1)
                  foreground: root.fg
                  fontFamily: root.font
                  tooltipText: root.t("information")
                  onClicked: root.infoOpen = true
                }
                Button {
                  id: gearButton
                  x: infoButton.x + infoButton.width + Style.space(6)
                  y: Math.round((heroTitle.implicitHeight - height) / 2)
                  // The pill is its body text plus Style.space(4); a square gear of that height.
                  height: heroBody.implicitHeight + Style.space(4)
                  width: height
                  iconText: "\udb81\udc93"
                  bordered: true
                  iconSize: Style.font.body
                  horizontalPadding: Style.space(4)
                  verticalPadding: Style.space(1)
                  foreground: root.fg
                  fontFamily: root.font
                  tooltipText: root.t("settings")
                  onClicked: { root.confirmUnpair = false; root.settingsOpen = true }
                }
              }
            }
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: Number(root.stats.on_count || 0) > 0 ? "󰛨" : "󰌵"
                color: Number(root.stats.on_count || 0) > 0 ? Commons.Color.accent : root.dim
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
            text: root.hostWidget ? root.hostWidget.errorText() : String(root.stats.error || "")
            color: Commons.Color.urgent
            font.family: root.font
            font.pixelSize: Style.font.caption
          }


          // ---------------------------------------------------------------- information
          Column {
            visible: root.infoOpen
            width: parent.width
            spacing: Style.space(12)

            PanelSeparator { width: parent.width; foreground: root.fg }

            Row {
              spacing: Style.space(12)
              Button {
                anchors.verticalCenter: parent.verticalCenter
                text: root.t("back") + " · \u232b"
                iconText: "\udb80\udc4d"
                bordered: true
                foreground: root.fg
                fontFamily: root.font
                tooltipText: root.t("backTip")
                onClicked: root.infoOpen = false
              }
              Text {
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: root.t("information").toUpperCase()
                color: root.fg
                font.family: root.font
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1
              }
            }
            PanelSeparator { width: parent.width; foreground: root.fg }

            MorePlugins {
              width: parent.width
              separated: false
              ownId: "ubruckhaus.hue-lights"
              // The list from the marketplace's catalog when the page shows (plugin_catalog.py).
              catalogScript: decodeURIComponent(Qt.resolvedUrl("plugin_catalog.py").toString().replace(/^file:\/\//, ""))
              lang: root.lang
              fg: root.fg
              dim: root.dim
              fontFamily: root.font
              onOpened: root.close()
            }
          }

          // ---------------------------------------------------------------- settings
          Column {
            visible: root.settingsOpen
            width: parent.width
            spacing: Style.space(12)

            PanelSeparator { width: parent.width; foreground: root.fg }

            Row {
              spacing: Style.space(12)
              Button {
                anchors.verticalCenter: parent.verticalCenter
                text: root.t("back") + " · ⌫"
                iconText: "󰁍"
                bordered: true
                foreground: root.fg
                fontFamily: root.font
                tooltipText: root.t("backTip")
                onClicked: root.settingsOpen = false
              }
              Text {
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: root.t("settings").toUpperCase()
                color: root.fg
                font.family: root.font
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1
              }
            }
            PanelSeparator { width: parent.width; foreground: root.fg }

            Row {
              spacing: Style.space(10)
              Text {
                id: languageLabel
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(90)
                textFormat: Text.PlainText
                text: root.t("language")
                color: root.dim
                font.family: root.font
                font.pixelSize: Style.font.caption
              }
              CompactDropdown {
                id: languagePicker
                anchors.verticalCenter: parent.verticalCenter
                // Wide enough for the longest option, plus padding and the chevron.
                width: fitWidth
                fontSize: Style.font.caption
                rowHeight: languageLabel.implicitHeight + Style.space(6)
                popupRowHeight: languageLabel.implicitHeight + Style.space(8)
                showLabel: false
                fontFamily: root.font
                options: [{ value: "auto", label: root.t("systemLanguage", [I18n.languageName(root.systemLang)]) }]
                  .concat(I18n.LANGUAGES.map(function(l) { return { value: l.code, label: l.name } }))
                value: root.languageSetting
                // Picking cuts the value binding inside the dropdown; restore it.
                onChanged: function(picked) {
                  if (root.hostWidget) root.hostWidget.setLanguage(picked)
                  languagePicker.value = Qt.binding(function() { return root.languageSetting })
                }
              }
            }

            Item {
              visible: root.paired
              width: parent.width
              height: Math.max(bridgeText.implicitHeight, unpairButton.implicitHeight)
              Text {
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(90)
                textFormat: Text.PlainText
                text: root.t("bridge")
                color: root.dim
                font.family: root.font
                font.pixelSize: Style.font.caption
              }
              Text {
                id: bridgeText
                anchors.left: parent.left
                anchors.leftMargin: Style.space(100)
                anchors.right: unpairButton.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
                textFormat: Text.PlainText
                text: (root.bridge.name || "Hue Bridge") + (root.bridge.ip ? " · " + root.bridge.ip : "")
                color: root.fg
                font.family: root.font
                font.pixelSize: Style.font.body
              }
              Button {
                id: unpairButton
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: root.confirmUnpair ? root.t("reallyUnpair") : root.t("unpair")
                iconText: "󰌸"
                bordered: true
                foreground: root.confirmUnpair ? Commons.Color.urgent : root.fg
                fontFamily: root.font
                tooltipText: root.t("unpairTip")
                onClicked: {
                  if (root.confirmUnpair) { root.confirmUnpair = false; root.settingsOpen = false; root.send({ cmd: "unpair" }) }
                  else root.confirmUnpair = true
                }
              }
            }
          }

          // ---------------------------------------------------------------- main view
          Column {
            visible: !root.settingsOpen && !root.infoOpen
            width: parent.width
            spacing: Style.space(10)

            // ---------------------------------------------------------------- pairing
            Column {
              visible: !root.paired
              width: parent.width
              spacing: Style.space(10)

              PanelSeparator { width: parent.width; foreground: root.fg }
              PanelSectionHeader { text: root.t("pairHeader").toUpperCase(); foreground: root.fg; fontFamily: root.font }

              Text {
                textFormat: Text.PlainText
                width: parent.width
                wrapMode: Text.WordWrap
                text: root.phase === "pairing"
                  ? root.t("pressNow", [Number(root.stats.pair_remaining || 0), root.stats.pair_ip])
                  : root.t("clickPair")
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
                    text: root.t("pair")
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
                text: root.stats.discovering ? root.t("searching") : root.t("notFound")
                color: root.dim
                font.family: root.font
                font.pixelSize: Style.font.caption
              }

              Item {
                visible: root.phase !== "pairing"
                width: parent.width
                height: manualPair.implicitHeight
                // Key-events-only field: it never reads the clipboard (a Qt TextField would read
                // every foreign clipboard change, unbounded, inside the shared shell). Type the IP.
                KeyTextField {
                  id: manualIp
                  maximumLength: 64
                  anchors.left: parent.left
                  anchors.right: manualPair.left
                  anchors.rightMargin: Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter
                  placeholderText: root.t("ipPlaceholder")
                  foreground: root.fg
                  onAccepted: root.send({ cmd: "pair", ip: text })
                }
                Button {
                  id: manualPair
                  anchors.right: parent.right
                  text: root.t("pair")
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
                  text: root.t("searchAgain")
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
                  text: root.t("cancel")
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
              text: root.phase === "ok" ? root.t("noLights") : root.t("connecting")
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
                    readonly property color tint: lit ? (room.color || Commons.Color.accent) : root.fg
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
                            text: roomBlock.room.kind === "other" ? root.t("otherLights") : roomBlock.room.name
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
                          accent: roomBlock.lit ? roomBlock.tint : Commons.Color.accent
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
                            text: lightRow.light.name + (lightRow.light.reachable === false ? " · " + root.t("unreachable") : "")
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
                            accent: lightRow.lightOn ? lightRow.light.color : Commons.Color.accent
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
                            tooltipText: root.t("recallScene")
                            onClicked: root.send({ cmd: "scene", id: scene.id })
                          }
                        }
                        Button {
                          visible: parent.total > parent.limit
                          text: roomBlock.allScenes ? root.t("less") : root.t("more", [parent.total - parent.limit])
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
                text: root.t("allOff")
                iconText: "󰐥"
                bordered: true
                foreground: root.fg
                fontFamily: root.font
                enabled: Number(root.stats.on_count || 0) > 0
                tooltipText: root.t("allOffTip")
                onClicked: root.send({ cmd: "all_off" })
              }
              Button {
                text: root.t("refresh")
                iconText: "󰑐"
                bordered: true
                foreground: root.fg
                fontFamily: root.font
                onClicked: root.send({ cmd: "refresh" })
              }
            }
          }
        }
      }
    }
  }
}
