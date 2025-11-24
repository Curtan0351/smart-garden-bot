import logging
import os
import sys
import signal
import time
import json
import asyncio
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv('BOT_TOKEN')

# Импортируем и инициализируем контроллер NodeMCU
from nodemcu_http_controller import NodeMCUHTTPController
NODEMCU_IP = os.getenv('NODEMCU_IP', '192.168.0.119')
nodemcu = NodeMCUHTTPController(NODEMCU_IP)
# Файл конфигурации
CONFIG_FILE = 'config.json'

# Настройки по умолчанию
DEFAULT_CONFIG = {
    'auto_mode': False,
    'auto_mode_type': 'smart',
    'watering_duration': 3,
    'schedule_morning_time': '09:00',
    'schedule_evening_time': '19:00',
    'notifications': True,
    'moisture_threshold': 430,
    'report_interval': 30,
    'last_watering': None,
    'watering_count_today': 0,
    'last_watering_date': None,
    'dont_ask_again_today': False
}

def signal_handler(sig, frame):
    print('🚨 Получен сигнал завершения...')
    if 'nodemcu' in globals():
        nodemcu.disconnect()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def main():
    # Проверяем токен бота
    if not BOT_TOKEN:
        print("❌ ОШИБКА: BOT_TOKEN не найден в .env файле!")
        return
    
    # Подключаемся к NodeMCU по Wi-Fi
    print(f"🔌 Подключаемся к NodeMCU ({NODEMCU_IP})...")
    if nodemcu.connect():
        print("✅ Успешное подключение к NodeMCU по Wi-Fi")
    else:
        print("❌ Не удалось подключиться к NodeMCU")
        print("💡 Проверьте:")
        print(f"   • IP адрес: {NODEMCU_IP}")
        print("   • NodeMCU подключен к Wi-Fi")
        print("   • NodeMCU включен")
    
    # Инициализируем конфиг
    load_config()
    
    # Создаем Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск бота
    print("🤖 Бот запущен...")
    if nodemcu.connected:
        print("🌿 Режим: АВТОНОМНАЯ СИСТЕМА (Wi-Fi)")
    else:
        print("🌿 Режим: ОЖИДАНИЕ ПОДКЛЮЧЕНИЯ")
    
    application.run_polling()
def load_config():
    """Загружает настройки из файла"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                for key, value in DEFAULT_CONFIG.items():
                    if key not in config:
                        config[key] = value
                return config
        except Exception as e:
            logging.error(f"Ошибка загрузки конфига: {e}")
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(config):
    """Сохраняет настройки в файл"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Ошибка сохранения конфига: {e}")
        return False

def update_config(new_settings):
    """Обновляет настройки"""
    config = load_config()
    config.update(new_settings)
    save_config(config)
    return config

# Функция для получения реальных данных с датчика
def get_sensor_data():
    """Получение данных с датчика - только реальные данные, при отсутствии связи возвращает None"""
    if not nodemcu.connected:
        return None
    
    try:
        raw_value, status = nodemcu.get_moisture()
        if raw_value is not None:
            # Конвертируем в проценты на основе ваших калибровочных значений
            air_value = 750
            water_value = 305
            moisture_percent = int((air_value - raw_value) / (air_value - water_value) * 100)
            moisture_percent = max(0, min(100, moisture_percent))

            # Температуру пока оставляем эмулированной
            import random
            temperature = random.randint(18, 28)
            
            return {
                'moisture_percent': moisture_percent,
                'moisture_raw': raw_value,
                'temperature': temperature,
                'status': status
            }
    except Exception as e:
        print(f"❌ Ошибка получения данных с датчика: {e}")
    
    return None

# Функция для получения текстового статуса влажности
def get_moisture_status(moisture_raw):
    """Возвращает текстовый статус на основе сырого значения датчика"""
    if moisture_raw >= 530:
        return "🚨 ОЧЕНЬ СУХО", "Срочный полив требуется!"
    elif moisture_raw >= 430:
        return "⚠️ СУХО", "Рекомендуется полив"
    elif moisture_raw >= 350:
        return "✅ НОРМА", "Влажность в норме"
    elif moisture_raw >= 320:
        return "🌟 ИДЕАЛЬНО", "Оптимальная влажность"
    elif moisture_raw >= 310:
        return "🌧️ ВЛАЖНО", "Полив не требуется"
    elif moisture_raw >= 305:
        return "🚨 ПЕРЕУВЛАЖНЕНИЕ", "Опасно для растения"
    else:
        return "💦 СЛИШКОМ МОКРО", "Возможно гниение корней"

