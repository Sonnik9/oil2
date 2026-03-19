import os
import json
import asyncio
from decimal import Decimal, ROUND_HALF_DOWN

def round_step(value: float, step: float) -> float:
    """Округление значения (цена/объем) до ближайшего шага (tick size / lot size)."""
    if not step or step <= 0:
        return value
    val_d = Decimal(str(value))
    step_d = Decimal(str(step))
    quantized = (val_d / step_d).quantize(Decimal('1'), rounding=ROUND_HALF_DOWN) * step_d
    return float(quantized)

def float_to_str(value: float) -> str:
    """Предотвращает появление научной записи (1e-05) при конвертации."""
    return f"{Decimal(str(value)):f}"

def _sync_append(filepath: str, data: dict):
    """Синхронная логика безопасного дописывания в JSON-массив."""
    if not os.path.exists(filepath):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump([data], f, indent=4, ensure_ascii=False)
        return
    
    with open(filepath, 'r', encoding='utf-8') as f:
        try:
            content = json.load(f)
        except json.JSONDecodeError:
            content = []
            
    content.append(data)
    
    tmp_file = filepath + ".tmp"
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(content, f, indent=4, ensure_ascii=False)
    os.replace(tmp_file, filepath)  # Атомарная замена

async def async_append_to_json(filepath: str, data: dict):
    """Асинхронная обертка для записи без блокировки event loop."""
    await asyncio.to_thread(_sync_append, filepath, data)