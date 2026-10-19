// Generated from ~/Projects/omarchy-plugin-i18n/qml/MorePlugins.qml by
// install_more.py; edit it there, not in a plugin.
//
// "More plugins by the same author": a card for each of the author's other
// marketplace plugins (I18n.MORE_PLUGINS, this plugin left out), with its
// glyph, name and a one-line explanation. A click opens the plugin's page in
// the Omarchy Plugin Marketplace. The URLs are fixed https addresses built in
// I18n.moreUrl; nothing here is read from the network.
import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "I18n.js" as I18n

Column {
  id: more

  // This plugin's id, which gets no card.
  property string ownId: ""
  property string lang: "en"
  property color fg: Commons.Color.popups.text
  property color dim: Qt.darker(fg, 1.45)
  property string fontFamily: Style.font.family
  // A separator above the heading, for the section at the end of a page;
  // off on a page of its own, whose header has one already.
  property bool separated: true
  // Lines of a card's explanation before it is cut; more for a narrow panel.
  property int textLines: 3
  // Lines of a plugin's name; two in a narrow panel, so long names wrap.
  property int nameLines: 1
  // A card was clicked and its page is opening: the panel can close.
  signal opened()

  readonly property var plugins: I18n.MORE_PLUGINS.filter(function(p) { return p.id !== more.ownId })
  readonly property int columns: 2
  readonly property real gap: Style.space(8)

  spacing: Style.space(10)

  PanelSeparator { visible: more.separated; width: parent.width; foreground: more.fg }

  Text {
    textFormat: Text.PlainText
    text: I18n.tr(more.lang, "moreTitle").toUpperCase()
    color: more.dim
    font.family: more.fontFamily
    font.pixelSize: Style.font.caption
    font.bold: true
    font.letterSpacing: 1
  }

  Grid {
    width: parent.width
    columns: more.columns
    columnSpacing: more.gap
    rowSpacing: more.gap

    Repeater {
      model: more.plugins

      Rectangle {
        id: card

        required property var modelData
        readonly property string url: I18n.moreUrl(modelData.id)

        width: (more.width - more.gap * (more.columns - 1)) / more.columns
        height: Math.max(icon.height, textColumn.implicitHeight) + Style.space(16)
        radius: Style.space(6)
        color: cardMouse.containsMouse ? Qt.rgba(more.fg.r, more.fg.g, more.fg.b, 0.08) : Qt.rgba(more.fg.r, more.fg.g, more.fg.b, 0.03)
        border.width: 1
        border.color: cardMouse.containsMouse ? Commons.Color.accent : Qt.rgba(more.fg.r, more.fg.g, more.fg.b, 0.16)

        // The plugin's glyph on a small tile, as a preview of its bar icon.
        Rectangle {
          id: icon
          x: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(30)
          height: width
          radius: Style.space(5)
          color: Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.16)

          Text {
            anchors.centerIn: parent
            textFormat: Text.PlainText
            text: card.modelData.glyph
            color: Commons.Color.accent
            font.family: more.fontFamily
            font.pixelSize: Style.font.title
          }
        }

        Column {
          id: textColumn
          anchors.left: icon.right
          anchors.leftMargin: Style.space(8)
          anchors.right: openMark.left
          anchors.rightMargin: Style.space(4)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(2)

          Text {
            width: parent.width
            textFormat: Text.PlainText
            text: card.modelData.name
            color: more.fg
            wrapMode: more.nameLines > 1 ? Text.WordWrap : Text.NoWrap
            maximumLineCount: more.nameLines
            elide: Text.ElideRight
            font.family: more.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.bold: true
          }
          Text {
            width: parent.width
            textFormat: Text.PlainText
            text: I18n.tr(more.lang, card.modelData.key)
            color: more.dim
            wrapMode: Text.WordWrap
            maximumLineCount: more.textLines
            elide: Text.ElideRight
            font.family: more.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        // Opens in the browser.
        Text {
          id: openMark
          anchors.top: parent.top
          anchors.topMargin: Style.space(6)
          anchors.right: parent.right
          anchors.rightMargin: Style.space(7)
          textFormat: Text.PlainText
          text: "\u{F03CC}"
          color: cardMouse.containsMouse ? Commons.Color.accent : more.dim
          font.family: more.fontFamily
          font.pixelSize: Style.font.caption
        }

        MouseArea {
          id: cardMouse
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: {
            Qt.openUrlExternally(card.url)
            more.opened()
          }
        }
      }
    }
  }
}
