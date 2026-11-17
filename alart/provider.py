from abc import ABC, abstractmethod
import httpx
from urllib.parse import quote
from .sanaei import legacy_server_token,_check_public_dns,validate_panel_url

def _target(server):
    url=validate_panel_url(server["api_url"])
    _check_public_dns(httpx.URL(url).host)
    return url

class ProvisioningProvider(ABC):
    @abstractmethod
    def create_service(self, *, key: str, plan: dict, server: dict, user_id: int) -> dict: ...
    @abstractmethod
    def disable_service(self, *, server: dict, remote_id: str) -> None: ...

class HTTPProvisioningProvider(ProvisioningProvider):
    """Contract for an operator-controlled provisioning API. See ARCHITECTURE.md."""
    def create_service(self, *, key, plan, server, user_id):
        with httpx.Client(timeout=20,follow_redirects=False,trust_env=False) as client:
            response = client.post(_target(server) + "/services", headers={"Authorization": "Bearer " + legacy_server_token(server), "Idempotency-Key": key}, json={"plan_id": plan["id"], "duration_hours": plan["duration_hours"], "customer_id": user_id})
            response.raise_for_status()
            data = response.json()
        if not isinstance(data.get("id"), str) or not isinstance(data.get("configuration"), str):
            raise ValueError("Invalid provisioning response")
        return data

    def disable_service(self, *, server, remote_id):
        with httpx.Client(timeout=20,follow_redirects=False,trust_env=False) as client:
            response = client.post(_target(server) + "/services/" + quote(remote_id,safe="") + "/disable", headers={"Authorization": "Bearer " + legacy_server_token(server)})
            response.raise_for_status()
