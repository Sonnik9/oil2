import asyncio
from datetime import datetime
import os
from typing import Dict, Optional

import aiohttp
import random
import pytz

from API.symbols import PhemexSymbols
from API.phemex_client import PhemexPrivateClient
from c_log import UnifiedLogger
from consts import TIME_ZONE
from utils import round_step, async_append_to_json
from tg_sender import TelegramSender

from dotenv import load_dotenv

load_dotenv()


TZ = pytz.timezone(TIME_ZONE)
ERRORS_FILE = "oi_errors.json"

class OpenInterestScreener:
    def __init__(self, config: dict, logger: UnifiedLogger):
        self.config = config
        self.log = logger
        
        self.pos_side = self.config.get("pos_side", "LONG").upper()
        self.pos_mode = self.config.get("pos_mode", "hedged").lower() # "hedged" или "merged" 
        
        # Новые маржинальные настройки
        self.margin_type = self.config.get("margin_type", "ISOLATED").upper()
        self.leverage = self.config.get("leverage", 10)
        self.margin_amount = self.config.get("margin_amount", 1.0)
        
        self.order_interval = self.config.get("order_request_interval", [0.5, 1.5])
        self.batches_sleep_interval = self.config.get("batches_sleep_interval", [5.0, 10.0]) # ИСПРАВЛЕН КЛЮЧ
        self.sleep_every_n_symbols = self.config.get("sleep_every_n_symbols", [50, 60])
        self.price_interval = self.config.get("price_request_interval", 5)
        self.indentation_pct = self.config.get("order_indentation_pct", 5)
        self.iteration_interval = self.config.get("iteration_interval", 600)
        self.blacklist = set(self.config.get("black_list", []))
        
        self.api_key = os.getenv("api_key") or ""
        self.api_secret = os.getenv("api_secret") or ""

        self.bot_token = os.getenv("bot_token") or self.config.get("telegram", {}).get("bot_token", "") or ""
        self.chat_id = os.getenv("chat_id") or self.config.get("telegram", {}).get("chat_id", "") or ""
        self.telegram_enable = self.config.get("telegram", {}).get("enable", False)

        self.prices: Dict[str, float] = {}
        self.symbols_api = PhemexSymbols()
        self.client: Optional[PhemexPrivateClient] = None
        self._running = False
        self._price_task: Optional[asyncio.Task] = None

        self.leverage_set_cache = set()  # Кэш для хранения уже установленного плеча по символу

    async def _process_symbol(self, sym_info, tg_bot: Optional[TelegramSender]):
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
        
        # --- Расчет объема на основе маржи и плеча ---
        notional_value = self.margin_amount * self.leverage
        # Phemex требует минимальный номинал ордера ~$6. Берем максимум.
        actual_notional = max(6.0, notional_value) 
        
        qty = max(lot_size, actual_notional / order_price)
        qty = round_step(qty, lot_size)
        
        try:
            # 1. Сначала задаем плечо
            if symbol not in self.leverage_set_cache:
                lev_resp = await self.client.set_leverage(symbol, phemex_pos_side, self.leverage, mode=self.pos_mode)
                self.leverage_set_cache.add(symbol)

                # Логируем только реальные ошибки (11084 - это "Leverage not modified", всё ок)
                if lev_resp.get("code", -1) not in (0, 11084):
                    self.log.warning(f"[{symbol}] Не удалось сменить плечо: {lev_resp}")

            # 2. Постановка ордера
            resp = await self.client.place_order(symbol, side, qty, order_price, phemex_pos_side)
            code = resp.get("code", -1)
            
            if code == 0:
                # УСПЕХ! Сразу логируем сырой ответ, чтобы видеть все детали
                # self.log.info(f"[{symbol}] ✅ ОРДЕР ВСТАЛ! Ответ: {resp}")
                
                # 3. Отмена ордера на этой же итерации
                data_dict = resp.get("data") or {}
                # Phemex иногда пишет orderID, а иногда orderId
                order_id = data_dict.get("orderID") or data_dict.get("orderId")
                
                if order_id:
                    # ДОБАВИЛИ phemex_pos_side третьим аргументом
                    cancel_resp = await self.client.cancel_order(symbol, order_id, phemex_pos_side)
                    if cancel_resp.get("code", -1) == 0:
                        self.log.info(f"[{symbol}] 🗑️ Ордер {order_id} моментально отменен.")
                    else:
                        self.log.error(f"[{symbol}] ❌ ОШИБКА ОТМЕНЫ ОРДЕРА! {cancel_resp}")

            elif code == 11150:               
                # Максимально спартанский пуш в Telegram
                if tg_bot and self.telegram_enable:
                    await tg_bot.send_message(f"#{symbol}")
                    self.log.info(f"[{symbol}] 🚀 Символ отправлен в Telegram.")

                msg = resp.get("msg", "")
                self.log.warning(f"[{symbol}] Ошибка выставления: {code} - {msg}")
                await self._save_error(symbol, code, msg)  
                
            else:
                msg = resp.get("msg", "")
                self.log.warning(f"[{symbol}] Ошибка выставления: {code} - {msg}")
                
        except asyncio.TimeoutError:
            self.log.warning(f"[{symbol}] Таймаут ожидания ответа от биржи!")
        except Exception as e:
            self.log.error(f"[{symbol}] Исключение при отправке/отмене: {e}")
            await self._save_error(symbol, -1, str(e))

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
        tg_bot = TelegramSender(self.bot_token, self.chat_id) if self.telegram_enable else None
        
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

                # Определяем, на каком шаге будет ПЕРВАЯ пауза
                next_sleep_target = random.randint(*self.sleep_every_n_symbols)
                
                for num, sym_info in enumerate(filtered_symbols, start=1):
                    if not self._running:
                        break
                        
                    await self._process_symbol(sym_info, tg_bot)

                    # Логика сна
                    if num == next_sleep_target:
                        batches_sleep = random.uniform(*self.batches_sleep_interval)
                        self.log.debug(f"Достигнут лимит батча ({num} симв.). Длинная пауза: {batches_sleep:.2f} сек.")
                        await asyncio.sleep(batches_sleep)
                        
                        # Назначаем СЛЕДУЮЩИЙ таргет (текущий шаг + новое случайное число)
                        next_sleep_target = num + random.randint(*self.sleep_every_n_symbols)
                    else:
                        order_sleep = random.uniform(*self.order_interval)
                        await asyncio.sleep(order_sleep)
                    
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

    async def _save_error(self, symbol: str, code: int, msg: str):
        data = {
            "symbol": symbol,
            "timestamp": datetime.now(TZ).isoformat(),
            "code": code,
            "msg": msg
        }
        await async_append_to_json(ERRORS_FILE, data)