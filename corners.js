.pragma library
// The "Corners" setting of a plugin panel, as in Uwe's GTK apps (corners.py / theme_css.py).
//
//   "windows"  follows Hyprland's window rounding (Style.cornerRadius, the default)
//   "slight"   6 px      "rounded"  12 px      "square"  0
//
// sizes(setting, shellRadius) gives the three sizes every panel uses, plus the pill:
//   radius   cards, frames, popups, tool tips
//   control  buttons, drop-downs, text fields          = min(radius, 8)
//   small    check boxes, tracks, badges, swatches    = min(radius, 4)
//   pill     999 (round chips and switches), 0 in Square

var CHOICES = ["windows", "slight", "rounded", "square"]
var SLIGHT = 6
var ROUNDED = 12
var MAX_RADIUS = 24

function choice(value) {
  return CHOICES.indexOf(value) >= 0 ? value : "windows"
}

function radius(setting, shellRadius) {
  var picked = choice(setting)
  if (picked === "slight") return SLIGHT
  if (picked === "rounded") return ROUNDED
  if (picked === "square") return 0
  var shell = Number(shellRadius)
  return isFinite(shell) ? Math.max(0, Math.min(Math.round(shell), MAX_RADIUS)) : 0
}

function sizes(setting, shellRadius) {
  var r = radius(setting, shellRadius)
  return { radius: r, control: Math.min(r, 8), small: Math.min(r, 4), pill: r > 0 ? 999 : 0 }
}

// The four drop-down choices, translated: t is the panel's t(key, args).
function options(t, shellRadius) {
  return [
    { value: "windows", label: t("cornersWindows", [radius("windows", shellRadius)]) },
    { value: "slight", label: t("cornersSlight", [SLIGHT]) },
    { value: "rounded", label: t("cornersRounded", [ROUNDED]) },
    { value: "square", label: t("cornersSquare") }
  ]
}
