import sys
from pathlib import Path

# The add-on's code lives in rtsp_onvif/app, imported as the "app" package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rtsp_onvif"))
