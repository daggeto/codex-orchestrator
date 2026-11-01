"""Merge eligibility inspects large indexes without retaining their full output."""
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from orchestrator.merges import MergeService
from orchestrator.tasks import Conflict


class MergeIndexTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.run_git('init', '-b', 'main')
        self.run_git('config', 'user.name', 'Merge tests')
        self.run_git('config', 'user.email', 'merge@example.com')
        for index in range(800):
            (self.root / f'{index:04d}-{"file" * 18}').touch()
        self.run_git('add', '.')
        self.run_git('commit', '-m', 'Large clean index')
        self.service = MergeService(SimpleNamespace(store=None))

    def run_git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    async def test_large_clean_index_passes_eligibility_cleanliness_check(self):
        self.assertGreater(len(self.run_git('ls-files', '--stage', '-z')), 65536)
        await self.service.clean(str(self.root))

    async def test_submodule_before_diagnostic_tail_still_blocks(self):
        # Put the gitlink before the retained 64 KiB tail of the index listing.
        sha = self.run_git('rev-parse', 'HEAD').decode().strip()
        (self.root / '000-submodule').mkdir()
        self.run_git('update-index', '--add', '--cacheinfo', f'160000,{sha},000-submodule')
        self.run_git('commit', '-m', 'Record gitlink')
        listing = self.run_git('ls-files', '--stage', '-z')
        self.assertNotIn(b'160000 ', listing[-65536:])
        with self.assertRaisesRegex(Conflict, 'Submodule merges require manual handling'):
            await self.service.clean(str(self.root))

    async def test_filename_with_mode_like_newline_does_not_block(self):
        (self.root / 'newline\n160000 ordinary-file').touch()
        self.run_git('add', '.')
        self.run_git('commit', '-m', 'Record unusual filename')
        await self.service.clean(str(self.root))
