"""Launcher regression tests without starting services or installing packages."""
import contextlib
import errno
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
        selected = run.parse_args([
            "--frontend", "3001", "--backend", "9001", "--host", "localhost",
        ])
        self.assertEqual((selected.frontend_port, selected.backend_port), (3001, 9001))
        self.assertEqual(selected.host, "localhost")

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


class HostSelectionTest(unittest.TestCase):
    def test_resolves_hostname_to_one_numeric_address_and_socket_family(self):
        records = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ]
        with patch.object(run.socket, "getaddrinfo", return_value=records) as lookup:
            self.assertEqual(run.resolve_host("localhost"), (socket.AF_INET6, "::1"))
        lookup.assert_called_once_with("localhost", 0, type=socket.SOCK_STREAM)

    def test_invalid_hosts_report_resolution_failure(self):
        with patch.object(run.socket, "getaddrinfo", side_effect=socket.gaierror("unknown")):
            with self.assertRaisesRegex(RuntimeError, "Cannot resolve bind address"):
                run.resolve_host("missing")
        with self.assertRaisesRegex(RuntimeError, "must not be empty"):
            run.resolve_host(" ")

    def test_only_bound_interfaces_are_advertised(self):
        with patch.object(run, "lan_addresses", return_value=["192.168.1.2", "192.168.56.1"]):
            self.assertEqual(run.lan_urls_for_host("0.0.0.0", 5173), [
                "http://192.168.1.2:5173", "http://192.168.56.1:5173",
            ])
            self.assertEqual(run.lan_urls_for_host("192.168.56.1", 5173), [
                "http://192.168.56.1:5173",
            ])
            self.assertEqual(run.lan_urls_for_host("127.0.0.1", 5173), [])
            self.assertEqual(run.lan_urls_for_host("::1", 5173), [])
            self.assertEqual(run.lan_urls_for_host("::", 5173), [])
        self.assertEqual(run.service_url("0.0.0.0", 8000), "http://127.0.0.1:8000")
        self.assertEqual(run.service_url("192.168.56.1", 8000), "http://192.168.56.1:8000")
        self.assertEqual(run.service_url("::", 8000), "http://[::1]:8000")
        self.assertEqual(run.service_url("::1", 8000), "http://[::1]:8000")


