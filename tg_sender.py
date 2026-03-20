import aiohttp
from c_log import UnifiedLogger

logger = UnifiedLogger("tg_sender")

class TelegramSender:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send_message(self, text: str):
        if not self.token or not self.chat_id:
            return
            
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": False  # ГАРАНТИРУЕТ ЗВУКОВОЕ УВЕДОМЛЕНИЕ
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        logger.error(f"Ошибка отправки в Telegram: {err}")
                    else:
                        logger.info(f"Успешный пуш в Telegram: {text}")
        except Exception as e:
            logger.error(f"Исключение при отправке в Telegram: {e}")