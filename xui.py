import json
import time
from typing import Any, Dict, List, Set

import httpx


class XUIClient:
    """
    Актуальные endpoints по wiki:
      - POST /login
      - Base: /panel/api/inbounds
          GET  /list
          POST /onlines
          GET  /getClientTraffics/:email (если понадобится)

    ВНИМАНИЕ:
    У 3x-ui /login часто принимает JSON (как в wiki). В некоторых окружениях может принимать form-data.
    Здесь сделано через JSON. Если не заходит — замените json=... на data=...
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        tg_field: str = "email",
        active_mode: str = "enabled",  # enabled | online
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.tg_field = tg_field
        self.active_mode = active_mode

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=20.0,
            follow_redirects=True,
        )

    async def close(self):
        await self.client.aclose()

    async def login(self) -> None:
        r = await self.client.post(
            "/login",
            json={"username": self.username, "password": self.password},
        )
        r.raise_for_status()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        r = await self.client.request(method, url, **kwargs)
        if r.status_code == 401:
            await self.login()
            r = await self.client.request(method, url, **kwargs)
        r.raise_for_status()
        return r

    async def list_inbounds(self) -> List[Dict[str, Any]]:
        # GET /panel/api/inbounds/list
        r = await self._request("GET", "/panel/api/inbounds/list")
        data = r.json()
        return data.get("obj", []) if isinstance(data, dict) else []

    async def online_emails(self) -> Set[str]:
        # POST /panel/api/inbounds/onlines
        r = await self._request("POST", "/panel/api/inbounds/onlines", json={})
        data = r.json()
        obj = data.get("obj", []) if isinstance(data, dict) else []
        if isinstance(obj, list):
            return {str(x) for x in obj}
        return set()

    def _extract_clients_from_inbound(self, inbound: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Обычно inbound.settings — JSON-строка, внутри есть {"clients":[...]}
        Иногда settings уже dict.
        Иногда статистика клиентов в inbound.clientStats
        """
        clients: List[Dict[str, Any]] = []

        settings = inbound.get("settings")
        if isinstance(settings, dict):
            clients = settings.get("clients", []) or []
        elif isinstance(settings, str):
            try:
                js = json.loads(settings)
                clients = js.get("clients", []) or []
            except Exception:
                clients = []

        stats = inbound.get("clientStats") or inbound.get("clientStat") or []
        if isinstance(stats, list) and isinstance(clients, list) and clients:
            stat_by_key: Dict[str, Dict[str, Any]] = {}
            for s in stats:
                key = s.get("email") or s.get("remark") or s.get("id") or s.get("uuid")
                if key is not None:
                    stat_by_key[str(key)] = s
            for c in clients:
                key = c.get("email") or c.get("remark") or c.get("id") or c.get("uuid")
                if key is not None and str(key) in stat_by_key:
                    c["_stat"] = stat_by_key[str(key)]

        return clients if isinstance(clients, list) else []

    def _client_matches_tg_id(self, client: Dict[str, Any], tg_id: int) -> bool:
        """
        В актуальном 3x-ui у клиента есть поле tgId (int64) — используем его напрямую.
        Если tg_field != tgId (например remark/email/comment), оставляем строковый поиск.
        """
        # Нормализуем имя поля
        field = (self.tg_field or "").strip()

        if field.lower() == "tgid" or field == "tgId":
            v = client.get("tgId")
            # tgId по модели int64, но на всякий случай допускаем строку
            try:
                return int(v) == int(tg_id)
            except Exception:
                return False

        # fallback: старый режим (по строковому полю)
        v = client.get(field)
        if v is None and field != "email":
            v = client.get("email")
        if v is None:
            return False
        return str(tg_id) in str(v)

    def _is_enabled_and_not_expired(self, client: Dict[str, Any]) -> bool:
        enabled = client.get("enable")
        if enabled is None:
            enabled = client.get("enabled")
        if enabled is False:
            return False

        exp = client.get("expiryTime") or client.get("expiry_time")
        if isinstance(exp, (int, float)) and exp > 0:
            now_ms = int(time.time() * 1000)
            if exp < now_ms:
                return False
        return True

    async def get_active_clients_for_tg(self, tg_id: int) -> List[Dict[str, Any]]:
        inbounds = await self.list_inbounds()

        online = set()
        if self.active_mode == "online":
            online = await self.online_emails()

        result: List[Dict[str, Any]] = []
        for inbound in inbounds:
            clients = self._extract_clients_from_inbound(inbound)
            for c in clients:
                if not self._client_matches_tg_id(c, tg_id):
                    continue

                if self.active_mode == "online":
                    email = c.get("email")
                    if not email or str(email) not in online:
                        continue
                else:
                    if not self._is_enabled_and_not_expired(c):
                        continue

                result.append(
                    {
                        "inbound_id": inbound.get("id"),
                        "port": inbound.get("port"),
                        "protocol": inbound.get("protocol"),
                        "client": c,
                    }
                )

        return result