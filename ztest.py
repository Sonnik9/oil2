import time
import hmac
import hashlib
import asyncio
import aiohttp

# ================= НАСТРОЙКИ =================
API_KEY = "094ade21-a721-4b34-b6c9-2c13a3aaa56f"
API_SECRET = "jTOP6Z90qywJrFVmX-W5Q9XNaS2Lx34ujxJ9IlGMpkVjNmQ4YTc5ZS1lNTMzLTRjN2UtYTI0NC01NzJhMjUyMzBiNTk"

SYMBOL = "BTCUSDT"
LEVERAGE = 16

# "merged"   → One-Way / Merged mode
# "hedged"   → Hedge mode
MODE = "hedged" 
# =============================================

def get_signature(api_secret: str, path: str, query_no_question: str, expiry: int) -> str:
    # query_no_question — БЕЗ ? в начале
    message = f"{path}{query_no_question}{expiry}"
    # print("Строка для подписи:", message)          # ← раскомменти для отладки
    return hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

async def test_leverage():
    base_url = "https://api.phemex.com"
    path = "/g-positions/leverage"
    
    # Формируем query БЕЗ ? 
    if MODE == "merged":
        query_no_q = f"leverageRr={LEVERAGE}&symbol={SYMBOL}"
    elif MODE == "hedged":
        query_no_q = f"longLeverageRr={LEVERAGE}&shortLeverageRr={LEVERAGE}&symbol={SYMBOL}"
    else:
        raise ValueError("Неверный режим! Только 'merged' или 'hedged'")

    # Для URL добавляем ? 
    query_for_url = f"?{query_no_q}"

    expiry = int(time.time() + 60)
    
    signature = get_signature(API_SECRET, path, query_no_q, expiry)

    headers = {
        "Content-Type": "application/json",
        "x-phemex-access-token": API_KEY,
        "x-phemex-request-expiry": str(expiry),
        "x-phemex-request-signature": signature
    }

    url = f"{base_url}{path}{query_for_url}"
    
    print(f"Отправляем запрос на: {url}")
    print(f"Режим: {MODE.upper()}")
    # print(f"Строка подписи: {path}{query_no_q}{expiry}")     # отладка
    # print(f"Signature: {signature}")                         # отладка
    
    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, data=None) as resp:
            text = await resp.text()
            print(f"Код HTTP: {resp.status}")
            print(f"Ответ биржи: {text}")

if __name__ == "__main__":
    asyncio.run(test_leverage())