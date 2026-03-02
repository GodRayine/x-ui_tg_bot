import json
import time
from typing import Any, Dict, List, Set

import httpx


class XUIClient:
    """
    3x-ui REST client с поддержкой:
      - web_basepath (WEBBASEPATH / secret path), напр. "/EmptyArclight_panel" или ""
      - api_prefix   (где живёт API), напр. "/panel/api" (обычно) или "/api" (за reverse proxy)
      - tgId         (штатное поле Telegram ID у клиента)
      - active_mode  ("online" через /inbounds/onlines или "enabled" по enable/expiryTime)

    Итоговые пути:
      login:   {web_basepath}/login
      list:    {web_basepath}{api_prefix}/inbounds/list
      onlines: {web_basepath}{api_prefix}/inbounds/onlines
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        tg_field: str = "tgId",
        active_mode: str = "enabled",   # enabled | online
        web_basepath: str = "",         # например "/EmptyArclight_panel" или ""
        api_prefix: str = "/panel/api", # "/panel/api" или "/api"
        verify_tls: bool = True,
        timeout: float = 20.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password

        self.tg_field = (tg_field or "tgId").strip()
        self.active_mode = (active_mode or "enabled").strip().lower()

        self.web_basepath = self._norm_path(web_basepath)
        self.api_prefix = self._norm_path(api_prefix) or "/panel/api"

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=True,
            verify=verify_tls,
        )

    # ---- helpers ----
    @staticmethod
    def _norm_path(p: str) -> str:
        p = (p or "").strip()
        if not p:
            return ""
        if not p.startswith("/"):
            p = "/" + p
        return p.rstrip("/")

    def _api(self, suffix: str) -> str:
        """
        Собирает: {web_basepath}{api_prefix}{suffix}
        suffix должен начинаться с /
        """
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        return f"{self.web_basepath}{self.api_prefix}{suffix}"

    def _login_url(self) -> str:
        return f"{self.web_basepath}/login" if self.web_basepath else "/login"

    # ---- http ----
    async def close(self) -> None:
        await self.client.aclose()

    async def login(self) -> None:
        # По актуальной документации 3x-ui логин часто принимает JSON.
        # Если у вас ожидается form-data, замените json=... на data=...
        r = await self.client.post(
            self._login_url(),
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

    # ---- API ----
    async def list_inbounds(self) -> List[Dict[str, Any]]:
        r = await self._request("GET", self._api("/inbounds/list"))
        data = r.json()
        # обычно {"success": true, "obj": [...]}
        return data.get("obj", []) if isinstance(data, dict) else []

    async def online_emails(self) -> Set[str]:
        r = await self._request("POST", self._api("/inbounds/onlines"), json={})
        data = r.json()
        obj = data.get("obj", []) if isinstance(data, dict) else []
        return {str(x) for x in obj} if isinstance(obj, list) else set()

    # ---- parsing / filtering ----
    def _extract_clients_from_inbound(self, inbound: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        inbound["settings"] часто JSON-строка вида {"clients":[...]}
        иногда settings уже dict.
        """
        settings = inbound.get("settings")
        clients: List[Dict[str, Any]] = []

        if isinstance(settings, dict):
            clients = settings.get("clients", []) or []
        elif isinstance(settings, str):
            try:
                js = json.loads(settings)
                clients = js.get("clients", []) or []
            except Exception:
                clients = []

        return clients if isinstance(clients, list) else []

    def _client_matches_tg_id(self, client: Dict[str, Any], tg_id: int) -> bool:
        """
        В актуальном 3x-ui штатное поле Telegram ID: tgId (int64).
        Если tg_field = tgId — сравниваем числом.
        Иначе fallback: строковый поиск по выбранному полю.
        """
        field = (self.tg_field or "").strip()

        if field.lower() == "tgid" or field == "tgId":
            try:
                return int(client.get("tgId")) == int(tg_id)
            except Exception:
                return False

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
        """
        Возвращает список найденных клиентов (с контекстом inbound),
        отфильтрованных по tgId и "активности".
        """
        inbounds = await self.list_inbounds()

        online: Set[str] = set()
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
