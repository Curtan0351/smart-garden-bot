import json
import os

CONFIG_FILE = 'config.json'

# Настройки по умолчанию
DEFAULT_CONFIG = {
    'auto_mode': False,
    'watering_duration': 3,
    'notifications': True,
    'moisture_threshold': 400,
    'report_interval': 30
}

def load_config():
    """Загружает настройки из файла"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(config):
    """Сохраняет настройки в файл"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def get_config():
    """Возвращает текущие настройки"""
    return load_config()

def update_config(new_settings):
    """Обновляет настройки"""
    config = load_config()
    config.update(new_settings)
    save_config(config)
    return config