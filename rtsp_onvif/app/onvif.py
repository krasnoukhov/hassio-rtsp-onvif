"""ONVIF on top of go2rtc: PullPoint motion events, and per-camera fixes to go2rtc's replies.

Event replies mirror scrypted-onvif-rebroadcast (tns1:VideoSource/MotionAlarm), which UniFi Protect accepts.
"""

import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

SUB_TTL = timedelta(hours=1)
MAX_SUBS = 20

NS = (
    'xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
    'xmlns:tev="http://www.onvif.org/ver10/events/wsdl" '
    'xmlns:tt="http://www.onvif.org/ver10/schema" '
    'xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" '
    'xmlns:tns1="http://www.onvif.org/ver10/topics"'
)
SOAP = "application/soap+xml; charset=utf-8"


def utcnow():
    return datetime.now(UTC)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def envelope(body):
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<s:Envelope {NS}><s:Body>{body}</s:Body></s:Envelope>'


def fault(text):
    return envelope(f"<s:Fault><s:Reason><s:Text>{text}</s:Text></s:Reason></s:Fault>")


def operation(body):
    m = re.search(r"<(?:[\w-]+:)?Body[^>]*>\s*<(?:[\w-]+:)?(\w+)", body)
    return m.group(1) if m else ""


class Subscriptions:
    def __init__(self, state, log=print):
        self.state, self.log = state, log  # state(): the current motion state
        self.subs = {}
        self.cond = threading.Condition()

    def _expire(self):
        now = utcnow()
        for sid in [s for s, v in self.subs.items() if v["expires"] < now]:
            del self.subs[sid]

    def create(self):
        with self.cond:
            self._expire()
            while len(self.subs) >= MAX_SUBS:
                del self.subs[next(iter(self.subs))]
            sid = str(uuid.uuid4())
            self.subs[sid] = {"events": [], "expires": utcnow() + SUB_TTL}
            return sid, self.subs[sid]["expires"]

    def _get(self, sid):
        # Subscriptions live in memory and Protect keeps polling its id after a restart.
        # Recreate it with the current state: closes an event left open, or reports one started meanwhile.
        if sid and sid not in self.subs:
            self.subs[sid] = {"events": [(utcnow(), self.state())], "expires": utcnow() + SUB_TTL}
            self.log("subscription restored", sid)
        return self.subs.get(sid) or next(iter(self.subs.values()), None)

    def renew(self, sid):
        with self.cond:
            expires = utcnow() + SUB_TTL
            if sub := self._get(sid):
                sub["expires"] = expires
            return expires

    def remove(self, sid):
        with self.cond:
            self.subs.pop(sid, None)

    def push(self, state):
        with self.cond:
            for sub in self.subs.values():
                sub["events"] = (sub["events"] + [(utcnow(), state)])[-50:]
            self.cond.notify_all()

    def pull(self, sid, timeout, limit):
        with self.cond:
            sub = self._get(sid)
            if sub is None:
                return [], utcnow() + SUB_TTL
            self.cond.wait_for(lambda: sub["events"], timeout)
            events, sub["events"] = sub["events"][:limit], sub["events"][limit:]
            sub["expires"] = utcnow() + SUB_TTL
            return events, sub["expires"]


def _duration(text, default):
    m = re.search(r"PT(?:(\d+)M)?(?:([\d.]+)S)?", text or "")
    if not m or not (m.group(1) or m.group(2)):
        return default
    return int(m.group(1) or 0) * 60 + float(m.group(2) or 0)


def _tag(body, name):
    m = re.search(rf"<(?:[\w-]+:)?{name}>([^<]*)<", body)
    return m and m.group(1)


