import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons as Commons

// See-through plugin popups, set in Theme Manager → Settings → "Plugin popups
// follow window style". That switch writes ~/.config/hypr/theme-manager-glass.lua;
// without it nothing changes here. With it the popup card follows Omarchy's
// window style (Theme Manager → Font & effects, or Omarchy's own menu):
// glassy = 0.6 (Hyprland blurs behind, Omarchy's keyboard-panel layer rule),
// transparent = 0.9, opaque = solid. Omarchy's own popups are left alone.
//
// Usage, inside the KeyboardPanel / PopupCard:
//   PopupGlass { inside: keyCatcher; active: panel.open }
// `inside` is any item of the card's content; the card is found from it.
// A plugin that paints its own card sets paintCard: false and uses `alpha`.
// The style is read again each time the popup opens.
Scope {
  id: glass

  property Item inside: null
  property bool active: false
  property bool paintCard: true

  readonly property string home: Quickshell.env("HOME")
  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME") || (home + "/.config")
  readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || (home + "/.local/state")
  readonly property string togglesDir: stateHome + "/omarchy/toggles/hypr"

  property bool enabled: false
  property bool glassy: false
  property bool opaque: false
  readonly property real alpha: !enabled || opaque ? 1 : glassy ? 0.6 : 0.9

  property Item card: null

  function findCard() {
    for (var it = inside ? inside.parent : null; it; it = it.parent) {
      if (it.borderSpec !== undefined && it.padding !== undefined && it.color !== undefined && it.radius !== undefined)
        return it
    }
    return null
  }

  function refresh() {
    if (!card && paintCard) card = findCard()
    gate.reload(); glassyFile.reload(); opaqueFile.reload()
  }

  onActiveChanged: if (active) refresh()
  onInsideChanged: card = null
  Component.onCompleted: refresh()

  FileView { id: gate; path: glass.configHome + "/hypr/theme-manager-glass.lua"; printErrors: false
    onLoaded: glass.enabled = true; onLoadFailed: glass.enabled = false }
  FileView { id: glassyFile; path: glass.togglesDir + "/window-glassy.lua"; printErrors: false
    onLoaded: glass.glassy = true; onLoadFailed: glass.glassy = false }
  FileView { id: opaqueFile; path: glass.togglesDir + "/window-opaque.lua"; printErrors: false
    onLoaded: glass.opaque = true; onLoadFailed: glass.opaque = false }

  // Only while see-through: otherwise the card keeps its own colour binding.
  Binding {
    target: glass.card
    property: "color"
    when: glass.paintCard && glass.card !== null && glass.alpha < 1
    value: Qt.rgba(Commons.Color.popups.background.r, Commons.Color.popups.background.g,
                   Commons.Color.popups.background.b, Commons.Color.popups.background.a * glass.alpha)
    restoreMode: Binding.RestoreBindingOrValue
  }
}