class PortSelectionTest(unittest.TestCase):
    def test_ipv6_probe_uses_requested_socket_family_and_interface(self):
        probe = Mock()
        socket_context = Mock()
        socket_context.__enter__ = Mock(return_value=probe)
        socket_context.__exit__ = Mock(return_value=False)
        with patch.object(run.socket, "socket", return_value=socket_context) as create:
            self.assertEqual(run.available_port(8000, host="::1", family=socket.AF_INET6), 8000)
        create.assert_called_once_with(socket.AF_INET6, socket.SOCK_STREAM)
        probe.bind.assert_called_once_with(("::1", 8000))

    def test_nonlocal_bind_address_fails_without_scanning_other_ports(self):
        probe = Mock()
        probe.bind.side_effect = OSError(errno.EADDRNOTAVAIL, "address is not assigned")
        socket_context = Mock()
        socket_context.__enter__ = Mock(return_value=probe)
        socket_context.__exit__ = Mock(return_value=False)
        with patch.object(run.socket, "socket", return_value=socket_context) as create:
            with self.assertRaisesRegex(RuntimeError, "Cannot bind to 192.168.99.99:8000"):
                run.available_port(8000, host="192.168.99.99")
        create.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind.assert_called_once_with(("192.168.99.99", 8000))

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
    def launch(self, arguments, environment, addresses=None):
        backend = Mock()
        backend.poll.return_value = 7
        frontend = Mock()
        frontend.poll.return_value = None
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(run, "select_runtime", return_value=Path(sys.executable)),
            patch.object(run, "load_env_file"),
            patch.object(run, "lan_addresses", return_value=addresses or []),
            patch.object(run.subprocess, "Popen", side_effect=[backend, frontend]) as popen,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(run.main(arguments), 7)
        # If either service exits, the other one must be shut down.
        frontend.terminate.assert_called_once()
        frontend.wait.assert_called_once()
        self.assertEqual(popen.call_count, 2)
        self.launch_output = output.getvalue()
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

    def test_default_start_uses_environment_ports_and_shared_wildcard_host(self):
        backend_port, frontend_port = unused_ports()
        backend, frontend = self.launch([], {
            "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port),
        }, addresses=["192.168.1.2", "192.168.56.1"])
        command = frontend.args[0]
        self.assertEqual(command[command.index("--host") + 1], "0.0.0.0")
        self.assertEqual(command[command.index("--port") + 1], str(frontend_port))
        self.assertEqual(backend.kwargs["env"]["BACKEND_PORT"], str(backend_port))
        for address in ("192.168.1.2", "192.168.56.1"):
            self.assertIn(f"LAN:      http://{address}:{frontend_port}", self.launch_output)
            self.assertIn(f"LAN API:  http://{address}:{backend_port}/docs", self.launch_output)
            self.assertIn(f"http://{address}:{frontend_port}", backend.kwargs["env"]["CORS_ORIGINS"])

    def test_host_precedence_and_loopback_override(self):
        backend_port, frontend_port = unused_ports()
        ports = {"BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port)}
        for arguments, environment_host, expected in (
            ([], "127.0.0.1", "127.0.0.1"),
            (["--lan"], "127.0.0.1", "0.0.0.0"),
            (["--lan", "--host", "127.0.0.1"], "0.0.0.0", "127.0.0.1"),
        ):
            with self.subTest(arguments=arguments, environment_host=environment_host):
                calls = self.launch(arguments, {**ports, "BACKEND_HOST": environment_host},
                                    addresses=["192.168.1.2"])
                for call in calls:
                    command = call.args[0]
                    self.assertEqual(command[command.index("--host") + 1], expected)
                    self.assertEqual(call.kwargs["env"]["BACKEND_HOST"], expected)
                if expected == "127.0.0.1":
                    self.assertNotIn("LAN:", self.launch_output)
                    self.assertNotIn("Open a LAN URL", self.launch_output)

    def test_specific_adapter_is_used_by_proxy_and_cors(self):
        with patch.object(run, "available_port", side_effect=[8000, 5173]):
            backend, frontend = self.launch(["--host", "192.168.56.1"], {
                "CORS_ORIGINS": "https://custom.example",
            }, addresses=["192.168.1.2", "192.168.56.1"])
        self.assertEqual(frontend.kwargs["env"]["VITE_BACKEND_URL"], "http://192.168.56.1:8000")
        self.assertEqual(backend.kwargs["env"]["FRONTEND_URL"], "http://192.168.56.1:5173")
        self.assertEqual(backend.kwargs["env"]["CORS_ORIGINS"], ",".join([
            "https://custom.example", "http://localhost:5173", "http://127.0.0.1:5173",
            "http://192.168.56.1:5173",
        ]))
        self.assertIn("LAN:      http://192.168.56.1:5173", self.launch_output)
        self.assertNotIn("192.168.1.2", self.launch_output)

    def test_ipv6_loopback_launch_uses_family_and_reachable_proxy_url(self):
        with patch.object(run, "available_port", side_effect=[8000, 5173]) as ports:
            backend, frontend = self.launch(["--host", "::1"], {})
        for call in ports.call_args_list:
            self.assertEqual(call.kwargs["family"], socket.AF_INET6)
            self.assertEqual(call.kwargs["host"], "::1")
        for call in (backend, frontend):
            command = call.args[0]
            self.assertEqual(command[command.index("--host") + 1], "::1")
        self.assertEqual(frontend.kwargs["env"]["VITE_BACKEND_URL"], "http://[::1]:8000")
        self.assertIn("Frontend: http://[::1]:5173", self.launch_output)
        self.assertNotIn("LAN:", self.launch_output)

    def test_explicit_cors_wildcard_is_preserved(self):
        environment = {"CORS_ORIGINS": " * "}
        run.merge_cors_origins(environment, 5173, ["http://192.168.1.2:5173"])
        self.assertEqual(environment, {"CORS_ORIGINS": " * "})

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
