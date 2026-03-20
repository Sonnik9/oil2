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

    def _get_signature(self, path: str, query_no_question: str, expiry: int, body_str: str) -> str:
        # Строго как в твоем рабочем скрипте: query_no_question БЕЗ '?' в начале
        message = f"{path}{query_no_question}{expiry}{body_str}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    async def _request(self, method: str, path: str, query_no_q: str = "", body: Optional[Dict[str, Any]] = None, timeout_sec: float = 10.0) -> Dict[str, Any]:
        expiry = int(time.time() + 60)
        body_str = json.dumps(body, separators=(',', ':')) if body else ""
        
        # Получаем подпись по правильной строке (без ?)
        signature = self._get_signature(path, query_no_q, expiry, body_str)
        
        headers = {
            "Content-Type": "application/json",
            "x-phemex-access-token": self.api_key,
            "x-phemex-request-expiry": str(expiry),
            "x-phemex-request-signature": signature
        }

        # Для URL добавляем '?', если query_no_q не пустой
        query_for_url = f"?{query_no_q}" if query_no_q else ""
        url = f"{self.BASE_URL}{path}{query_for_url}"
        
        async with self.session.request(method, url, headers=headers, data=body_str if body else None, timeout=timeout_sec) as resp:
            text = await resp.text()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                raise RuntimeError(f"Bad response {resp.status}: {text}")

    async def get_all_tickers(self, timeout_sec: float = 10.0) -> Dict[str, float]:
        urls = [
            f"{self.BASE_URL}/md/v2/ticker/24hr/all", 
            f"{self.BASE_URL}/md/ticker/24hr/all"
        ]
        data = None
        for url in urls:
            try:
                async with self.session.get(url, timeout=timeout_sec) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "result" in data or "data" in data:
                            break
            except Exception:
                continue
                
        if not data: return {}
        res = {}
        items = data.get("result") or data.get("data")
        
        if isinstance(items, dict): items_list = list(items.values())
        elif isinstance(items, list): items_list = items
        else: return {}

        for item in items_list:
            if not isinstance(item, dict): continue
            sym = item.get("symbol")
            price = item.get("closeRp")
            if sym and price:
                try: res[sym] = float(price)
                except ValueError: pass
        return res
    
    async def set_leverage(self, symbol: str, pos_side: str, leverage: int, mode: str = "hedged") -> Dict[str, Any]:
        """Строго по документации и рабочему тестовому скрипту."""
        if mode == "merged":
            query_no_q = f"leverageRr={leverage}&symbol={symbol}"
        elif mode == "hedged":
            query_no_q = f"longLeverageRr={leverage}&shortLeverageRr={leverage}&symbol={symbol}"
        else:
            raise ValueError("Неверный режим! Только 'merged' или 'hedged'")

        return await self._request("PUT", "/g-positions/leverage", query_no_q=query_no_q)

    async def place_order(self, symbol: str, side: str, qty: float, price: float, pos_side: str) -> Dict[str, Any]:
        from utils import float_to_str
        
        body = {
            "symbol": symbol,
            "side": side,
            "orderQtyRq": float_to_str(qty),
            "priceRp": float_to_str(price),
            "ordType": "Limit",
            "timeInForce": "GoodTillCancel",
            "posSide": pos_side # Так как мы используем hedged mode, posSide обязателен
        }
        
        # Для ордера query пустой, данные идут в теле
        return await self._request("POST", "/g-orders", body=body)

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        # Для отмены параметры идут в query БЕЗ '?'
        query_no_q = f"orderID={order_id}&symbol={symbol}"
        return await self._request("DELETE", "/g-orders/cancel", query_no_q=query_no_q)