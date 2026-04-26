# VERSION 0.1.0
#
# Lambda SmartHome — proxy entre Alexa et l'instance Jeedom de l'utilisateur.
#
# Architecture : ce Lambda ne contient PAS la logique métier. Il forwarde la
# directive Alexa au smartHomeProxy.php du plugin alexaapiv2 et retourne sa
# réponse à Alexa, telle quelle. Toute la logique (Discovery, Control, ReportState)
# est en PHP côté Jeedom.
#
# Auth : le bearer token reçu d'Alexa (account-linking) est l'access_token signé
# émis par oauth/token.php — il contient l'apikey du plugin. Le proxy PHP le
# valide et utilise jeedom::apiAccess pour autoriser la requête.

CODE_VERS = 0.1

# ─── Built-in imports ───────────────────────────────────────────────────────
import json
import logging
import os
from typing import Any, Dict, Optional

# ─── 3rd-party imports ──────────────────────────────────────────────────────
import urllib3
from urllib3.util.retry import Retry

# ─── Configuration injectée par deployCode ──────────────────────────────────
# Ces 2 placeholders sont remplacés par le plugin lors du déploiement
# (voir core/api/alexa_Api/devSkill.class.php → injectCreds).
JEEDOM_URL = "{{JEEDOM_URL}}"      # ex: "https://jeedom.example.com"
JEEDOM_PROXY_PATH = "/plugins/alexaapiv2/core/php/smartHomeProxy.php"

# ─── Logger ─────────────────────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ─── HTTP client (PoolManager global, réutilisé entre invocations) ──────────
_HTTP = urllib3.PoolManager(
    timeout=urllib3.Timeout(connect=2.0, read=6.0),  # 8s total Alexa SLA
    retries=Retry(
        total=1,
        backoff_factor=0.3,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(["POST"]),
    ),
    maxsize=4,
)


def _extract_bearer(directive: Dict[str, Any]) -> Optional[str]:
    """
    Extrait l'access_token Alexa selon le namespace.

    - Alexa.Discovery   : payload.scope.token
    - Alexa.Authorization (Grant) : payload.grantee.token
    - Tout le reste     : endpoint.scope.token
    """
    header = directive.get("header", {})
    namespace = header.get("namespace", "")
    payload = directive.get("payload", {})
    if namespace == "Alexa.Discovery":
        return payload.get("scope", {}).get("token")
    if namespace == "Alexa.Authorization" and header.get("name") == "AcceptGrant":
        return payload.get("grantee", {}).get("token")
    endpoint = directive.get("endpoint", {})
    return endpoint.get("scope", {}).get("token")


def _error_response(directive: Dict[str, Any], err_type: str, message: str) -> Dict[str, Any]:
    """
    Construit une ErrorResponse Alexa v3.
    Conserve le correlationToken et l'endpointId du directive original.
    """
    in_header = directive.get("header", {})
    out_header = {
        "namespace": "Alexa",
        "name": "ErrorResponse",
        "messageId": in_header.get("messageId", ""),
        "payloadVersion": "3",
    }
    if "correlationToken" in in_header:
        out_header["correlationToken"] = in_header["correlationToken"]

    event: Dict[str, Any] = {
        "header": out_header,
        "payload": {"type": err_type, "message": message},
    }
    endpoint = directive.get("endpoint")
    if endpoint and "endpointId" in endpoint:
        event["endpoint"] = {"endpointId": endpoint["endpointId"]}
    return {"event": event}


def _accept_grant_response(directive: Dict[str, Any]) -> Dict[str, Any]:
    """Réponse positive simple à AcceptGrant — on ne stocke rien côté Lambda."""
    in_header = directive.get("header", {})
    return {
        "event": {
            "header": {
                "namespace": "Alexa.Authorization",
                "name": "AcceptGrant.Response",
                "messageId": in_header.get("messageId", ""),
                "payloadVersion": "3",
            },
            "payload": {},
        }
    }


def _forward_to_jeedom(directive: Dict[str, Any], token: str) -> Dict[str, Any]:
    """
    Forwarde le directive au smartHomeProxy.php avec Authorization: Bearer.
    Retourne le JSON Alexa renvoyé par le proxy ou une ErrorResponse.
    """
    if not JEEDOM_URL or "{{" in JEEDOM_URL:
        logger.error("JEEDOM_URL not configured")
        return _error_response(directive, "INTERNAL_ERROR", "JEEDOM_URL not configured")

    url = JEEDOM_URL.rstrip("/") + JEEDOM_PROXY_PATH
    body = json.dumps({"directive": directive}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    try:
        resp = _HTTP.request("POST", url, body=body, headers=headers)
    except urllib3.exceptions.MaxRetryError as e:
        logger.error("MaxRetryError contacting Jeedom: %s", e)
        return _error_response(directive, "BRIDGE_UNREACHABLE", "Jeedom unreachable")
    except Exception as e:
        logger.error("Unexpected error contacting Jeedom: %s", e)
        return _error_response(directive, "INTERNAL_ERROR", str(e))

    if resp.status == 401:
        return _error_response(directive, "INVALID_AUTHORIZATION_CREDENTIAL", "Token rejected")
    if resp.status >= 500:
        logger.error("Jeedom returned HTTP %s", resp.status)
        return _error_response(directive, "INTERNAL_ERROR", f"Jeedom HTTP {resp.status}")
    if resp.status != 200:
        logger.error("Jeedom returned HTTP %s body=%s", resp.status, resp.data[:200])
        return _error_response(directive, "INTERNAL_ERROR", f"Jeedom HTTP {resp.status}")

    try:
        return json.loads(resp.data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        logger.error("Invalid JSON from Jeedom: %s — body=%s", e, resp.data[:200])
        return _error_response(directive, "INTERNAL_ERROR", "Invalid JSON from Jeedom")


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Point d'entrée Lambda."""
    logger.info("Received: %s", json.dumps(event)[:500])

    directive = event.get("directive")
    if not isinstance(directive, dict):
        return _error_response({}, "INVALID_DIRECTIVE", "Missing directive")

    namespace = directive.get("header", {}).get("namespace", "")
    name = directive.get("header", {}).get("name", "")

    # AcceptGrant : pas besoin de proxy, on accuse réception
    if namespace == "Alexa.Authorization" and name == "AcceptGrant":
        token = _extract_bearer(directive)
        if not token:
            return _error_response(directive, "INVALID_AUTHORIZATION_CREDENTIAL", "Missing token")
        return _accept_grant_response(directive)

    token = _extract_bearer(directive)
    if not token:
        return _error_response(directive, "INVALID_AUTHORIZATION_CREDENTIAL", "Missing bearer token")

    return _forward_to_jeedom(directive, token)