# Проверка возможности полива
def check_watering_restrictions():
    """Проверяет ограничения и возвращает (можно_полить, сообщение, уровень_предупреждения)"""
    config = load_config()
    
    # Если нет связи с NodeMCU - нельзя поливать
    if not nodemcu.connected:
        return False, "❌ НЕТ СВЯЗИ С СИСТЕМОЙ\n\nНе удается подключиться к системе полива. Проверьте подключение NodeMCU.", "danger"
    
    # Сбрасываем флаг "не спрашивать" если новый день
    today = datetime.now().date()
    if config.get('dont_ask_again_date') != str(today):
        config['dont_ask_again_today'] = False
        config['dont_ask_again_date'] = str(today)
        save_config(config)
    
    # Если установлен флаг "не спрашивать" - пропускаем проверки
    if config.get('dont_ask_again_today'):
        return True, "✅ Можно поливать", "ok"
    
    # Проверяем дату последнего полива
    warning_message = None
    warning_level = "ok"
    
    if config['last_watering_date']:
        last_date = datetime.fromisoformat(config['last_watering_date']).date()
        if last_date == today and config['watering_count_today'] >= 1:
            hours_since_last = (datetime.now() - datetime.fromisoformat(config['last_watering'])).total_seconds() / 3600
            
            if hours_since_last < 6:
                warning_message = f"🚨 СЛИШКОМ ЧАСТЫЙ ПОЛИВ!\n\nПрошло менее 6 часов с последнего полива. Это может навредить растению."
                warning_level = "danger"
            elif hours_since_last < 12:
                warning_message = f"⚠️ ВНИМАНИЕ!\n\nСегодня уже был полив ({config['watering_count_today']} раз). Прошло {int(hours_since_last)} часов."
                warning_level = "warning"
            else:
                warning_message = f"💡 ИНФОРМАЦИЯ\n\nСегодня уже был полив, но прошло {int(hours_since_last)} часов."
                warning_level = "info"
    
    # Проверяем влажность почвы
    sensor_data = get_sensor_data()
    if sensor_data is None:
        return False, "❌ НЕТ ДАННЫХ О ВЛАЖНОСТИ\n\nНе удалось получить данные с датчика влажности.", "danger"
    
    if sensor_data['moisture_raw'] < 310:  # Уже очень влажно
        if warning_message:
            warning_message += f"\n\n❌ Влажность почвы слишком высокая ({sensor_data['moisture_raw']})."
        else:
            warning_message = f"❌ Влажность почвы слишком высокая ({sensor_data['moisture_raw']})."
        warning_level = "danger"
    
    if warning_message:
        return True, warning_message, warning_level
    else:
        return True, "✅ Можно поливать", "ok"

