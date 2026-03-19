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
        
        self.pos_side = self.config.get("pos_side", "LONG").upper()
        self.order_interval = self.config.get("order_request_interval", 1)
        self.price_interval = self.config.get("price_request_interval", 5)
        self.indentation_pct = self.config.get("order_indentation_pct", 5)
        self.iteration_interval = self.config.get("iteration_interval", 600)
        self.blacklist = set(self.config.get("black_list", []))
        
        self.api_key = self.config.get("api_key", "")
        self.api_secret = self.config.get("api_secret", "")
        
        self.prices: Dict[str, float] = {}
        self.symbols_api = PhemexSymbols()
        self.client: Optional[PhemexPrivateClient] = None
        self._running = False
        self._price_task: Optional[asyncio.Task] = None

    async def _update_prices_loop(self):
        """Фоновое обновление цен."""
        while self._running:
            await asyncio.sleep(self.price_interval) # Сначала спим, так как при старте уже запросили
            if self.client:
                try:
                    tickers = await asyncio.wait_for(
                        self.client.get_all_tickers(timeout_sec=10.0), 
                        timeout=15.0
                    )
                    if tickers:
                        self.prices = tickers
                except asyncio.TimeoutError:
                    self.log.warning("Таймаут фонового обновления цен.")
                except Exception as e:
                    self.log.error(f"Failed to fetch prices in bg: {e}")
            
    async def start(self):
        self._running = True
        
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300, enable_cleanup_closed=True)
        
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            self.client = PhemexPrivateClient(self.api_key, self.api_secret, session)
            
            self.log.info("Запрашиваем стартовые цены одним прямым запросом...")
            try:
                self.prices = await self.client.get_all_tickers()
            except Exception as e:
                self.log.error(f"Фатальная ошибка при первичном получении цен: {e}")
                return
                
            if not self.prices:
                self.log.error("Прайсы не загрузились (пустой ответ)! Прерываем скринер. Смотри консоль [DEBUG] для отладки.")
                return
                
            self.log.info(f"Успешно получены стартовые цены для {len(self.prices)} пар.")
            
            # Запускаем фоновое обновление ТОЛЬКО после успешного старта
            self._price_task = asyncio.create_task(self._update_prices_loop())
            
            while self._running:
                self.log.info("--- Начинается новая итерация проверки Open Interest ---")
                try:
                    symbols_list = await self.symbols_api.get_all(quote="USDT", only_active=True)
                except Exception as e:
                    self.log.error(f"Ошибка получения списка символов: {e}")
                    await asyncio.sleep(10)
                    continue

                filtered_symbols = [s for s in symbols_list if s.symbol not in self.blacklist]
                self.log.info(f"Символов для проверки: {len(filtered_symbols)}")
                
                for sym_info in filtered_symbols:
                    if not self._running:
                        break
                        
                    await self._process_symbol(sym_info)
                    await asyncio.sleep(self.order_interval)
                    
                self.log.info(f"Итерация завершена. Сон {self.iteration_interval} сек.")
                if self._running:
                    await asyncio.sleep(self.iteration_interval)

    async def stop(self):
        if not self._running:
            return
        self.log.info("Остановка скринера...")
        self._running = False
        if self._price_task:
            self._price_task.cancel()
        await self.symbols_api.aclose()

    async def _process_symbol(self, sym_info):
        symbol = sym_info.symbol
        price = self.prices.get(symbol)
        
        if not price:
            self.log.warning(f"[{symbol}] Пропуск: нет цены.")
            return
            
        if self.pos_side == "LONG":
            order_price = price * (1 - self.indentation_pct / 100.0)
            side = "Buy"
        else:
            order_price = price * (1 + self.indentation_pct / 100.0)
            side = "Sell"
            
        phemex_pos_side = self.pos_side.capitalize()
            
        raw = sym_info.raw_data
        tick_size = float(raw.get("tickSize", "0.001"))
        lot_size = float(raw.get("lotSize", "0.01"))
        
        order_price = round_step(order_price, tick_size)
        
        min_notional = 6.0 
        qty = max(lot_size, min_notional / order_price)
        qty = round_step(qty, lot_size)
        
        try:
            # 1. Постановка ордера
            resp = await self.client.place_order(symbol, side, qty, order_price, phemex_pos_side)
            self.log.info(f"[{symbol}] РЕСПОНС ПОСТАНОВКИ: {resp}") # Сырой словарь в лог
            
            code = resp.get("code", -1)
            
            if code == 0:
                # 2. Отмена ордера, если постановка успешна
                order_id = resp.get("data", {}).get("orderID")
                if order_id:
                    cancel_resp = await self.client.cancel_order(symbol, order_id)
                    self.log.info(f"[{symbol}] РЕСПОНС ОТМЕНЫ: {cancel_resp}") # Сырой словарь в лог
                    
                    if cancel_resp.get("code", -1) != 0:
                        self.log.error(f"[{symbol}] ОШИБКА ОТМЕНЫ!")
            else:
                msg = resp.get("msg", "")
                self.log.warning(f"[{symbol}] Ошибка выставления: {code} - {msg}")
                await self._save_error(symbol, code, msg)
                
        except asyncio.TimeoutError:
            self.log.warning(f"[{symbol}] Таймаут ожидания ответа от биржи!")
        except Exception as e:
            self.log.error(f"[{symbol}] Исключение при отправке/отмене: {e}")
            await self._save_error(symbol, -1, str(e))

    async def _save_error(self, symbol: str, code: int, msg: str):
        data = {
            "symbol": symbol,
            "timestamp": datetime.now(TZ).isoformat(),
            "code": code,
            "msg": msg
        }
        await async_append_to_json(ERRORS_FILE, data)