import unittest

from app import onvif

PROFILES = (
    "<trt:GetProfilesResponse>"
    '<trt:Profiles token="birds" fixed="true"><tt:Name>birds</tt:Name>'
    '<tt:VideoSourceConfiguration token="birds"><tt:Name>VSC</tt:Name></tt:VideoSourceConfiguration>'
    '<tt:VideoEncoderConfiguration token="birds"><tt:Name>VEC</tt:Name></tt:VideoEncoderConfiguration>'
    "</trt:Profiles>"
    '<trt:Profiles token="ipad" fixed="true"><tt:Name>ipad</tt:Name></trt:Profiles>'
    '<trt:Profiles token="ipad_src" fixed="true"><tt:Name>ipad_src</tt:Name></trt:Profiles>'
    "</trt:GetProfilesResponse>"
)


def patch(op, xml, audio=False, stream="ipad"):
    return onvif.patch(op, xml, "10.0.0.2:8000", stream, "iPad", "02:00:00:00:00:01", audio)


class PatchTest(unittest.TestCase):
    def test_keeps_only_this_cameras_profile(self):
        xml = patch("GetProfiles", PROFILES)
        self.assertIn('token="ipad"', xml)
        self.assertNotIn('token="birds"', xml)
        self.assertNotIn('token="ipad_src"', xml)

    def test_audio_is_described_only_when_enabled(self):
        self.assertNotIn("AAC", patch("GetProfiles", PROFILES, stream="birds"))
        xml = patch("GetProfiles", PROFILES, audio=True, stream="birds")
        self.assertIn("<tt:Encoding>AAC</tt:Encoding>", xml)
        xml = patch("GetAudioSources", "<trt:GetAudioSourcesResponse/>", audio=True)
        self.assertIn("<tt:Channels>1</tt:Channels>", xml)

    def test_adds_event_service(self):
        xml = patch("GetServices", "<tds:GetServicesResponse></tds:GetServicesResponse>")
        self.assertIn("<tds:XAddr>http://10.0.0.2:8000/onvif/event_service</tds:XAddr>", xml)
        xml = patch("GetCapabilities", "<tds:Capabilities></tds:Capabilities>")
        self.assertIn("<tt:WSPullPointSupport>true</tt:WSPullPointSupport>", xml)

    def test_network_interface_mac(self):
        xml = patch("GetNetworkInterfaces", "<tds:GetNetworkInterfacesResponse />")
        self.assertIn("<tt:HwAddress>02:00:00:00:00:01</tt:HwAddress>", xml)
        self.assertIn("<tt:Address>10.0.0.2</tt:Address>", xml)

    def test_model_and_snapshot(self):
        self.assertIn("<tds:Model>iPad</tds:Model>", patch("GetDeviceInformation", "<tds:Model>go2rtc</tds:Model>"))
        xml = patch("GetSnapshotUri", "<tt:Uri>http://10.0.0.2:8000/api/frame.jpeg?src=</tt:Uri>")
        self.assertIn("src=ipad</tt:Uri>", xml)


class EventsTest(unittest.TestCase):
    def setUp(self):
        self.state = False
        self.subs = onvif.Subscriptions(lambda: self.state, log=lambda *a: None)

    def reply(self, op, body="", query=""):
        return onvif.event_reply(op, body, query, "10.0.0.2:8000", self.subs, "ipad")

    def test_subscribe_and_pull(self):
        xml = self.reply("CreatePullPointSubscription")
        sid = xml.split("sub=")[1].split("<")[0]
        self.subs.push(True)
        xml = self.reply("PullMessages", "<Timeout>PT1S</Timeout><MessageLimit>10</MessageLimit>", f"sub={sid}")
        self.assertIn("tns1:VideoSource/MotionAlarm", xml)
        self.assertIn('Name="State" Value="true"', xml)
        self.assertIn('Name="Source" Value="ipad"', xml)

    def test_unknown_subscription_is_restored_with_current_state(self):
        self.state = True
        xml = self.reply("PullMessages", "<Timeout>PT1S</Timeout>", "sub=1234-abcd")
        self.assertIn('Value="true"', xml)

    def test_timeout_without_events(self):
        self.reply("CreatePullPointSubscription")
        xml = self.reply("PullMessages", "<Timeout>PT0.1S</Timeout>", "")
        self.assertNotIn("NotificationMessage", xml)

    def test_unsupported(self):
        self.assertIsNone(self.reply("Seek"))

    def test_operation(self):
        body = '<s:Envelope><s:Body xmlns:x="y"> <tds:GetProfiles/></s:Body></s:Envelope>'
        self.assertEqual(onvif.operation(body), "GetProfiles")


if __name__ == "__main__":
    unittest.main()