def event_reply(op, body, query, host, subs, source):
    """Reply to an event-service operation, or None when it isn't supported."""
    sid = (parse_qs(query).get("sub") or [None])[0]
    if not sid:
        m = re.search(r"[?&]sub=([0-9a-f-]+)", body)
        sid = m and m.group(1)
    now = utcnow()
    if op == "GetEventProperties":
        return envelope(
            "<tev:GetEventPropertiesResponse>"
            "<tev:TopicNamespaceLocation>http://www.onvif.org/ver10/topics/topicns.xml</tev:TopicNamespaceLocation>"
            "<tev:FixedTopicSet>true</tev:FixedTopicSet>"
            '<tev:TopicSet xmlns:wstop="http://docs.oasis-open.org/wsn/t-1">'
            '<tt:RuleEngine><tt:CellMotionDetector><tt:Motion wstop:topic="true"/></tt:CellMotionDetector></tt:RuleEngine>'
            '<tt:VideoSource><tt:MotionAlarm wstop:topic="true"/></tt:VideoSource>'
            "</tev:TopicSet>"
            "<tev:TopicExpressionDialect>http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet</tev:TopicExpressionDialect>"
            "<tev:MessageContentFilterDialect>http://www.onvif.org/ver10/tev/messageContentFilter/ItemFilter</tev:MessageContentFilterDialect>"
            "</tev:GetEventPropertiesResponse>"
        )
    if op == "GetServiceCapabilities":
        return envelope(
            '<tev:GetServiceCapabilitiesResponse><tev:Capabilities WSSubscriptionPolicySupport="false" '
            'WSPullPointSupport="true" WSPausableSubscriptionManagerInterfaceSupport="false"/>'
            "</tev:GetServiceCapabilitiesResponse>"
        )
    if op == "CreatePullPointSubscription":
        sid, expires = subs.create()
        subs.log("subscription created", sid)
        return envelope(
            "<tev:CreatePullPointSubscriptionResponse><tev:SubscriptionReference>"
            f'<a:Address xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing">http://{host}/onvif/event_service?sub={sid}</a:Address>'
            f"</tev:SubscriptionReference><tev:CurrentTime>{iso(now)}</tev:CurrentTime>"
            f"<tev:TerminationTime>{iso(expires)}</tev:TerminationTime></tev:CreatePullPointSubscriptionResponse>"
        )
    if op == "PullMessages":
        timeout = min(_duration(_tag(body, "Timeout"), 2), 10)
        limit = int(_tag(body, "MessageLimit") or 10)
        events, expires = subs.pull(sid, timeout, limit)
        msgs = "".join(
            '<wsnt:NotificationMessage><wsnt:Topic Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet">'
            f'tns1:VideoSource/MotionAlarm</wsnt:Topic><wsnt:Message><tt:Message UtcTime="{iso(t)}" PropertyOperation="Changed">'
            f'<tt:Source><tt:SimpleItem Name="Source" Value="{source}"/></tt:Source>'
            f'<tt:Data><tt:SimpleItem Name="State" Value="{str(state).lower()}"/></tt:Data>'
            "</tt:Message></wsnt:Message></wsnt:NotificationMessage>"
            for t, state in events
        )
        return envelope(
            f"<tev:PullMessagesResponse><tev:CurrentTime>{iso(utcnow())}</tev:CurrentTime>"
            f"<tev:TerminationTime>{iso(expires)}</tev:TerminationTime>{msgs}</tev:PullMessagesResponse>"
        )
    if op == "Renew":
        expires = subs.renew(sid)
        return envelope(
            f"<tev:RenewResponse><tev:CurrentTime>{iso(now)}</tev:CurrentTime>"
            f"<tev:TerminationTime>{iso(expires)}</tev:TerminationTime></tev:RenewResponse>"
        )
    if op == "Unsubscribe":
        subs.remove(sid)
        return envelope("<tev:UnsubscribeResponse/>")
    if op == "SetSynchronizationPoint":
        return envelope("<tev:SetSynchronizationPointResponse/>")
    return None


