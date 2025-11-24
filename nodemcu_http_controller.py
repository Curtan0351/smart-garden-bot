import requests
import logging
from typing import Optional, Tuple

class NodeMCUHTTPController:
    def __init__(self, ip_address: str):
        self.base_url = f"http://{ip_address}"
        self.connected = False
        self.ip = ip_address
        
    def connect(self) -> bool:
        """Проверка подключения к NodeMCU"""
        try:
            response = requests.get(f"{self.base_url}/status", timeout=5)
            if response.status_code == 200:
                self.connected = True
                logging.info(f"✅ Подключено к NodeMCU: {self.ip}")
                return True
        except Exception as e:
            logging.error(f"❌ Ошибка подключения к {self.ip}: {e}")
            self.connected = False
        return False
    
    def get_moisture(self) -> Tuple[Optional[int], Optional[str]]:
        """Получение влажности"""
        if not self.connected:
            return None, None
            
        try:
            response = requests.get(f"{self.base_url}/status", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get('moisture'), data.get('status')
        except Exception as e:
            logging.error(f"Ошибка получения влажности: {e}")
            self.connected = False
            
        return None, None
    
    def water_plant(self, duration: int) -> Tuple[bool, str]:
        """Полив растения"""
        if not self.connected:
            return False, "Нет подключения к системе"
            
        try:
            response = requests.get(f"{self.base_url}/water", params={'duration': duration}, timeout=30)
            if response.status_code == 200:
                return True, "Полив выполнен успешно"
            else:
                data = response.json()
                return False, data.get('error', 'Ошибка полива')
        except Exception as e:
            logging.error(f"Ошибка полива: {e}")
            return False, f"Ошибка связи: {str(e)}"
    
    def force_water_plant(self, duration: int) -> Tuple[bool, str]:
        """Принудительный полив"""
        if not self.connected:
            return False, "Нет подключения к системе"
            
        try:
            response = requests.get(f"{self.base_url}/force_water", timeout=30)
            if response.status_code == 200:
                return True, "Принудительный полив выполнен"
            else:
                return False, "Ошибка принудительного полива"
        except Exception as e:
            return False, f"Ошибка связи: {str(e)}"
    
    def reset_watering_time(self) -> bool:
        """Сброс времени полива"""
        if not self.connected:
            return False
            
        try:
            response = requests.get(f"{self.base_url}/reset", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def set_auto_mode(self, enable: bool) -> bool:
        """Включение/выключение авторежима"""
        if not self.connected:
            return False
            
        try:
            response = requests.get(f"{self.base_url}/auto", params={'enable': 1 if enable else 0}, timeout=5)
            return response.status_code == 200
        except:
            return False