"""dmgbuild settings for Cannonball's disk image: the app on the left, an arrow,
and Applications on the right. scripts/build-macos.sh runs
`dmgbuild -s packaging/macos/dmg-settings.py -D app=APP -D background=DIR/background.png`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(defines["settings_dir"])))  # noqa: F821 - dmgbuild supplies defines
from dmg_background import APP_AT, APPLICATIONS_AT, ICON_SIZE, WINDOW  # noqa: E402

application = defines["app"]  # noqa: F821
files = [application]
symlinks = {"Applications": "/Applications"}
format = "UDZO"
background = defines["background"]  # noqa: F821 - a @2x file beside it makes it sharp on Retina
window_rect = ((100, 100), WINDOW)
default_view = "icon-view"
icon_size = ICON_SIZE
text_size = 14
icon_locations = {Path(application).name: APP_AT, "Applications": APPLICATIONS_AT}
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
