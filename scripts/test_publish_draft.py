import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('publish_draft', Path(__file__).with_name('publish-draft.py'))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
SHA = 'a' * 40


class FakeGitHub:
    def __init__(self, *, draft=True, sha=SHA, existing=True, tag_sha=SHA):
        self.repository = 'hikariatama/libvpn'
        self.existing = existing
        self.sha = sha
        self.tag_sha = tag_sha
        self.release = {'id': 1, 'tag_name': 'v0.0.1', 'draft': draft, 'upload_url': 'https://uploads.github.com/assets{?name}'}
        self.assets = [{'id': 2, 'name': 'stale-package.zip'}] if existing else []
        self.mutations = []

    def pages(self, path):
        return iter(([self.release] if self.existing else []) if path == '/releases' else list(self.assets))

    def request(self, method, path, data=None, *, upload=None):
        if method == 'GET':
            if path == '/releases/1':
                return self.release
            if '/tags/' in path:
                return {'object': {'sha': self.tag_sha, 'type': 'commit'}} if self.existing else None
            return {'object': {'sha': self.sha, 'type': 'commit'}}
        self.mutations.append((method, path, data))
        if method == 'DELETE':
            self.assets = []
            return None
        if upload is not None:
            asset = {'id': 3, 'name': upload.name, 'size': upload.stat().st_size,
                     'digest': 'sha256:' + hashlib.sha256(upload.read_bytes()).hexdigest()}
            self.assets.append(asset)
            return asset
        if path in ('/releases/1', '/releases'):
            self.release.update(data)
            return self.release
        return {}


class DraftReleaseTests(unittest.TestCase):
    def test_missing_package_fails_before_publishing(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                release.collect_artifacts(Path(root), ['missing.zip'])

    def test_draft_rebuild_keeps_tag_and_replaces_assets(self):
        github = FakeGitHub()
        with tempfile.TemporaryDirectory() as root:
            package = Path(root) / 'app.zip'
            package.write_bytes(b'verified build')
            files = release.collect_artifacts(Path(root), ['app.zip'])
            notes = Path(__file__).with_name('release-notes.md').read_text().strip()
            draft, _ = release.publish(github, '0.0.1', SHA, files, notes=notes)
        self.assertTrue(draft['draft'])
        self.assertEqual(draft['name'], 'libvpn v0.0.1')
        self.assertEqual(draft['body'], notes)
        self.assertEqual({a['name'] for a in github.assets}, {'app.zip', 'SHA256SUMS.txt'})
        self.assertFalse(any(method == 'PATCH' and '/git/refs/' in path for method, path, _ in github.mutations))
        self.assertTrue(any(method == 'DELETE' for method, _, _ in github.mutations))

    def test_first_release_starts_as_draft(self):
        github = FakeGitHub(existing=False)
        with tempfile.TemporaryDirectory() as root:
            package = Path(root) / 'app.zip'
            package.write_bytes(b'first build')
            files = release.collect_artifacts(Path(root), ['app.zip'])
            current, _ = release.publish(github, '0.0.1', SHA, files)
        self.assertTrue(current['draft'])
        self.assertTrue(any(method == 'POST' and path == '/releases' for method, path, _ in github.mutations))
        self.assertFalse(any(method == 'DELETE' for method, _, _ in github.mutations))

    def test_published_release_is_rejected_before_any_mutation(self):
        github = FakeGitHub(draft=False)
        before = list(github.assets)
        with self.assertRaisesRegex(RuntimeError, 'immutable'):
            release.publish(github, '0.0.1', SHA, {})
        self.assertEqual(github.mutations, [])
        self.assertEqual(github.assets, before)

    def test_existing_tag_cannot_be_moved_even_for_a_draft(self):
        github = FakeGitHub(tag_sha='b' * 40)
        with self.assertRaisesRegex(RuntimeError, 'another commit'):
            release.publish(github, '0.0.1', SHA, {})
        self.assertEqual(github.mutations, [])

    def test_concurrent_publication_stops_before_asset_replacement(self):
        github = FakeGitHub()
        original = github.request
        def request(method, path, data=None, **kwargs):
            if method == 'GET' and path == '/releases/1':
                github.release['draft'] = False
            return original(method, path, data, **kwargs)
        github.request = request
        with self.assertRaisesRegex(RuntimeError, 'no longer a draft'):
            release.publish(github, '0.0.1', SHA, {})
        self.assertEqual(github.mutations, [])

    def test_annotated_tag_must_resolve_to_the_requested_commit(self):
        github = FakeGitHub()
        original = github.request
        def request(method, path, data=None, **kwargs):
            if method == 'GET' and path == '/git/ref/tags/v0.0.1':
                return {'object': {'type': 'tag', 'sha': 'c' * 40}}
            if method == 'GET' and path == '/git/tags/' + 'c' * 40:
                return {'object': {'type': 'commit', 'sha': 'b' * 40}}
            return original(method, path, data, **kwargs)
        github.request = request
        with self.assertRaisesRegex(RuntimeError, 'another commit'):
            release.publish(github, '0.0.1', SHA, {})
        self.assertEqual(github.mutations, [])

    def test_stale_build_cannot_replace_current_draft(self):
        github = FakeGitHub(sha='b' * 40)
        with self.assertRaises(RuntimeError):
            release.publish(github, '0.0.1', SHA, {})
        self.assertEqual(github.mutations, [])


if __name__ == '__main__':
    unittest.main()
