import unittest
from pathlib import Path

import yaml

ADDON = Path(__file__).resolve().parents[1] / "rtsp_onvif"


def keys(schema):
    return {k.rstrip("?") for k in schema}


class TranslationsTest(unittest.TestCase):
    def test_every_option_is_translated(self):
        schema = yaml.safe_load((ADDON / "config.yaml").read_text())["schema"]
        camera = keys(schema["cameras"][0])
        files = sorted((ADDON / "translations").glob("*.yaml"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(language=path.stem):
                conf = yaml.safe_load(path.read_text())["configuration"]
                self.assertEqual(set(conf), keys(schema))
                self.assertEqual(set(conf["cameras"]["fields"]), camera)
                for option in [*conf.values(), *conf["cameras"]["fields"].values()]:
                    self.assertTrue(option.get("name"))


if __name__ == "__main__":
    unittest.main()
