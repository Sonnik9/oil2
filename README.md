# Phemex Open Interest Screener (OI-Screener)

## 📌 Описание

**OI-Screener** — асинхронный торговый скринер для биржи Phemex,  
который автоматически выявляет фьючерсные пары, где временно ограничена торговля  
(например, из-за превышения лимита **Open Interest**).

---

## ⚙️ Принцип работы

### 1. Сбор данных
- Получение списка всех USDT-фьючерсных контрактов
- Фоновое обновление цен по всем тикерам

### 2. Итерация
- Проход по всем символам
- Исключение пар из `black_list`

### 3. Подготовка ордера
- Установка режима маржи (`ISOLATED` / `CROSS`)
- Установка плеча
- Расчет цены лимитного ордера с отступом (`order_indentation_pct`)
- Расчет минимального допустимого объема:
  - учитываются `tickSize`, `lotSize`
  - минимальный номинал биржи ≈ $6

### 4. Проверка (Ping)
- Отправка лимитного ордера

#### ✔ Успех:
- Ордер принят биржей  
- Немедленная отмена

#### ❌ Ошибка:
- Получен отказ от биржи  
- Ошибка логируется в `oi_errors.json`

### 5. Цикл
- После полного прохода — пауза (`iteration_interval`)
- Повтор

---

## ⚙️ Конфигурация (`config.json`)

### Основные параметры

| Параметр | Тип | Описание |
|--------|-----|----------|
| api_key | string | API ключ |
| api_secret | string | API секрет |
| pos_side | string | LONG / SHORT |
| margin_type | string | ISOLATED / CROSS |
| leverage | number | Плечо |
| margin_amount | float | Маржа |

---

## 📋 Пример config.json

{
    "api_key": "your_api_key",
    "api_secret": "your_api_secret",
    "pos_side": "LONG",
    "margin_type": "ISOLATED",
    "leverage": 10,
    "margin_amount": 2.0,
    "order_request_interval": 1,
    "price_request_interval": 5,
    "order_indentation_pct": 10,
    "iteration_interval": 600,
    "black_list": [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT"
    ]
}

---

## 📂 Логи

### logs/
- oi_screener.log

### oi_errors.json

{
    "symbol": "CFGUSDT",
    "timestamp": "2026-03-20T14:30:00.000000+02:00",
    "code": 11084,
    "msg": "Open interest limit exceeded"
}

---

## 🚀 Запуск

python main.py

---

## 🛑 Остановка

Ctrl + C
