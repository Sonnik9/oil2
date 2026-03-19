import asyncio
from datetime import datetime
from typing import Dict, Optional

import aiohttp
import pytz

from API.symbols import PhemexSymbols
from API.phemex_client import PhemexPrivateClient
from c_log import UnifiedLogger
from consts import TIME_ZONE
from utils import round_step, async_append_to_json

TZ = pytz.timezone(TIME_ZONE)
ERRORS_FILE = "oi_errors.json"

class OpenInterestScreener:
    def __init__(self, config: dict, logger: UnifiedLogger):
        self.config = config
        self.log = logger
        
        # Конфигурация
        self.pos_side = self.config.get("pos_side", "LONG").upper()
        self.order_interval = self.config.get("order_request_interval", 1)
        self.price_interval = self.config.get("price_request_interval", 5)
        self.indentation_pct = self.config.get("order_indentation_pct", 5)
        self.iteration_interval = self.config.get("iteration_interval", 600)
        self.blacklist = set(self.config.get("black_list", []))
        
        self.api_key = self.config.get("api_key", "")
        self.api_secret = self.config.get("api_secret", "")
        
        # Стейт
        self.prices: Dict[str, float] = {}
        self.symbols_api = PhemexSymbols()
        self.client: Optional[PhemexPrivateClient] = None
        self._running = False
        self._price_task: Optional[asyncio.Task] = None

    async def _update_prices_loop(self):
        """Фоновое обновление цен."""
        while self._running:
            if self.client:
                try:
                    tickers = await self.client.get_all_tickers()
                    if tickers:
                        self.prices = tickers
                except Exception as e:
                    self.log.error(f"Failed to fetch prices: {e}")
            await asyncio.sleep(self.price_interval)
            
    async def start(self):
        self._running = True
        
        async with aiohttp.ClientSession() as session:
            self.client = PhemexPrivateClient(self.api_key, self.api_secret, session)
            self._price_task = asyncio.create_task(self._update_prices_loop())
            
            while self._running:
                self.log.info("Начинается новая итерация проверки Open Interest...")
                symbols_list = await self.symbols_api.get_all(quote="USDT", only_active=True)
                filtered_symbols = [s for s in symbols_list if s.symbol not in self.blacklist]
                
                for sym_info in filtered_symbols:
                    if not self._running:
                        break
                        
                    await self._process_symbol(sym_info)
                    await asyncio.sleep(self.order_interval)
                    
                self.log.info(f"Итерация завершена. Сон {self.iteration_interval} сек.")
                if self._running:
                    await asyncio.sleep(self.iteration_interval)

    async def stop(self):
        self.log.info("Остановка скринера...")
        self._running = False
        if self._price_task:
            self._price_task.cancel()
        await self.symbols_api.aclose()

    async def _process_symbol(self, sym_info):
        symbol = sym_info.symbol
        price = self.prices.get(symbol)
        if not price:
            return  # Цены еще не подгрузились
            
        # Логика смещения цены (indentation)
        if self.pos_side == "LONG":
            order_price = price * (1 - self.indentation_pct / 100.0)
            side = "Buy"
        else:
            order_price = price * (1 + self.indentation_pct / 100.0)
            side = "Sell"
            
        phemex_pos_side = self.pos_side.capitalize() # "Long" или "Short"
            
        # Спецификация контракта
        raw = sym_info.raw_data
        tick_size = float(raw.get("tickSize", "0.001"))
        lot_size = float(raw.get("lotSize", "0.01"))
        
        order_price = round_step(order_price, tick_size)
        
        # Минимальный объем. Для Phemex лучше ставить эквивалент ~$6 чтобы пройти мин. номинал
        min_notional = 6.0 
        qty = max(lot_size, min_notional / order_price)
        qty = round_step(qty, lot_size)
        
        try:
            resp = await self.client.place_order(symbol, side, qty, order_price, phemex_pos_side)
            code = resp.get("code", -1)
            
            if code == 0:
                # Все отлично - ордер выставился, сразу отменяем
                order_id = resp.get("data", {}).get("orderID")
                self.log.info(f"[{symbol}] Успех. Открытый интерес в норме. Отменяем ордер {order_id}...")
                if order_id:
                    cancel_resp = await self.client.cancel_order(symbol, order_id)
                    if cancel_resp.get("code", -1) != 0:
                        self.log.error(f"[{symbol}] КРИТИЧЕСКАЯ ОШИБКА: Не удалось отменить ордер! {cancel_resp}")
            else:
                # Ошибка (потенциально лимит OI)
                msg = resp.get("msg", "")
                self.log.warning(f"[{symbol}] Ошибка выставления: {code} - {msg}")
                await self._save_error(symbol, code, msg)
                
        except Exception as e:
            self.log.error(f"[{symbol}] Исключение при отправке: {e}")
            await self._save_error(symbol, -1, str(e))

    async def _save_error(self, symbol: str, code: int, msg: str):
        data = {
            "symbol": symbol,
            "timestamp": datetime.now(TZ).isoformat(),
            "code": code,
            "msg": msg
        }
        await async_append_to_json(ERRORS_FILE, data)