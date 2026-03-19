import asyncio
import json
import os
import sys
import signal

from c_log import UnifiedLogger
from screener import OpenInterestScreener

async def main():
    logger = UnifiedLogger("oi_screener")
    
    config_path = "config.json"
    if not os.path.exists(config_path):
        logger.error(f"Config file {config_path} not found!")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    screener = OpenInterestScreener(config, logger)
    
    # Кроссплатформенная обработка сигналов
    # На Windows (win32) ProactorEventLoop не поддерживает add_signal_handler
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(screener.stop()))

    try:
        logger.info("Скринер запускается...")
        await screener.start()
    except asyncio.CancelledError:
        logger.info("Получен сигнал отмены. Запускаем процедуру остановки...")
        await screener.stop()
    except Exception:
        logger.exception("Фатальная ошибка в main лупе")
        await screener.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Глушим системный трейсбек при принудительном завершении (Ctrl+C)
        print("\n[INFO] Процесс прерван пользователем (Ctrl+C). Graceful shutdown успешно завершен.")