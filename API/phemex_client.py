import time
import json
import hmac
import hashlib
from typing import Any, Dict, Optional
import aiohttp

class PhemexPrivateClient:
    BASE_URL = "https://api.phemex.com"

    def __init__(self, api_key: str, api_secret: str, session: aiohttp.ClientSession):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = session

    def _get_signature(self, path: str, query: str, expiry: int, body: str) -> str:
        message = f"{path}{query}{expiry}{body}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    async def _request(self, method: str, path: str, query: str = "", body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        expiry = int(time.time() + 60)
        body_str = json.dumps(body, separators=(',', ':')) if body else ""
        
        headers = {
            "Content-Type": "application/json",
            "x-phemex-access-token": self.api_key,
            "x-phemex-request-expiry": str(expiry),
            "x-phemex-request-signature": self._get_signature(path, query, expiry, body_str)
        }

        url = f"{self.BASE_URL}{path}{query}"
        
        async with self.session.request(method, url, headers=headers, data=body_str if body else None) as resp:
            text = await resp.text()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                raise RuntimeError(f"Bad response {resp.status}: {text}")

    async def get_all_tickers(self) -> Dict[str, float]:
        """Единый запрос для получения всех цен (V2 endpoint)."""
        url = f"{self.BASE_URL}/md/v2/ticker/24hr/all"
        async with self.session.get(url) as resp:
            data = await resp.json()
            res = {}
            items = data.get("result", data.get("data", []))
            for item in items:
                sym = item.get("symbol")
                price = item.get("lastPriceRp") or item.get("lastRp") or item.get("last")
                if sym and price:
                    # Если возвращается старый формат 'last', возможно потребуется деление на 10^4
                    # Но lastPriceRp всегда отдает float-совместимую строку
                    res[sym] = float(price)
            return res

    async def place_order(self, symbol: str, side: str, qty: float, price: float, pos_side: str) -> Dict[str, Any]:
        """
        Выставляет лимитный ордер. 
        side: "Buy" или "Sell", pos_side: "Long" или "Short"
        """
        from utils import float_to_str
        
        body = {
            "symbol": symbol,
            "side": side,
            "orderQtyRq": float_to_str(qty),
            "priceRp": float_to_str(price),
            "ordType": "Limit",
            "timeInForce": "GoodTillCancel",
            "posSide": pos_side
        }
        return await self._request("POST", "/g-orders", body=body)

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        query = f"?symbol={symbol}&orderID={order_id}"
        return await self._request("DELETE", "/g-orders/cancel", query=query)