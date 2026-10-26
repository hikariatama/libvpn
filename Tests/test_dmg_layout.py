"""Round-trip Finder metadata with the hash-pinned packaging dependencies."""
import copy
import pathlib
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'Tools'))
import distribution as d

try:
    from ds_store import DSStore
except ImportError:
    DSStore = None


@unittest.skipIf(DSStore is None, 'Run with work/dmg-venv/bin/python3 after packaging setup')
class DMGLayoutTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = pathlib.Path(self.folder.name)
        (self.root / 'libvpn.app').mkdir()
        (self.root / 'Applications').symlink_to('/Applications')
        (self.root / '.background.tiff').write_bytes(b'synthetic artwork')
        self.window = dict(WindowBounds='{{160, 160}, {654, 444}}', ShowStatusBar=False,
                           ShowTabView=False, ShowToolbar=False, ShowPathbar=False, ShowSidebar=False)
        self.icons = dict(backgroundType=2, backgroundImageAlias=b'synthetic alias', iconSize=140.0,
                          textSize=12.0, labelOnBottom=True, arrangeBy='none',
                          scrollPositionX=0.0, scrollPositionY=0.0)
        self.positions = {'libvpn.app': (197, 195), 'Applications': (473, 195)}

    def save(self):
        with DSStore.open(str(self.root / '.DS_Store'), 'w+') as store:
            store['.']['bwsp'] = self.window
            store['.']['icvp'] = self.icons
            store['.']['icvl'] = (b'type', b'icnv')
            for name, point in self.positions.items():
                store[name]['Iloc'] = point

    def test_content_geometry_and_retina_validation(self):
        settings = runpy.run_path(str(d.ROOT / 'Tools/dmg_settings.py'),
                                 init_globals={'defines': {'app': str(self.root / 'libvpn.app'), 'artwork': str(d.ARTWORK)}})
        self.assertEqual(settings['window_rect'][1], (654, 444))
        self.assertEqual(settings['icon_locations'], self.positions)
        self.assertEqual((settings['icon_size'], settings['text_size'], settings['label_pos']), (140, 12, 'bottom'))
        self.save()
        with patch.object(d, 'run') as artwork:
            d.validate_layout(self.root)
            artwork.assert_called_once_with([d.ARTWORK / 'artwork', '--verify-background',
                                             self.root / '.background.tiff', d.ROOT / 'Tools/Artwork/libvpn-dmg-banner.png'])

    def test_fixed_frame_and_content_coordinates_are_preserved(self):
        for bounds in ('{{160, 160}, {654, 422}}', '{{160, 160}, {654, 466}}'):
            with self.subTest(bounds=bounds):
                self.window['WindowBounds'] = bounds
                self.save()
                with self.assertRaisesRegex(ValueError, 'window size'):
                    d.validate_layout(self.root)
        self.window['WindowBounds'] = '{{160, 160}, {654, 444}}'
        self.positions['libvpn.app'] = (197, 217)
        self.save()
        with self.assertRaisesRegex(ValueError, 'icon position'):
            d.validate_layout(self.root)

    def test_chrome_sorting_scroll_and_labels_are_rejected(self):
        for target, key, value in [(self.window, k, True) for k in
                                  ('ShowStatusBar', 'ShowTabView', 'ShowToolbar', 'ShowPathbar', 'ShowSidebar')] + [
                                  (self.icons, 'arrangeBy', 'name'), (self.icons, 'scrollPositionY', 22.0),
                                  (self.icons, 'iconSize', 112.0), (self.icons, 'textSize', 13.0),
                                  (self.icons, 'labelOnBottom', False)]:
            with self.subTest(key=key):
                previous = copy.deepcopy(target[key])
                target[key] = value
                self.save()
                with self.assertRaises(ValueError):
                    d.validate_layout(self.root)
                target[key] = previous

    def test_unexpected_visible_files_are_rejected(self):
        self.save()
        (self.root / 'extra.txt').write_text('synthetic')
        with self.assertRaisesRegex(ValueError, 'visible DMG contents'):
            d.validate_layout(self.root)