# Главное меню
def main_menu_keyboard():
    keyboard = [
        ['🌱 Статус растения'],
        ['💦 Полить растение'],
        ['🤖 Автономный режим'],
        ['📚 Информация'],
        ['⚙️ Настройки']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Меню полива
def watering_menu_keyboard():
    config = load_config()
    duration = config['watering_duration']
    keyboard = [
        [f'💦 Полить {duration} сек'],
        ['❌ Отмена']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Меню подтверждения полива
def confirm_watering_keyboard():
    keyboard = [
        ['✅ ДА, полить', '❌ НЕТ, отменить'],
        ['🔔 Больше не спрашивать сегодня']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Меню времени полива
def watering_time_menu_keyboard():
    config = load_config()
    current_duration = config['watering_duration']
    
    keyboard = [
        [f'⏱ 3 сек {"✅" if current_duration == 3 else ""}', f'⏱ 5 сек {"✅" if current_duration == 5 else ""}'],
        [f'⏱ 10 сек {"✅" if current_duration == 10 else ""}', '↩️ Назад']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Меню автономного режима
def auto_mode_menu_keyboard():
    config = load_config()
    auto_mode = config['auto_mode']
    mode_type = config.get('auto_mode_type', 'smart')
    
    keyboard = []
    
    if auto_mode:
        if mode_type == 'smart':
            keyboard.append(['🧠 Умный режим ✅'])
            keyboard.append(['📅 Перейти на расписание', '❌ Выключить авторежим'])
        else:
            keyboard.append(['📅 Режим по расписанию ✅'])
            keyboard.append(['🧠 Перейти на умный режим', '❌ Выключить авторежим'])
    else:
        keyboard.append(['🧠 Включить умный режим'])
        keyboard.append(['📅 Включить режим по расписанию'])
    
    keyboard.append(['↩️ Назад'])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Меню настроек
def settings_menu_keyboard():
    keyboard = [
        ['⏱ Время полива', '🔔 Уведомления'],
        ['📅 Настройка расписания', '🔄 Сброс времени полива'],
        ['↩️ Назад']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Меню уведомлений
def notifications_menu_keyboard():
    config = load_config()
    status = "✅ Вкл" if config['notifications'] else "❌ Выкл"
    
    keyboard = [
        [f'🔔 {status}'],
        ['↩️ Назад']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Меню настройки расписания
def schedule_settings_menu_keyboard():
    config = load_config()
    keyboard = [
        ['🕘 Утреннее время', '🕖 Вечернее время'],
        ['↩️ Назад']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Меню выбора времени для расписания
def time_selection_menu_keyboard():
    keyboard = [
        ['🕘 09:00', '🕙 10:00', '🕚 11:00'],
        ['🕖 19:00', '🕗 20:00', '🕘 21:00'],
        ['↩️ Назад']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    
    status_text = f"🌿 Привет, {user.first_name}!\nДобро пожаловать в систему умного полива растений!\n\n"
    
    if nodemcu.connected:
        status_text += "✅ **СИСТЕМА ПОДКЛЮЧЕНА**\nСвязь с NodeMCU установлена"
    else:
        status_text += "❌ **ПРОБЛЕМА СВЯЗИ**\nНе удается подключиться к системе полива\n\n🔌 Проверьте:\n• Подключение NodeMCU к компьютеру\n• USB кабель\n• Драйверы CH340/CP2102"
    
    await update.message.reply_text(
        status_text,
        reply_markup=main_menu_keyboard(),
        parse_mode='Markdown'
    )

async def water_plant(duration_seconds):
    """Полив растения - только реальный полив"""
    if not nodemcu.connected:
        return False, "❌ НЕТ СВЯЗИ С СИСТЕМОЙ ПОЛИВА"
    
    success, message = nodemcu.water_plant(duration_seconds)
    if success:
        # Обновляем историю поливов
        config = load_config()
        now = datetime.now()
        today = now.date()
        
        if config['last_watering_date'] != str(today):
            config['watering_count_today'] = 0
        
        config['last_watering'] = now.isoformat()
        config['last_watering_date'] = str(today)
        config['watering_count_today'] += 1
        save_config(config)
    
    return success, message

async def start_watering(update: Update, context: ContextTypes.DEFAULT_TYPE, duration: int):
    """Запускает процесс полива"""
    # Показываем начальное сообщение
    await update.message.reply_text(
        f"💦 **ЗАПУСК ПОЛИВА...**\n\n"
        f"⏱ Длительность: {duration} секунд\n"
        f"💧 Объем воды: ~{duration * 50} мл\n"
        f"🔄 Процесс запущен...",
        reply_markup=main_menu_keyboard(),
        parse_mode='Markdown'
    )
    
    # ВЫЗОВ ФУНКЦИИ ПОЛИВА
    success, result_message = await water_plant(duration)
    
    if success:
        # Получаем актуальные данные после полива
        sensor_data = get_sensor_data()
        if sensor_data:
            status, description = get_moisture_status(sensor_data['moisture_raw'])
            
            await update.message.reply_text(
                f"✅ **ПОЛИВ ЗАВЕРШЕН!**\n\n"
                f"{result_message}\n"
                f"⏱ Длительность: {duration} секунд\n"
                f"💧 Текущая влажность: {sensor_data['moisture_raw']} ({status})\n"
                f"📅 Время: {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"🌱 Растение получило влагу",
                reply_markup=main_menu_keyboard(),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"✅ **ПОЛИВ ЗАВЕРШЕН!**\n\n"
                f"{result_message}\n"
                f"⏱ Длительность: {duration} секунд\n"
                f"💧 Не удалось получить данные о влажности\n"
                f"📅 Время: {datetime.now().strftime('%H:%M:%S')}",
                reply_markup=main_menu_keyboard(),
                parse_mode='Markdown'
            )
    else:
        error_text = (
            f"❌ ОШИБКА ПОЛИВА!\n\n"
            f"{result_message}\n\n"
            f"💡 Проверьте:\n"
            f"• Подключение NodeMCU\n"
            f"• Питание помпы\n"
            f"• Соединение реле"
        )
        await update.message.reply_text(
            error_text,
            reply_markup=main_menu_keyboard()
        )

# Обработка всех сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    config = load_config()
    
    print(f"📨 Получено сообщение от {update.effective_user.id}: '{text}'")
    
    # Главное меню
    if text == '🌱 Статус растения':
        sensor_data = get_sensor_data()
        
        if sensor_data is None:
            await update.message.reply_text(
                "❌ **НЕТ СВЯЗИ С ДАТЧИКОМ**\n\n"
                "Не удалось получить данные о состоянии растения.\n\n"
                "🔧 **Возможные причины:**\n"
                "• NodeMCU не подключен к компьютеру\n"
                "• Проблема с USB кабелем\n"
                "• Не установлены драйверы CH340/CP2102\n"
                "• NodeMCU не запущен\n\n"
                "✅ **Решение:**\n"
                "1. Проверьте подключение NodeMCU\n"
                "2. Перезагрузите систему\n"
                "3. Убедитесь, что загружена прошивка",
                reply_markup=main_menu_keyboard()
            )
            return
        
        status, description = get_moisture_status(sensor_data['moisture_raw'])
        
        # Информация о последнем поливе
        last_watering_info = "Не было"
        if config['last_watering']:
            last_time = datetime.fromisoformat(config['last_watering'])
            time_diff = datetime.now() - last_time
            hours = int(time_diff.total_seconds() // 3600)
            if hours < 1:
                minutes = int(time_diff.total_seconds() // 60)
                last_watering_info = f"{minutes} минут назад"
            else:
                last_watering_info = f"{hours} часов назад"
        
        # Формируем текст статуса
        status_text = (
            f"🌱 **СТАТУС РАСТЕНИЯ**\n\n"
            f"💧 Влажность почвы: {sensor_data['moisture_raw']} ({status})\n"
            f"📊 Уровень: {sensor_data['moisture_percent']}%\n"
            f"🌡️ Температура: {sensor_data['temperature']}°C\n"
            f"⏱ Последний полив: {last_watering_info}\n"
            f"📈 Поливов сегодня: {config['watering_count_today']}\n\n"
        )
        
        # Добавляем информацию об авторежиме
        if config['auto_mode']:
            mode_type = config.get('auto_mode_type', 'smart')
            mode_name = "Умный режим" if mode_type == 'smart' else "По расписанию"
            status_text += f"🤖 Авторежим: {mode_name} ✅\n\n"
        else:
            status_text += f"🤖 Авторежим: Выключен ❌\n\n"
            
        status_text += f"💡 {description}"
        
        await update.message.reply_text(
            status_text,
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )
        
    elif text == '💦 Полить растение':
        # Проверяем можно ли поливать
        can_water, message, warning_level = check_watering_restrictions()
        
        if not can_water:
            await update.message.reply_text(
                message,
                reply_markup=main_menu_keyboard()
            )
            return
            
        config = load_config()
        duration = config['watering_duration']
        
        # Всегда показываем меню подтверждения если есть предупреждение
        if warning_level in ["danger", "warning", "info"]:
            context.user_data['pending_watering'] = {
                'duration': duration,
                'warning_level': warning_level
            }
            
            await update.message.reply_text(
                message + "\n\n**Вы уверены, что хотите продолжить?**",
                reply_markup=confirm_watering_keyboard(),
                parse_mode='Markdown'
            )
        else:
            # Можно поливать без предупреждений
            await start_watering(update, context, duration)
        
    elif text == '🤖 Автономный режим':
        if not nodemcu.connected:
            await update.message.reply_text(
                "❌ **НЕТ СВЯЗИ С СИСТЕМОЙ**\n\n"
                "Автономный режим недоступен. Сначала восстановите подключение к NodeMCU.",
                reply_markup=main_menu_keyboard()
            )
            return
            
        config = load_config()
        auto_mode = config['auto_mode']
        mode_type = config.get('auto_mode_type', 'smart')
        
        if auto_mode:
            if mode_type == 'smart':
                duration = config['watering_duration']
                status_text = (
                    "🤖 **АВТОНОМНЫЙ РЕЖИМ: УМНЫЙ** ✅\n\n"
                    "🧠 *Как работает:*\n"
                    "• Система анализирует влажность почвы\n"
                    "• Полив при влажности >430\n"
                    f"• Длительность полива: {duration} сек\n"
                    "• Учет времени последнего полива\n"
                    "• Защита от перелива\n\n"
                    "📊 *Текущие настройки:*\n"
                    f"• Порог полива: 430\n"
                    f"• Длительность: {duration} сек\n"
                    f"• Используется настройка времени полива"
                )
            else:
                morning_time = config.get('schedule_morning_time', '09:00')
                evening_time = config.get('schedule_evening_time', '19:00')
                duration = config['watering_duration']
                status_text = (
                    "🤖 **АВТОНОМНЫЙ РЕЖИМ: ПО РАСПИСАНИЮ** ✅\n\n"
                    "📅 *Как работает:*\n"
                    f"• Полив каждый день в {morning_time} и {evening_time}\n"
                    f"• Длительность полива: {duration} сек\n"
                    "• Работает в выходные и праздники\n\n"
                    "⏰ *Текущее расписание:*\n"
                    f"• Утро: {morning_time} (~{duration * 50} мл)\n"
                    f"• Вечер: {evening_time} (~{duration * 50} мл)"
                )
        else:
            status_text = (
                "🤖 **АВТОНОМНЫЙ РЕЖИМ: ВЫКЛЮЧЕН** ❌\n\n"
                "Ручное управление поливом.\n\n"
                "🔧 *Доступные режимы:*\n"
                "• 🧠 Умный режим - полив по влажности\n"
                "• 📅 По расписанию - полив по времени"
            )
        
        await update.message.reply_text(
            status_text,
            reply_markup=auto_mode_menu_keyboard(),
            parse_mode='Markdown'
        )
        
    elif text == '📚 Информация':
        await update.message.reply_text(
            "🌿 **ИНФОРМАЦИЯ О СИСТЕМЕ**\n\n"
            "🪴 *Растение:* Монстера\n"
            "💧 *Идеальная влажность:* 320-350\n"  
            "🌡️ *Идеальная температура:* 20-25°C\n"
            "💦 *Объем полива:* 150-500 мл\n\n"
            "📊 *Диапазоны датчика:*\n"
            "• 530+: 🚨 ОЧЕНЬ СУХО\n"
            "• 430-530: ⚠️ СУХО\n" 
            "• 350-430: ✅ НОРМА\n"
            "• 320-350: 🌟 ИДЕАЛЬНО\n"
            "• 310-320: 🌧️ ВЛАЖНО\n"
            "• 305-310: 🚨 ПЕРЕУВЛАЖНЕНИЕ\n"
            "• <305: 💦 СЛИШКОМ МОКРО\n\n"
            f"🔌 *Статус связи:* {'✅ ПОДКЛЮЧЕНО' if nodemcu.connected else '❌ ОТКЛЮЧЕНО'}",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )
        
    elif text == '⚙️ Настройки':
        status_emoji = "✅" if config['notifications'] else "❌"
        auto_emoji = "✅" if config['auto_mode'] else "❌"
        
        connection_status = "✅ Подключено" if nodemcu.connected else "❌ Отключено"
        
        await update.message.reply_text(
            f"⚙️ **НАСТРОЙКИ СИСТЕМЫ**\n\n"
            f"🔌 Связь с NodeMCU: {connection_status}\n"
            f"⏱ Длительность полива: {config['watering_duration']} сек\n"
            f"🔔 Уведомления: {status_emoji}\n"
            f"🤖 Авторежим: {auto_emoji}\n\n"
            "Выберите параметр для настройки:",
            reply_markup=settings_menu_keyboard(),
            parse_mode='Markdown'
        )
    
    # 🔧 ОБРАБОТКА МЕНЮ ПОЛИВА
    elif text.startswith('💦 Полить'):
        try:
            # Извлекаем время из текста кнопки "💦 Полить 5 сек"
            duration = int(text.split(' ')[2])
        except:
            duration = config['watering_duration']
        
        # Проверяем можно ли поливать
        can_water, message, warning_level = check_watering_restrictions()
        
        if not can_water:
            await update.message.reply_text(
                message,
                reply_markup=main_menu_keyboard()
            )
            return
        
        # Всегда показываем меню подтверждения если есть предупреждение
        if warning_level in ["danger", "warning", "info"]:
            context.user_data['pending_watering'] = {
                'duration': duration,
                'warning_level': warning_level
            }
            
            await update.message.reply_text(
                message + "\n\n**Вы уверены, что хотите продолжить?**",
                reply_markup=confirm_watering_keyboard(),
                parse_mode='Markdown'
            )
        else:
            # Можно поливать без предупреждений
            await start_watering(update, context, duration)
        
    elif text == '❌ Отмена':
        if 'pending_watering' in context.user_data:
            del context.user_data['pending_watering']
        await update.message.reply_text(
            "❌ Полив отменен",
            reply_markup=main_menu_keyboard()
        )
    
    # 🔧 ОБРАБОТКА ПОДТВЕРЖДЕНИЯ ПОЛИВА
    elif text == '✅ ДА, полить':
        if 'pending_watering' in context.user_data:
            duration = context.user_data['pending_watering']['duration']
            del context.user_data['pending_watering']
            await start_watering(update, context, duration)
        else:
            await update.message.reply_text(
                "❌ Не найдено ожидающего полива.",
                reply_markup=main_menu_keyboard()
            )

    elif text == '❌ НЕТ, отменить':
        if 'pending_watering' in context.user_data:
            del context.user_data['pending_watering']
        await update.message.reply_text(
            "✅ Полив отменен.",
            reply_markup=main_menu_keyboard()
        )

    elif text == '🔔 Больше не спрашивать сегодня':
        if 'pending_watering' in context.user_data:
            # Устанавливаем флаг, чтобы сегодня больше не спрашивать
            config = load_config()
            config['dont_ask_again_today'] = True
            config['dont_ask_again_date'] = str(datetime.now().date())
            save_config(config)
            
            duration = context.user_data['pending_watering']['duration']
            del context.user_data['pending_watering']
            await start_watering(update, context, duration)
        else:
            await update.message.reply_text(
                "❌ Не найдено ожидающего полива.",
                reply_markup=main_menu_keyboard()
            )
    
    # 🔧 ОБРАБОТКА АВТОНОМНОГО РЕЖИМА
    elif text == '🧠 Включить умный режим':
        if not nodemcu.connected:
            await update.message.reply_text(
                "❌ Невозможно включить авторежим: нет связи с системой",
                reply_markup=main_menu_keyboard()
            )
            return
            
        update_config({
            'auto_mode': True,
            'auto_mode_type': 'smart'
        })
        await update.message.reply_text(
            "✅ **ВКЛЮЧЕН УМНЫЙ АВТОРЕЖИМ**\n\n"
            "🤖 Система будет автоматически поливать растение когда это необходимо.\n\n"
            "🧠 *Логика работы:*\n"
            f"• Длительность полива: {config['watering_duration']} сек\n"
            "• Анализ влажности почвы\n" 
            "• Полив при значениях >430\n"
            "• Защита от переливов\n"
            "• Учет времени последнего полива",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )

    elif text == '📅 Включить режим по расписанию':
        if not nodemcu.connected:
            await update.message.reply_text(
                "❌ Невозможно включить авторежим: нет связи с системой",
                reply_markup=main_menu_keyboard()
            )
            return
            
        update_config({
            'auto_mode': True, 
            'auto_mode_type': 'schedule'
        })
        morning_time = config.get('schedule_morning_time', '09:00')
        evening_time = config.get('schedule_evening_time', '19:00')
        await update.message.reply_text(
            "✅ **ВКЛЮЧЕН РЕЖИМ ПО РАСПИСАНИЮ**\n\n"
            "📅 Система будет поливать растение по расписанию.\n\n"
            "⏰ *Расписание полива:*\n"
            f"• {morning_time} - Утренний полив\n"
            f"• {evening_time} - Вечерний полив\n\n"
            f"⏱ Длительность: {config['watering_duration']} секунд",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )

    elif text == '🧠 Умный режим ✅':
        # Уже активен - показываем информацию
        await update.message.reply_text(
            "🧠 **УМНЫЙ РЕЖИМ АКТИВЕН** ✅\n\n"
            "Система автоматически определяет когда нужно поливать растение.\n\n"
            "📊 *Критерии полива:*\n"
            f"• Длительность: {config['watering_duration']} сек\n"
            "• Влажность почвы >430\n"
            "• Прошло >24ч с последнего полива\n"
            "• Учитывается история поливов",
            reply_markup=auto_mode_menu_keyboard(),
            parse_mode='Markdown'
        )

    elif text == '📅 Режим по расписанию ✅':
        # Уже активен - показываем информацию  
        morning_time = config.get('schedule_morning_time', '09:00')
        evening_time = config.get('schedule_evening_time', '19:00')
        await update.message.reply_text(
            "📅 **РЕЖИМ ПО РАСПИСАНИЮ АКТИВЕН** ✅\n\n"
            "Следующий полив по расписанию:\n"
            f"• {morning_time} - Утренний полив\n" 
            f"• {evening_time} - Вечерний полив\n\n"
            f"⏱ Длительность: {config['watering_duration']} сек\n"
            f"⏰ Текущее время: " + datetime.now().strftime("%H:%M"),
            reply_markup=auto_mode_menu_keyboard(),
            parse_mode='Markdown'
        )

    elif text == '📅 Перейти на расписание':
        update_config({'auto_mode_type': 'schedule'})
        morning_time = config.get('schedule_morning_time', '09:00')
        evening_time = config.get('schedule_evening_time', '19:00')
        await update.message.reply_text(
            "✅ **ПЕРЕКЛЮЧЕНО НА РЕЖИМ ПО РАСПИСАНИЮ**\n\n"
            f"Теперь полив будет по расписанию:\n"
            f"• {morning_time} и {evening_time} ежедневно\n"
            f"⏱ Длительность: {config['watering_duration']} сек",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )

    elif text == '🧠 Перейти на умный режим':
        update_config({'auto_mode_type': 'smart'})
        await update.message.reply_text(
            "✅ **ПЕРЕКЛЮЧЕНО НА УМНЫЙ РЕЖИМ**\n\n"
            f"Теперь полив будет по влажности почвы.\n"
            f"⏱ Длительность: {config['watering_duration']} сек\n"
            f"Порог срабатывания: >430",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )

    elif text == '❌ Выключить авторежим':
        update_config({'auto_mode': False})
        await update.message.reply_text(
            "❌ **АВТОНОМНЫЙ РЕЖИМ ВЫКЛЮЧЕН**\n\n"
            "Теперь полив только в ручном режиме.",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )
    
    # 🔧 ОБРАБОТКА НАСТРОЕК
    elif text == '⏱ Время полива':
        await update.message.reply_text(
            f"⏱ **НАСТРОЙКА ВРЕМЕНИ ПОЛИВА**\n\n"
            f"Текущее значение: {config['watering_duration']} сек\n"
            f"Выберите новое значение:\n\n"
            f"*Это время используется в:*\n"
            f"• Ручном поливе\n"
            f"• Умном авторежиме\n"
            f"• Режиме по расписанию",
            reply_markup=watering_time_menu_keyboard(),
            parse_mode='Markdown'
        )
        
    elif text == '🔔 Уведомления':
        status = "включены" if config['notifications'] else "выключены"
        await update.message.reply_text(
            f"🔔 **НАСТРОЙКА УВЕДОМЛЕНИЙ**\n\n"
            f"Текущий статус: {status}\n"
            "Выберите действие:",
            reply_markup=notifications_menu_keyboard(),
            parse_mode='Markdown'
        )
    
    elif text == '📅 Настройка расписания':
        morning_time = config.get('schedule_morning_time', '09:00')
        evening_time = config.get('schedule_evening_time', '19:00')
        await update.message.reply_text(
            f"📅 **НАСТРОЙКА РАСПИСАНИЯ**\n\n"
            f"🕘 Утренний полив: {morning_time}\n"
            f"🕖 Вечерний полив: {evening_time}\n\n"
            f"⏱ Длительность полива: {config['watering_duration']} сек\n\n"
            f"Выберите параметр для настройки:",
            reply_markup=schedule_settings_menu_keyboard(),
            parse_mode='Markdown'
        )

    elif text == '🔄 Сброс времени полива':
        if nodemcu.connected:
            success = nodemcu.reset_watering_time()
            if success:
                await update.message.reply_text(
                    "✅ Время последнего полива сброшено!\n\n"
                    "Теперь можно снова полить растение.",
                    reply_markup=main_menu_keyboard()
                )
            else:
                await update.message.reply_text(
                    "❌ Не удалось сбросить время полива",
                    reply_markup=main_menu_keyboard()
                )
        else:
            await update.message.reply_text(
                "❌ NodeMCU не подключен",
                reply_markup=main_menu_keyboard()
            )
    
    # 🔧 ОБРАБОТКА ВРЕМЕНИ ПОЛИВА
    elif text.startswith('⏱ 3 сек'):
        update_config({'watering_duration': 3})
        await update.message.reply_text(
            "✅ **Время полива установлено: 3 секунды**\n"
            "💧 Примерный объем воды: 150 мл\n\n"
            "*Это время будет использоваться во всех режимах*",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )
        
    elif text.startswith('⏱ 5 сек'):
        update_config({'watering_duration': 5})
        await update.message.reply_text(
            "✅ **Время полива установлено: 5 секунд**\n"
            "💧 Примерный объем воды: 250 мл\n\n"
            "*Это время будет использоваться во всех режимах*",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )
        
    elif text.startswith('⏱ 10 сек'):
        update_config({'watering_duration': 10})
        await update.message.reply_text(
            "✅ **Время полива установлено: 10 секунд**\n"
            "💧 Примерный объем воды: 500 мл\n\n"
            "*Это время будет использоваться во всех режимах*",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )
    
    # 🔧 ОБРАБОТКА УВЕДОМЛЕНИЙ
    elif text.startswith('🔔'):
        # Переключаем уведомления
        new_notifications = not config['notifications']
        update_config({'notifications': new_notifications})
        
        status = "ВКЛЮЧЕНЫ" if new_notifications else "ВЫКЛЮЧЕНЫ"
        await update.message.reply_text(
            f"🔔 Уведомления **{status}**",
            reply_markup=main_menu_keyboard(),
            parse_mode='Markdown'
        )
    
    # 🔧 НАСТРОЙКА РАСПИСАНИЯ
    elif text == '🕘 Утреннее время':
        await update.message.reply_text(
            "🕘 **НАСТРОЙКА УТРЕННЕГО ВРЕМЕНИ**\n\n"
            "Выберите время для утреннего полива:",
            reply_markup=time_selection_menu_keyboard()
        )
    
    elif text == '🕖 Вечернее время':
        await update.message.reply_text(
            "🕖 **НАСТРОЙКА ВЕЧЕРНЕГО ВРЕМЕНИ**\n\n"
            "Выберите время для вечернего полива:",
            reply_markup=time_selection_menu_keyboard()
        )
    
    # Обработка выбора времени для расписания
    elif text in ['🕘 09:00', '🕙 10:00', '🕚 11:00', '🕖 19:00', '🕗 20:00', '🕘 21:00']:
        time_value = text.split(' ')[1]  # Извлекаем время из текста
        
        # Определяем, какое время настраивается (утреннее или вечернее)
        if 'Утреннее время' in context.user_data.get('last_schedule_setting', ''):
            update_config({'schedule_morning_time': time_value})
            await update.message.reply_text(
                f"✅ **Утреннее время установлено: {time_value}**",
                reply_markup=schedule_settings_menu_keyboard(),
                parse_mode='Markdown'
            )
        elif 'Вечернее время' in context.user_data.get('last_schedule_setting', ''):
            update_config({'schedule_evening_time': time_value})
            await update.message.reply_text(
                f"✅ **Вечернее время установлено: {time_value}**",
                reply_markup=schedule_settings_menu_keyboard(),
                parse_mode='Markdown'
            )
        else:
            # Если контекст потерян, сохраняем оба времени
            if time_value in ['09:00', '10:00', '11:00']:
                update_config({'schedule_morning_time': time_value})
                await update.message.reply_text(
                    f"✅ **Утреннее время установлено: {time_value}**",
                    reply_markup=schedule_settings_menu_keyboard(),
                    parse_mode='Markdown'
                )
            else:
                update_config({'schedule_evening_time': time_value})
                await update.message.reply_text(
                    f"✅ **Вечернее время установлено: {time_value}**",
                    reply_markup=schedule_settings_menu_keyboard(),
                    parse_mode='Markdown'
                )
    
    # 🔧 КНОПКА "НАЗАД"
    elif text == '↩️ Назад':
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=main_menu_keyboard()
        )
    
    # ❓ НЕИЗВЕСТНАЯ КОМАНДА
    else:
        await update.message.reply_text(
            "❓ Неизвестная команда. Используйте кнопки меню.",
            reply_markup=main_menu_keyboard()
        )

def main():
    # Проверяем токен бота
    if not BOT_TOKEN:
        print("❌ ОШИБКА: BOT_TOKEN не найден в .env файле!")
        print("📝 Создайте файл .env и добавьте: BOT_TOKEN=ваш_токен")
        return
    
    # Пытаемся подключиться к NodeMCU
    print("🔌 Подключаемся к NodeMCU...")
    if nodemcu.connect():
        print("✅ Успешное подключение к NodeMCU")
    else:
        print("❌ Не удалось подключиться к NodeMCU")
        print("💡 Бот будет сообщать о проблемах связи")
    
    # Инициализируем конфиг при первом запуске
    load_config()
    
    # Создаем Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск бота
    print("🤖 Бот запущен...")
    if nodemcu.connected:
        print("🌿 Режим: РЕАЛЬНАЯ СИСТЕМА (подключено к NodeMCU)")
    else:
        print("🌿 Режим: ОЖИДАНИЕ ПОДКЛЮЧЕНИЯ")
    
    application.run_polling()

if __name__ == '__main__':
    main()