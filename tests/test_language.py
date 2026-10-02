"""Check the user-visible language modes without touching a real photo library."""
import ast
import base64
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "organize_gallery_media.py",
    "organize_leftover_media.py",
    "organize_gallery_exact_duplicates.py",
    "repair_photo_metadata.py",
)
SUPPORT = ("config.ini", "gallery_config.py", "platform_guard.py", "terminal_language.py", "en_messages.json")
HAN = re.compile(r"[\u3400-\u9fff]")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9s3L8AAAAASUVORK5CYII="
)


@unittest.skipUnless(sys.platform == "darwin", "The scripts run on macOS only")
class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.media = self.base / "Media"
        self.batch = self.media / "Incoming" / "Stage0" / "BatchA"
        self.batch.mkdir(parents=True)
        (self.media / "Gallery" / "Main").mkdir(parents=True)
        (self.batch / "Screenshot_2024.png").write_bytes(PNG)

    def copy_for(self, language):
        target = self.base / language
        target.mkdir()
        for name in (*SUPPORT, *SCRIPTS):
            shutil.copy(ROOT / name, target / name)
        config = (target / "config.ini").read_text(encoding="utf-8")
        config = config.replace("language = zh", f"language = {language}")
        config = config.replace("backup_root = ~/Pictures/GalleryOrganizer", f"backup_root = {self.media}")
        (target / "config.ini").write_text(config, encoding="utf-8")
        return target

    def run_script(self, folder, script, *args):
        return subprocess.run(
            [sys.executable, str(folder / script), *map(str, args)],
            text=True, capture_output=True, timeout=30,
        )

    def test_english_help_and_configuration_errors(self):
        english = self.copy_for("en")
        for script in SCRIPTS:
            with self.subTest(script=script):
                result = self.run_script(english, script, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotRegex(result.stdout + result.stderr, HAN)
        config_path = english / "config.ini"
        config_path.write_text(config_path.read_text().replace("auto_from =\n", "auto_from = 20241301\n"))
        result = self.run_script(english, "repair_photo_metadata.py", "--help")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a valid date", result.stderr)
        self.assertNotRegex(result.stderr, HAN)

    def test_both_languages_plan_the_same_screenshot_destination(self):
        chinese, english = self.copy_for("zh"), self.copy_for("en")
        destination = self.media / "Gallery" / "Screenshots" / "日期未知" / "Screenshot_2024.png"
        for folder in (chinese, english):
            result = self.run_script(folder, "organize_leftover_media.py", self.batch)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(destination), result.stdout)
            if folder == english:
                displayed = result.stdout.replace(str(destination), "<destination>")
                displayed = displayed.replace(str(self.batch / "Screenshot_2024.png"), "<source>")
                self.assertNotRegex(displayed, HAN)
        self.assertTrue((self.batch / "Screenshot_2024.png").exists())
        self.assertFalse(destination.exists())

    def test_non_macos_rejection_uses_configured_language(self):
        for language in ("zh", "en"):
            folder = self.copy_for(language)
            code = (
                "import runpy,sys; sys.platform='win32'; "
                f"runpy.run_path({str(folder / SCRIPTS[0])!r}, run_name='__main__')"
            )
            result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("supports macOS only" if language == "en" else "仅支持 macOS", result.stderr)

    def test_catalog_covers_runtime_chinese_literals(self):
        catalog = json.loads((ROOT / "en_messages.json").read_text(encoding="utf-8"))
        for script in SCRIPTS:
            tree = ast.parse((ROOT / script).read_text(encoding="utf-8"))
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docstrings and HAN.search(node.value)):
                    self.assertIn(node.value, catalog, (script, node.lineno))
                    self.assertNotRegex(catalog[node.value], HAN)


if __name__ == "__main__":
    unittest.main()
