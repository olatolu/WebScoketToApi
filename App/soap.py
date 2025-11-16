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

_SOAP_CLIENT = None
_SOAP_CLIENT_RETRY_COUNT = 0
_MAX_SOAP_RETRIES = 3

def get_soap_client():
    """
    Lazily get or rebuild the SOAP client.
    Retries building the client up to _MAX_SOAP_RETRIES times before giving up.
    """
    global _SOAP_CLIENT, _SOAP_CLIENT_RETRY_COUNT
    
    if _SOAP_CLIENT is not None:
        return _SOAP_CLIENT
    
    if _SOAP_CLIENT_RETRY_COUNT >= _MAX_SOAP_RETRIES:
        logger.error(f"SOAP client initialization failed {_MAX_SOAP_RETRIES} times, giving up")
        raise RuntimeError("Cannot initialize SOAP client")
    
    try:
        _SOAP_CLIENT = build_soap_client()
        _SOAP_CLIENT_RETRY_COUNT = 0  # Reset on success
        logger.info("SOAP client initialized successfully")
        return _SOAP_CLIENT
    except Exception as e:
        _SOAP_CLIENT_RETRY_COUNT += 1
        logger.error(f"Failed to initialize SOAP client ({_SOAP_CLIENT_RETRY_COUNT}/{_MAX_SOAP_RETRIES}): {e}")
        _SOAP_CLIENT = None
        raise

def reset_soap_client():
    """
    Force reset the SOAP client (call this after a SOAP operation fails).
    """
    global _SOAP_CLIENT, _SOAP_CLIENT_RETRY_COUNT
    _SOAP_CLIENT = None
    _SOAP_CLIENT_RETRY_COUNT = 0
    logger.warning("SOAP client reset, will reconnect on next request")

# Deprecated: use get_soap_client() instead
SOAP_CLIENT = None