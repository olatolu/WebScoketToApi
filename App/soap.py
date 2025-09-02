import logging
import requests
from zeep import Client, Settings
from zeep.transports import Transport
from requests_ntlm import HttpNtlmAuth
from App import config

logger = logging.getLogger("uvicorn.error")

def build_soap_client():
    session = requests.Session()
    if config.SOAP_BASIC_USER and config.SOAP_BASIC_PASS:
        session.auth = HttpNtlmAuth(config.SOAP_BASIC_USER, config.SOAP_BASIC_PASS)

    transport = Transport(session=session)
    settings = Settings(strict=False, xml_huge_tree=True)
    base_client = Client(wsdl=config.SOAP_WSDL, transport=transport, settings=settings)

    logger.info(f"Available bindings: {list(base_client.wsdl.bindings.keys())}")

    return base_client.create_service(
        "{urn:microsoft-dynamics-schemas/page/wb_tracking_api}WB_Tracking_API_Binding",
        config.SOAP_ENDPOINT,
    )

SOAP_CLIENT = build_soap_client()
