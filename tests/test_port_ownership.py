"""Cross-process reservations and fail-closed backend listener verification."""
import asyncio
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orchestrator.agents.codex import CodexAgent
from orchestrator.agents.registry import AgentRegistry
from orchestrator.ports import PortReservations
from orchestrator.processes import process_owns_listener
from orchestrator.roles.loader import RoleConfig


class PortReservationTests(unittest.TestCase):
    def test_separate_cli_process_skips_the_service_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = AgentRegistry(SimpleNamespace(base_port=15000), None, None)
            registry._port_reservations = PortReservations(directory)
            port = registry._allocate_port('service')
            source = str(Path(__file__).resolve().parents[1] / 'src')
            script = '''
import sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from orchestrator.agents.registry import AgentRegistry
from orchestrator.ports import PortReservations
registry = AgentRegistry(SimpleNamespace(base_port=int(sys.argv[3])), None, None)
registry._port_reservations = PortReservations(sys.argv[2])
print(registry._allocate_port('cli'))
'''
            try:
                selected = int(subprocess.check_output([sys.executable, '-c', script, source, directory, str(port)], text=True))
                self.assertNotEqual(port, selected)
                # The child exited without explicit cleanup; the kernel released
                # its reservation, while the service still owns its original one.
                other = PortReservations(directory)
                self.assertFalse(other.reserve(port))
                self.assertTrue(other.reserve(selected))
                other.release(selected)
            finally:
                registry._release_port(port, 'service')
            self.assertTrue(registry._port_reservations.reserve(port))
            registry._port_reservations.release(port)

    def test_an_unmanaged_listener_cannot_be_reserved(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            reservations = PortReservations(directory)
            self.assertFalse(reservations.reserve(listener.getsockname()[1]))


class ListenerOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def inspect_listener(self, pid, owners, tree):
        listener = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(owners, b'')))
        processes = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(tree, b'')))
        async def spawn(command, *args, **kwargs):
            return processes if command == 'ps' else listener
        with patch('orchestrator.processes.shutil.which', return_value='/usr/sbin/lsof'), \
             patch('orchestrator.processes.asyncio.create_subprocess_exec', AsyncMock(side_effect=spawn)):
            return await process_owns_listener(pid, 15000)

    async def test_os_listener_evidence_accepts_the_spawned_process(self):
        self.assertTrue(await self.inspect_listener(1234, b'p1234\n', b'1234 100 1234\n'))
        with self.assertRaisesRegex(RuntimeError, 'another process'):
            await self.inspect_listener(5678, b'p1234\n', b'5678 100 5678\n1234 100 1234\n')

    async def test_npm_launcher_native_child_listener_is_accepted(self):
        self.assertTrue(await self.inspect_listener(
            1234, b'p2345\n', b'1234 100 1234\n2345 1234 1234\n'))

    async def test_nested_native_descendant_listener_is_accepted(self):
        self.assertTrue(await self.inspect_listener(
            1234, b'p3456\n', b'1234 100 1234\n2345 1234 1234\n3456 2345 1234\n'))

    async def test_group_membership_without_launcher_ancestry_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'another process'):
            await self.inspect_listener(1234, b'p2345\n', b'1234 100 1234\n2345 100 1234\n')

    async def test_descendant_outside_the_managed_shutdown_group_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'another process'):
            await self.inspect_listener(1234, b'p2345\n', b'1234 100 1234\n2345 1234 2345\n')

    async def test_managed_listener_does_not_mask_an_unrelated_listener(self):
        with self.assertRaisesRegex(RuntimeError, 'another process'):
            await self.inspect_listener(
                1234, b'p2345\np5678\n', b'1234 100 1234\n2345 1234 1234\n5678 100 5678\n')

    async def test_incomplete_or_cyclic_ancestry_is_rejected(self):
        for tree in (b'1234 100 1234\n', b'1234 100 1234\n2345 3456 1234\n3456 2345 1234\n'):
            with self.subTest(tree=tree), self.assertRaisesRegex(RuntimeError, 'another process'):
                await self.inspect_listener(1234, b'p2345\n', tree)

    async def test_missing_or_failed_ownership_inspection_is_rejected(self):
        with patch('orchestrator.processes.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'lsof is required'):
                await process_owns_listener(1234, 15000)
        probe = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b'', b'permission denied')))
        with patch('orchestrator.processes.shutil.which', return_value='/usr/sbin/lsof'), \
             patch('orchestrator.processes.asyncio.create_subprocess_exec', AsyncMock(return_value=probe)):
            with self.assertRaisesRegex(RuntimeError, 'Cannot verify'):
                await process_owns_listener(1234, 15000)

    async def test_exited_backend_never_accepts_an_existing_listener(self):
        agent = CodexAgent('agent', RoleConfig(name='writer'), 15000)
        agent.stop = AsyncMock()
        proc = SimpleNamespace(pid=1234, returncode=1)
        with patch('orchestrator.agents.codex.asyncio.create_subprocess_exec', AsyncMock(return_value=proc)), \
             patch('orchestrator.agents.codex.asyncio.sleep', AsyncMock()), \
             patch('orchestrator.agents.codex.process_owns_listener', AsyncMock(return_value=True)) as ownership, \
             patch('orchestrator.agents.codex.websockets.client.connect', AsyncMock()) as connect:
            with self.assertRaisesRegex(RuntimeError, 'exited before listener verification'):
                await agent.start()
            ownership.assert_not_awaited()
            connect.assert_not_awaited()
        agent.stop.assert_awaited_once()

    async def test_foreign_listener_rejects_startup_before_websocket_handshake(self):
        agent = CodexAgent('agent', RoleConfig(name='writer'), 15000)
        agent.stop = AsyncMock()
        proc = SimpleNamespace(pid=1234, returncode=None)
        with patch('orchestrator.agents.codex.asyncio.create_subprocess_exec', AsyncMock(return_value=proc)), \
             patch('orchestrator.agents.codex.asyncio.sleep', AsyncMock()), \
             patch('orchestrator.agents.codex.process_owns_listener', AsyncMock(side_effect=RuntimeError('another process'))), \
             patch('orchestrator.agents.codex.websockets.client.connect', AsyncMock()) as connect:
            with self.assertRaisesRegex(RuntimeError, 'another process'):
                await agent.start()
            connect.assert_not_awaited()
        agent.stop.assert_awaited_once()

    async def test_ownership_loss_during_handshake_closes_the_connection(self):
        agent = CodexAgent('agent', RoleConfig(name='writer'), 15000)
        agent._owns_listener = AsyncMock(side_effect=[True, False])
        ws = SimpleNamespace(close=AsyncMock())
        with patch('orchestrator.agents.codex.websockets.client.connect', AsyncMock(return_value=ws)):
            with self.assertRaisesRegex(RuntimeError, 'ownership changed'):
                await agent._connect_ws()
        ws.close.assert_awaited_once()
        self.assertIsNone(agent._ws)
        self.assertIsNone(agent._reader_task)
