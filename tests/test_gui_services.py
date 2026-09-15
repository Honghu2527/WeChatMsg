import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from gui import services


class FakeExporter:
    calls = []

    def __init__(self, database, contact, **kwargs):
        self.kwargs = kwargs
        self.__class__.calls.append((database, contact, kwargs))

    def start(self):
        self.kwargs["progress_callback"](0.5)
        self.kwargs["progress_callback"](1.0)


class ServiceTests(unittest.TestCase):
    def test_detect_accounts_adapts_existing_result(self):
        raw = SimpleNamespace(wxid="wxid_test", nick_name="Alice", wx_dir="C:/wechat", key="abc")
        accounts = services.detect_accounts(lambda: [raw])
        self.assertEqual(accounts[0].display_name, "Alice (wxid_test)")

    def test_detect_accounts_reports_empty_result(self):
        with self.assertRaisesRegex(services.GuiServiceError, "No WeChat 4.x"):
            services.detect_accounts(lambda: [])

    def test_filter_contacts_checks_remark_nickname_wxid_and_alias(self):
        contacts = [
            SimpleNamespace(remark="Work", nickname="Alice", wxid="wxid_a", alias="a"),
            SimpleNamespace(remark="Family", nickname="Bob", wxid="wxid_b", alias="builder"),
        ]
        self.assertEqual(services.filter_contacts(contacts, "BUILD"), [contacts[1]])
        self.assertEqual(services.filter_contacts(contacts, "wxid_a"), [contacts[0]])

    def test_validate_date_range_includes_entire_end_day(self):
        self.assertEqual(
            services.validate_date_range("2024-01-01", "2024-01-02"),
            ["2024-01-01 00:00:00", "2024-01-02 23:59:59"],
        )

    def test_validate_date_range_rejects_reverse_range(self):
        with self.assertRaisesRegex(services.GuiServiceError, "start date"):
            services.validate_date_range("2024-02-01", "2024-01-01")

    def test_use_prepared_database_requires_info_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(services.GuiServiceError, "info.json"):
                services.use_prepared_database(directory)

    def test_prepare_database_uses_separate_destination_and_does_not_store_key(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as workspace:
            account = services.AccountInfo("wxid_test", "Alice", source, "secret")
            calls = []

            def decryptor(key, src_dir, dest_dir):
                calls.append((key, src_dir, dest_dir))
                Path(dest_dir, "db_storage").mkdir(parents=True)

            prepared = services.prepare_database(account, workspace, decryptor=decryptor, xor_key_provider=lambda _: 123)
            self.assertNotEqual(Path(prepared.db_dir), Path(source))
            self.assertEqual(calls[0][1], source)
            info = Path(prepared.db_dir, "info.json").read_text(encoding="utf-8")
            self.assertNotIn("secret", info)
            self.assertIn('"username": "wxid_test"', info)

    def test_prepare_database_rejects_destination_inside_source(self):
        with tempfile.TemporaryDirectory() as source:
            account = services.AccountInfo("wxid_test", "Alice", source, "secret")
            workspace = str(Path(source, "generated"))
            with self.assertRaisesRegex(services.GuiServiceError, "outside the original"):
                services.prepare_database(account, workspace, decryptor=lambda *args, **kwargs: None)

    def test_export_contact_uses_adapter_and_reports_progress(self):
        FakeExporter.calls.clear()
        progress = []
        with tempfile.TemporaryDirectory() as output:
            result = services.export_contact(
                object(), SimpleNamespace(wxid="wxid_a"), ["HTML"], output,
                "2024-01-01", "2024-01-02", lambda value, text: progress.append((value, text)),
                {"HTML": FakeExporter},
            )
        self.assertTrue(FakeExporter.calls)
        self.assertEqual(FakeExporter.calls[0][2]["time_range"][1], "2024-01-02 23:59:59")
        self.assertEqual(progress[-1], (1.0, "Export completed."))
        self.assertEqual(result, str(Path(output).resolve()))


if __name__ == "__main__":
    unittest.main()
