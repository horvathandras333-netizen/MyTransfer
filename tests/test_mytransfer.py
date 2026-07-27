from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import app as transfer
from desktop import QuickTunnel, TUNNEL_BINARY


class ShareServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.original_paths = transfer.DATA_DIR, transfer.UPLOAD_DIR, transfer.DATABASE_PATH
        self.original_password = transfer.ADMIN_PASSWORD
        transfer.DATA_DIR = self.root / "data"
        transfer.UPLOAD_DIR = transfer.DATA_DIR / "uploads"
        transfer.DATABASE_PATH = transfer.DATA_DIR / "shares.json"
        transfer.ADMIN_PASSWORD = ""

    def tearDown(self) -> None:
        transfer.DATA_DIR, transfer.UPLOAD_DIR, transfer.DATABASE_PATH = self.original_paths
        transfer.ADMIN_PASSWORD = self.original_password
        self.temporary_directory.cleanup()

    def test_creates_a_three_day_share_and_downloads_it(self) -> None:
        source = self.root / "report.txt"
        source.write_bytes(b"file contents")

        share = transfer.create_share_from_path(source, "3d")

        self.assertEqual(share["original_name"], "report.txt")
        self.assertEqual(share["size"], len(b"file contents"))
        lifetime = datetime.fromisoformat(share["expires_at"]) - datetime.fromisoformat(share["created_at"])
        self.assertAlmostEqual(lifetime.total_seconds(), timedelta(days=3).total_seconds(), delta=2)
        response = transfer.app.test_client().get(f"/download/{share['token']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"file contents")
        response.close()

    def test_only_three_or_seven_day_expiry_is_accepted(self) -> None:
        source = self.root / "report.txt"
        source.write_text("contents", encoding="utf-8")

        with self.assertRaises(ValueError):
            transfer.create_share_from_path(source, "1d")
        self.assertIsNotNone(transfer.create_share_from_path(source, "7d"))

    def test_web_upload_preserves_filename_and_rejects_other_expiry(self) -> None:
        client = transfer.app.test_client()
        response = client.post(
            "/shares",
            data={"file": (io.BytesIO(b"contents"), "my report.txt"), "expires": "3d"},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(transfer.load_shares()[0]["original_name"], "my_report.txt")
        response = client.post(
            "/shares",
            data={"file": (io.BytesIO(b"contents"), "other.txt"), "expires": "1d"},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_dashboard_can_be_password_protected(self) -> None:
        transfer.ADMIN_PASSWORD = "test-password"
        client = transfer.app.test_client()
        self.assertEqual(client.get("/").status_code, 302)
        self.assertEqual(client.post("/login", data={"password": "test-password"}).status_code, 302)
        self.assertEqual(client.get("/").status_code, 200)


class DesktopHelpersTests(unittest.TestCase):
    def test_cloudflare_url_detection_and_bundled_component(self) -> None:
        self.assertTrue(TUNNEL_BINARY.is_file())
        self.assertEqual(
            QuickTunnel.extract_url("INF Your quick Tunnel has been created! Visit it at https://plain-star.trycloudflare.com"),
            "https://plain-star.trycloudflare.com",
        )
        self.assertIsNone(QuickTunnel.extract_url("Connecting to edge"))


if __name__ == "__main__":
    unittest.main()