# go2rtc's ONVIF never describes audio; Protect only takes it when the profile does.
# Matches the re-encoded track: AAC, 16 kHz, mono (Protect's players were silent on stereo 48 kHz).
AUDIO_SRC = (
    '<tt:AudioSourceConfiguration token="asrc_0"><tt:Name>AudioSource_0</tt:Name><tt:UseCount>1</tt:UseCount>'
    "<tt:SourceToken>audio_src_0</tt:SourceToken></tt:AudioSourceConfiguration>"
)
AUDIO_ENC = (
    '<tt:AudioEncoderConfiguration token="aenc_0"><tt:Name>AudioEncoder_0</tt:Name><tt:UseCount>1</tt:UseCount>'
    "<tt:Encoding>AAC</tt:Encoding><tt:Bitrate>64</tt:Bitrate><tt:SampleRate>16</tt:SampleRate></tt:AudioEncoderConfiguration>"
)
AUDIO_REPLIES = {
    "GetAudioSources": '<trt:AudioSources token="audio_src_0"><tt:Channels>1</tt:Channels></trt:AudioSources>',
    "GetAudioSourceConfigurations": AUDIO_SRC.replace("tt:AudioSourceConfiguration", "trt:Configurations"),
    "GetAudioEncoderConfigurations": AUDIO_ENC.replace("tt:AudioEncoderConfiguration", "trt:Configurations"),
}


def patch(op, xml, host, stream, model, mac, audio):
    """Adjust a go2rtc ONVIF reply for one camera (one ONVIF server per camera)."""
    ev = f"http://{host}/onvif/event_service"
    # go2rtc lists every stream as a profile; Protect should only see this camera's.
    xml = re.sub(
        r'<(trt:\w+) token="([^"]*)"[^>]*>.*?</\1>',
        lambda m: m.group(0) if m.group(2) == stream else "",
        xml,
        flags=re.S,
    )
    if audio:
        if op in ("GetProfiles", "GetProfile"):
            xml = xml.replace("</tt:VideoSourceConfiguration>", "</tt:VideoSourceConfiguration>" + AUDIO_SRC)
            xml = xml.replace("</tt:VideoEncoderConfiguration>", "</tt:VideoEncoderConfiguration>" + AUDIO_ENC)
        elif op in AUDIO_REPLIES:
            xml = re.sub(rf"<trt:{op}Response\s*/>", f"<trt:{op}Response>{AUDIO_REPLIES[op]}</trt:{op}Response>", xml)
    if op == "GetServices":
        xml = xml.replace(
            "</tds:GetServicesResponse>",
            "<tds:Service><tds:Namespace>http://www.onvif.org/ver10/events/wsdl</tds:Namespace>"
            f"<tds:XAddr>{ev}</tds:XAddr><tds:Version><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tds:Version>"
            "</tds:Service></tds:GetServicesResponse>",
        )
    elif op == "GetCapabilities":
        xml = xml.replace(
            "</tds:Capabilities>",
            f"<tt:Events><tt:XAddr>{ev}</tt:XAddr><tt:WSSubscriptionPolicySupport>false</tt:WSSubscriptionPolicySupport>"
            "<tt:WSPullPointSupport>true</tt:WSPullPointSupport>"
            "<tt:WSPausableSubscriptionManagerInterfaceSupport>false</tt:WSPausableSubscriptionManagerInterfaceSupport>"
            "</tt:Events></tds:Capabilities>",
        )
    elif op == "GetNetworkInterfaces":
        # go2rtc leaves this empty; Protect identifies cameras by MAC.
        ip = host.rsplit(":", 1)[0]
        xml = re.sub(
            r"<tds:GetNetworkInterfacesResponse\s*/>",
            '<tds:GetNetworkInterfacesResponse><tds:NetworkInterfaces token="eth0"><tt:Enabled>true</tt:Enabled>'
            f"<tt:Info><tt:Name>eth0</tt:Name><tt:HwAddress>{mac}</tt:HwAddress></tt:Info>"
            f"<tt:IPv4><tt:Enabled>true</tt:Enabled><tt:Config><tt:Manual><tt:Address>{ip}</tt:Address>"
            "<tt:PrefixLength>24</tt:PrefixLength></tt:Manual><tt:DHCP>false</tt:DHCP></tt:Config></tt:IPv4>"
            "</tds:NetworkInterfaces></tds:GetNetworkInterfacesResponse>",
            xml,
        )
    elif op == "GetDeviceInformation":
        xml = xml.replace("<tds:Model>go2rtc</tds:Model>", f"<tds:Model>{model}</tds:Model>")
    elif op == "GetSnapshotUri":
        # Protect sends an empty ProfileToken here.
        xml = xml.replace("src=</tt:Uri>", f"src={stream}</tt:Uri>")
    return xml
