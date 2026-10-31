"""Archive capture includes ignored files and preserves external symlinks as links."""
from pathlib import Path
import json
import tarfile
import tempfile
import unittest

from orchestrator.archives import capture, digest


class ArchiveCaptureTests(unittest.TestCase):
    def test_capture_verifies_files_history_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'checkout'
            root.mkdir()
            (root / '.ignored').write_text('ignored content')
            (root / 'untracked').write_text('uncommitted content')
            external = base / 'secret'
            external.write_text('outside checkout')
            (root / 'link').symlink_to(external)
            bundle = base / 'history.bundle'
            bundle.write_bytes(b'bundle fixture')
            target = base / 'archive.tar.gz'
            self.assertEqual(digest(target) if target.exists() else None, None)
            checksum = capture(root, target, bundle, {'tasks': [{'task_id': 'task'}]})
            self.assertEqual(digest(target), checksum)
            with tarfile.open(target) as archive:
                self.assertTrue(archive.getmember('checkout/link').issym())
                self.assertNotIn('checkout/secret', archive.getnames())
                self.assertEqual(b'ignored content', archive.extractfile('checkout/.ignored').read())
                history = json.load(archive.extractfile('manifest.json'))
                self.assertEqual('task', history['history']['tasks'][0]['task_id'])
