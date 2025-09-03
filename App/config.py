import os
from dotenv import load_dotenv

load_dotenv()

NOMINATIM_URL = "http://47.84.66.152:8080/reverse"
PLATFORM_API_URL = os.getenv("PLATFORM_API_URL", "https://api.overseetracking.com:9090/WebProcessorApi.ashx")
SOAP_ENDPOINT = os.getenv("SOAP_ENDPOINT", "http://aig-navdb18-064.g.group:8447/ANRML-Live/WS/ANRML/Page/WB_Tracking_API?wsdl")
PLATFORM_USERNAME = os.getenv("PLATFORM_USERNAME", "ANRMLOG1")
PLATFORM_PASSWORD = os.getenv("PLATFORM_PASSWORD", "456789")
LANGUAGE_TYPE = os.getenv("PLATFORM_LANGUAGE_TYPE", "2B72ABC6-19D7-4653-AAEE-0BE542026D46")
USE_HTTP_WS = os.getenv("USE_HTTP_WS", "false").lower() == "true"
VERIFY_SSL = os.getenv("VERIFY_SSL", "true").lower() == "true"

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

SOAP_WSDL = f"file://{os.path.join(PROJECT_ROOT, 'Soap', 'WB_Tracking_API.xml')}"

SOAP_BASIC_USER = os.getenv("SOAP_BASIC_USER", "")
SOAP_BASIC_PASS = os.getenv("SOAP_BASIC_PASS", "")

ALLOWED_ALARMS = set(os.getenv("ALLOWED_ALARMS", "17,3,8").split(","))
HEARTBEAT_SECONDS = int(os.getenv("WS_HEARTBEAT_SECONDS", "20"))
