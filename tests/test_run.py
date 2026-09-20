"""Launcher regression tests without starting services or installing packages."""
import contextlib
import io
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run  # noqa: E402


def unused_ports(count=2):
    """Obtain distinct candidate ports while holding all reservations open."""
    with contextlib.ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for listener in sockets:
            listener.bind(("0.0.0.0", 0))
        return [listener.getsockname()[1] for listener in sockets]


class ArgumentsTest(unittest.TestCase):
    def test_legacy_start_and_serve_with_lan_ports(self):
        defaults = run.parse_args([])
        self.assertFalse(defaults.lan)
        self.assertIsNone(defaults.frontend_port)
        self.assertIsNone(defaults.backend_port)
        selected = run.parse_args([
            "serve", "--lan", "--frontend-port", "3000", "--backend-port", "9000",
        ])
        self.assertTrue(selected.lan)
        self.assertEqual((selected.frontend_port, selected.backend_port), (3000, 9000))

    def test_invalid_ports_and_help_exit_before_setup(self):
        for flag in ("--frontend-port", "--backend-port"):
            for value in ("0", "65536", "-1", "invalid"):
                with self.subTest(flag=flag, value=value), patch.object(
                    run, "select_runtime"
                ) as setup, contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as result:
                        run.main([flag, value])
                    self.assertEqual(result.exception.code, 2)
                    setup.assert_not_called()
        with patch.object(run, "select_runtime") as setup, contextlib.redirect_stdout(
            io.StringIO()
        ):
            with self.assertRaises(SystemExit) as result:
                run.main(["--help"])
            self.assertEqual(result.exception.code, 0)
            setup.assert_not_called()


class PortSelectionTest(unittest.TestCase):
    def test_occupied_port_falls_back_but_explicit_port_fails(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            occupied = listener.getsockname()[1]
            self.assertGreater(run.available_port(occupied), occupied)
            with self.assertRaises(RuntimeError):
                run.available_port(occupied, strict=True)

    def test_reserved_port_is_skipped_or_rejected_when_explicit(self):
        port = unused_ports(1)[0]
        self.assertGreater(run.available_port(port, {port}), port)
        with self.assertRaises(RuntimeError):
            run.available_port(port, {port}, strict=True)

    def test_lan_binding_detects_a_collision_on_another_interface(self):
        try:
            addresses = socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
            )
        except OSError as error:
            self.skipTest(f"Cannot discover a LAN interface: {error}")
        address = next((item[4][0] for item in addresses
                        if not item[4][0].startswith("127.")
                        and item[4][0] != "0.0.0.0"), None)
        if address is None:
            self.skipTest("No IPv4 LAN interface available")
        with socket.socket() as listener:
            try:
                listener.bind((address, 0))
            except OSError as error:
                self.skipTest(f"LAN interface is not bindable: {error}")
            listener.listen()
            occupied = listener.getsockname()[1]
            # A loopback-only probe misses the real conflict with a LAN listener.
            self.assertEqual(run.available_port(occupied, strict=True), occupied)
            with self.assertRaises(RuntimeError):
                run.available_port(occupied, host="0.0.0.0", strict=True)
            self.assertGreater(run.available_port(occupied, host="0.0.0.0"), occupied)


class LaunchTest(unittest.TestCase):
    def launch(self, arguments, environment):
        backend = Mock()
        backend.poll.return_value = 7
        frontend = Mock()
        frontend.poll.return_value = None
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(run, "select_runtime", return_value=Path(sys.executable)),
            patch.object(run, "load_env_file"),
            patch.object(run.subprocess, "Popen", side_effect=[backend, frontend]) as popen,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run.main(arguments), 7)
        # If either service exits, the other one must be shut down.
        frontend.terminate.assert_called_once()
        frontend.wait.assert_called_once()
        self.assertEqual(popen.call_count, 2)
        return popen.call_args_list

    def test_lan_explicit_ports_override_environment_and_reach_children(self):
        backend_port, frontend_port = unused_ports()
        backend, frontend = self.launch([
            "serve", "--lan", "--frontend-port", str(frontend_port),
            "--backend-port", str(backend_port),
        ], {"BACKEND_PORT": "invalid", "FRONTEND_PORT": "invalid"})
        for call, port in ((backend, backend_port), (frontend, frontend_port)):
            command = call.args[0]
            self.assertEqual(command[command.index("--port") + 1], str(port))
            self.assertEqual(command[command.index("--host") + 1], "0.0.0.0")
        self.assertIn("--strictPort", frontend.args[0])
        self.assertEqual(backend.kwargs["env"]["BACKEND_PORT"], str(backend_port))
        self.assertEqual(frontend.kwargs["env"]["FRONTEND_PORT"], str(frontend_port))
        self.assertEqual(frontend.kwargs["env"]["VITE_BACKEND_URL"],
                         f"http://127.0.0.1:{backend_port}")

    def test_non_lan_start_uses_environment_ports_and_loopback_frontend(self):
        backend_port, frontend_port = unused_ports()
        backend, frontend = self.launch([], {
            "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port),
        })
        command = frontend.args[0]
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(command[command.index("--port") + 1], str(frontend_port))
        self.assertEqual(backend.kwargs["env"]["BACKEND_PORT"], str(backend_port))

    def test_automatic_backend_leaves_explicit_frontend_port_available(self):
        frontend_port = unused_ports(1)[0]
        backend, frontend = self.launch([
            "--frontend-port", str(frontend_port),
        ], {"BACKEND_PORT": str(frontend_port)})
        self.assertGreater(int(backend.kwargs["env"]["BACKEND_PORT"]), frontend_port)
        self.assertEqual(frontend.kwargs["env"]["FRONTEND_PORT"], str(frontend_port))

    def test_conflicting_explicit_ports_do_not_launch_processes(self):
        port = unused_ports(1)[0]
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(run, "select_runtime", return_value=Path(sys.executable)),
            patch.object(run, "load_env_file"),
            patch.object(run.subprocess, "Popen") as popen,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(RuntimeError):
                run.main(["--frontend-port", str(port), "--backend-port", str(port)])
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
