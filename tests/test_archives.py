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
            checksum = capture(root, target, bundle, {'tasks': [{'task_id': 'task'}]})
            self.assertEqual(digest(target), checksum)
            with tarfile.open(target) as archive:
                self.assertTrue(archive.getmember('checkout/link').issym())
                self.assertNotIn('checkout/secret', archive.getnames())
                self.assertEqual(b'ignored content', archive.extractfile('checkout/.ignored').read())
                history = json.load(archive.extractfile('manifest.json'))
                self.assertEqual('task', history['history']['tasks'][0]['task_id'])

    def test_lfs_pointer_blocks_incomplete_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'checkout'
            root.mkdir()
            (root / 'large.bin').write_text('version https://git-lfs.github.com/spec/v1\noid sha256:missing\n')
            bundle = base / 'history.bundle'
            bundle.write_bytes(b'bundle')
            with self.assertRaisesRegex(RuntimeError, 'LFS pointer'):
                capture(root, base / 'archive.tar.gz', bundle, {})
            self.assertTrue(root.exists())


class ExternalAttachmentArchiveTests(unittest.TestCase):
    def test_managed_bytes_stay_outside_capture_but_metadata_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            checkout = base / 'checkout'
            checkout.mkdir()
            (checkout / 'source.txt').write_text('source')
            attachments = base / 'attachments'
            attachments.mkdir()
            (attachments / 'generated-key').write_bytes(b'private original')
            bundle = base / 'history.bundle'
            bundle.write_bytes(b'history')
            target = base / 'archive.tar.gz'
            capture(checkout, target, bundle, {'attachments': [{'filename': 'notes.txt', 'attachment_id': 'a'}]})
            with tarfile.open(target) as archive:
                self.assertFalse(any('generated-key' in name or name.startswith('attachments/') for name in archive.getnames()))
                manifest = json.load(archive.extractfile('manifest.json'))
                self.assertEqual('a', manifest['history']['attachments'][0]['attachment_id'])
