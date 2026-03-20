import asyncio
import json
import os

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

    try:
        logger.info("Скринер запускается...")
        await screener.start()
    except asyncio.CancelledError:
        logger.info("Получен сигнал отмены. Запускаем процедуру остановки...")
    except Exception:
        logger.exception("Фатальная ошибка в main лупе")
    finally:
        # Гарантированное выполнение при любых раскладах
        await screener.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[INFO] Процесс прерван пользователем (Ctrl+C). Graceful shutdown успешно завершен.")