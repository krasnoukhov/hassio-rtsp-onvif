import unittest

from app import config


def cam(**kw):
    return {"name": "birds", "source": "rtsp://10.0.0.5/live", "onvif_port": 8000, **kw}


class ParseTest(unittest.TestCase):
    def test_defaults(self):
        opts = config.parse({"cameras": [cam()]})
        c = opts["cameras"][0]
        self.assertEqual(opts["rtsp_port"], 8564)
        self.assertEqual(opts["api_port"], 1985)
        self.assertEqual(c["mode"], "continuous")
        self.assertEqual(c["labels"], ["person", "animal"])
        self.assertEqual(c["index"], 0)

    def test_labels_are_split_and_checked(self):
        c = config.parse({"cameras": [cam(labels=" person , vehicle ")]})["cameras"][0]
        self.assertEqual(c["labels"], ["person", "vehicle"])
        with self.assertRaises(config.ConfigError):
            config.parse({"cameras": [cam(labels="person,bird")]})

    def test_rejects_bad_values(self):
        for bad in (
            cam(name="Birds"),
            cam(mode="sleepy"),
            cam(motion="ai"),
            cam(source="birds"),
            cam(mac="nope"),
            {"name": "birds", "source": "rtsp://h/x"},
        ):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                config.parse({"cameras": [bad]})

    def test_rejects_duplicates(self):
        with self.assertRaises(config.ConfigError):
            config.parse({"cameras": [cam(), cam(onvif_port=8001)]})
        with self.assertRaises(config.ConfigError):
            config.parse({"cameras": [cam(), cam(name="ipad")]})
        with self.assertRaises(config.ConfigError):
            config.parse({"cameras": [cam(onvif_port=8564)]})

    def test_needs_a_camera(self):
        with self.assertRaises(config.ConfigError):
            config.parse({"cameras": []})


class LoginTest(unittest.TestCase):
    def test_source_url_quotes_the_login(self):
        c = config.parse({"cameras": [cam(username="admin", password="p@ss:w/rd")]})["cameras"][0]
        self.assertEqual(config.source_url(c), "rtsp://admin:p%40ss%3Aw%2Frd@10.0.0.5/live")

    def test_source_url_without_login(self):
        c = config.parse({"cameras": [cam()]})["cameras"][0]
        self.assertEqual(config.source_url(c), "rtsp://10.0.0.5/live")

    def test_redact_hides_raw_and_quoted_password(self):
        opts = config.parse({"cameras": [cam(username="admin", password="p@ss:w/rd")]})
        hidden = config.secrets(opts)
        line = f"Error opening input file {config.source_url(opts['cameras'][0])} and p@ss:w/rd"
        self.assertEqual(
            config.redact(line, hidden),
            "Error opening input file rtsp://admin:***@10.0.0.5/live and ***",
        )


if __name__ == "__main__":
    unittest.main()
