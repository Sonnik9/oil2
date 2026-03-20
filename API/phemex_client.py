import time
import json
import hmac
import hashlib
from typing import Any, Dict, Optional
import aiohttp
import asyncio

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

    async def _request(self, method: str, path: str, query: str = "", body: Optional[Dict[str, Any]] = None, timeout_sec: float = 10.0) -> Dict[str, Any]:
        expiry = int(time.time() + 60)
        body_str = json.dumps(body, separators=(',', ':')) if body else ""
        
        headers = {
            "Content-Type": "application/json",
            "x-phemex-access-token": self.api_key,
            "x-phemex-request-expiry": str(expiry),
            "x-phemex-request-signature": self._get_signature(path, query, expiry, body_str)
        }

        url = f"{self.BASE_URL}{path}{query}"
        
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
                
        if not data:
            print("[DEBUG] Все эндпоинты тикеров недоступны (Таймаут или 404).")
            return {}

        res = {}
        items = data.get("result") or data.get("data")
        
        if isinstance(items, dict):
            items_list = list(items.values())
        elif isinstance(items, list):
            items_list = items
        else:
            print(f"[DEBUG] Неизвестный формат поля result/data: {type(items)} | Сырой ответ: {str(data)[:300]}")
            return {}

        for item in items_list:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol")
            
            # ДОБАВЛЕНО: closeRp и markPriceRp согласно свежему ответу Phemex
            # price = (
                # item.get("closeRp")
                # item.get("markPriceRp") or 
                # item.get("lastPriceRp") or 
                # item.get("lastRp") or 
                # item.get("markRp")
            # )

            price = item.get("closeRp")
            
            # if not price and "last" in item:
            #     try:
            #         price = float(item["last"]) / 10000.0
            #     except (ValueError, TypeError):
            #         price = None
                    
            if sym and price:
                try:
                    res[sym] = float(price)
                except ValueError:
                    pass
                    
        if not res:
            print(f"[DEBUG] Парсинг цен не дал результатов! Символы не найдены. Сырой ответ (первые 500 символов): {str(data)[:500]}")
            
        return res
    
    async def set_leverage(self, symbol: str, pos_side: str, leverage: int) -> Dict[str, Any]:
        """
        Устанавливает плечо.
        leverage = 0 включает Cross Margin.
        """
        body = {
            "symbol": symbol,
            "posSide": pos_side,
            "leverageRr": str(leverage)
        }
        return await self._request("PUT", "/g-positions/leverage", body=body)

    async def place_order(self, symbol: str, side: str, qty: float, price: float, pos_side: str) -> Dict[str, Any]:
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