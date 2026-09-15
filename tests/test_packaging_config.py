import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingConfigTests(unittest.TestCase):
    def test_spec_is_windowed_and_includes_runtime_resources(self):
        spec = (ROOT / "WeChatExporter.spec").read_text(encoding="utf-8")
        self.assertIn('name="WeChatExporter"', spec)
        self.assertIn("console=False", spec)
        self.assertIn('("exporter/resources", "exporter/resources")', spec)
        self.assertIn('("wxManager/parser/util/protocbuf", "wxManager/parser/util/protocbuf")', spec)

    def test_workflow_builds_and_uploads_expected_executable(self):
        workflow = (ROOT / ".github/workflows/build-windows-gui.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: windows-latest", workflow)
        self.assertIn('python-version: "3.11"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("PyInstaller", workflow)
        self.assertIn("dist/WeChatExporter.exe", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)


if __name__ == "__main__":
    unittest.main()
