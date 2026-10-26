"""Finder presentation written directly by dmgbuild; no GUI automation."""
from pathlib import Path

app = Path(defines['app'])
artwork = Path(defines['artwork'])
format = 'UDZO'
filesystem = 'HFS+'
files = [str(app)]
symlinks = {'Applications': '/Applications'}
# Preserve the sealed app's Finder metadata; Finder handles bundle extensions.
hide_extensions = []
icon = str(artwork / 'libvpn.icns')
background = str(artwork / 'background.tiff')
# Fixed requested frame: 422 pt artwork + a 22 pt title-bar budget.
# macOS 27 uses a 32 pt title bar; retain this frame even when it crops 10 pt.
# Icon coordinates stay relative to content, without title-bar padding.
window_rect = ((160, 160), (654, 422 + 22))
default_view = 'icon-view'
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
include_icon_view_settings = True
include_list_view_settings = False
arrange_by = None
grid_spacing = 80
icon_size = 140
text_size = 12
label_pos = 'bottom'
icon_locations = {'libvpn.app': (197, 195), 'Applications': (473, 195)}
