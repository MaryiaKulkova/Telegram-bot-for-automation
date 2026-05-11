import asyncio
import logging
from datetime import datetime, time
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, \
    InlineKeyboardButton
import gspread
from google.oauth2.service_account import Credentials
from typing import Optional, Dict, List
import re

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

# Telegram Bot Token (вставьте свой токен)
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Путь к JSON-файлу с ключами Google Cloud
GOOGLE_CREDENTIALS_FILE = "credentials.json"

# ID Google таблицы (из ссылки)
SPREADSHEET_ID = ""

# Название листа в таблице
WORKSHEET_NAME = "выпуск карт - запросы"

# Время проверки обновлений (каждый день в 10:00)
CHECK_TIME = time(10, 0)

# =============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# =============================================================================
# СОСТОЯНИЯ FSM
# =============================================================================

class RegistrationStates(StatesGroup):
    waiting_surname = State()
    waiting_name = State()
    waiting_patronymic = State()
    waiting_birthdate = State()


class ClarificationStates(StatesGroup):
    waiting_reason = State()


class CardValidityStates(StatesGroup):
    waiting_card_number = State()


class AdminBroadcastStates(StatesGroup):
    waiting_choice = State()
    waiting_message = State()


# =============================================================================
# GOOGLE SHEETS КЛАСС
# =============================================================================

class GoogleSheetsManager:
    def __init__(self, credentials_file: str, spreadsheet_id: str, worksheet_name: str):
        self.spreadsheet = None
        self.credentials_file = credentials_file
        self.spreadsheet_id = spreadsheet_id
        self.worksheet_name = worksheet_name
        self.worksheet = None
        self.last_statuses = {}  # Хранение предыдущих статусов для отслеживания изменений

    def connect(self):
        """Подключение к Google Sheets"""
        try:
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = Credentials.from_service_account_file(
                self.credentials_file,
                scopes=scopes
            )
            client = gspread.authorize(creds)
            self.spreadsheet = client.open_by_key(self.spreadsheet_id)
            self.worksheet = self.spreadsheet.worksheet(self.worksheet_name)

            logger.info("✅ Успешное подключение к Google Sheets")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к Google Sheets: {e}")
            return False

    # === НОВЫЕ МЕТОДЫ ДЛЯ АДМИН-РАССЫЛКИ ===

    def get_unique_programs(self) -> List[str]:
        """Возвращает все уникальные значения из столбца 'Образовательная программа/ Специальность'"""
        try:
            records = self.get_all_records()
            programs = set()
            for rec in records:
                prog = str(rec.get('Образовательная программа/ Специальность', '')).strip()
                if prog and prog.lower() not in ['не указан', '']:
                    programs.add(prog)
            return sorted(list(programs))
        except Exception as e:
            logger.error(f"Ошибка получения уникальных программ: {e}")
            return []

    def get_subscribers_with_full_subscription(self) -> List[Dict]:
        """Возвращает всех подписчиков с типом 'all'"""
        try:
            sheet = self.spreadsheet.worksheet("подписчики")
            records = sheet.get_all_records()
            val = str(records[0].get("Тип подписки", "")).strip().lower()
            return [r for r in records if str(r.get("Тип подписки", "")).strip().lower() == 'all']
        except Exception as e:
            logger.error(f"Ошибка получения подписчиков: {e}")
            return []

    def get_user_program_by_telegram_id(self, telegram_id: int) -> Optional[str]:
        """Находит образовательную программу пользователя по его Telegram ID"""
        try:
            main_records = self.get_all_records()
            for rec in main_records:
                if str(rec.get("Telegram ID", "")) == str(telegram_id):
                    return str(rec.get('Образовательная программа/ Специальность', '')).strip()
            return None
        except Exception:
            return None

    def _create_admins_sheet_if_not_exists(self):
        """Создаёт лист 'admins', если его нет"""
        try:
            try:
                self.spreadsheet.worksheet("admins")
            except gspread.exceptions.WorksheetNotFound:
                sheet = self.spreadsheet.add_worksheet(title="admins", rows=200, cols=3)
                sheet.update("A1", [["Telegram username"]])
                sheet.update("A2", [["example_admin"]])  # пример
                logger.info("✅ Создан лист 'admins'")
        except Exception as e:
            logger.error(f"Ошибка создания листа admins: {e}")

    def is_admin(self, username: Optional[str]) -> bool:
        """Проверяет, является ли пользователь админом по листу 'admins'"""
        if not username:
            return False
        try:
            sheet = self.spreadsheet.worksheet("admins")
            records = sheet.get_all_records()
            admins = {str(row.get("Telegram username", "")).strip().lower() for row in records if
                      row.get("Telegram username")}
            return username.strip().lower() in admins
        except Exception as e:
            logger.error(f"Ошибка проверки админа: {e}")
            return False

    def get_all_records(self) -> List[Dict]:
        """Получить все записи из таблицы"""
        try:
            # Получаем заголовки и данные отдельно
            headers = self.worksheet.row_values(1)
            all_values = self.worksheet.get_all_values()

            # Очищаем заголовки от пробелов
            clean_headers = [h.strip() for h in headers]

            # ОТЛАДКА: Выводим заголовки
            logger.info(f"📋 Заголовки таблицы: {clean_headers}")

            # Формируем список словарей
            records = []
            for row in all_values[1:]:  # Пропускаем первую строку (заголовки)
                record = {}
                for idx, header in enumerate(clean_headers):
                    if idx < len(row):
                        record[header] = row[idx]
                    else:
                        record[header] = ''
                records.append(record)

            return records
        except Exception as e:
            logger.error(f"Ошибка при получении данных: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def find_user(self, surname: str, name: str, patronymic: str, birthdate: str) -> Optional[Dict]:
        """
        Поиск пользователя по ФИО и дате рождения
        Возвращает словарь с данными пользователя и номером строки
        """
        try:
            all_records = self.get_all_records()

            # Нормализация входных данных
            surname_norm = surname.strip().lower()
            name_norm = name.strip().lower()
            patronymic_norm = patronymic.strip().lower()
            birthdate_norm = birthdate.strip()

            # ОТЛАДКА: Выводим что ищем
            logger.info(
                f"🔍 Ищем: Фамилия='{surname_norm}', Имя='{name_norm}', Отчество='{patronymic_norm}', ДР='{birthdate_norm}'")
            logger.info(f"📊 Всего записей в таблице: {len(all_records)}")

            matches = []

            for idx, record in enumerate(all_records, start=2):  # start=2 т.к. строка 1 - заголовки
                record_surname = str(record.get('Фамилия', '')).strip().lower()
                record_name = str(record.get('Имя', '')).strip().lower()
                record_patronymic = str(record.get('Отчество', '')).strip().lower()
                record_birthdate = str(record.get('Дата рождения', '')).strip()

                # ОТЛАДКА: Выводим первые 3 записи для проверки
                if idx <= 4:
                    logger.info(
                        f"Строка {idx}: Фамилия='{record_surname}', Имя='{record_name}', Отчество='{record_patronymic}', ДР='{record_birthdate}'")

                # Проверяем совпадение по частям
                surname_match = record_surname == surname_norm
                name_match = record_name == name_norm
                patronymic_match = record_patronymic == patronymic_norm
                birthdate_match = record_birthdate == birthdate_norm

                # ОТЛАДКА: Если есть хотя бы частичное совпадение
                if surname_match or name_match:
                    logger.info(
                        f"Строка {idx} - частичное совпадение: Фамилия={surname_match}, Имя={name_match}, Отчество={patronymic_match}, ДР={birthdate_match}")

                if (record_surname == surname_norm and
                        record_name == name_norm and
                        record_patronymic == patronymic_norm and
                        record_birthdate == birthdate_norm):
                    matches.append({
                        'row': idx,
                        'data': record
                    })
                    logger.info(f"✅ ПОЛНОЕ СОВПАДЕНИЕ найдено в строке {idx}!")

            if len(matches) == 0:
                logger.warning("❌ Совпадений не найдено")
                return None
            elif len(matches) == 1:
                logger.info(f"✅ Найдена 1 запись")
                return matches[0]
            else:
                # Несколько совпадений - устанавливаем статус "уточнение статуса"
                logger.warning(f"⚠️ Найдено {len(matches)} записей для {surname} {name} {patronymic}")
                return {
                    'row': matches[0]['row'],
                    'data': matches[0]['data'],
                    'multiple': True
                }
        except Exception as e:
            logger.error(f"❌ Ошибка поиска пользователя: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def update_telegram_id(self, row: int, telegram_id: int) -> bool:
        """Обновить Telegram ID пользователя"""
        try:
            # Находим номер столбца "Telegram ID"
            headers = self.worksheet.row_values(1)

            # Если столбца нет, создаем его
            if "Telegram ID" not in headers:
                col = len(headers) + 1
                self.worksheet.update_cell(1, col, "Telegram ID")
                logger.info("Создан новый столбец 'Telegram ID'")
            else:
                col = headers.index("Telegram ID") + 1

            # Обновляем значение
            self.worksheet.update_cell(row, col, str(telegram_id))
            logger.info(f"Telegram ID {telegram_id} добавлен в строку {row}")
            return True
        except Exception as e:
            logger.error(f"Ошибка обновления Telegram ID: {e}")
            return False

    def get_user_status(self, row: int) -> Dict:
        """Получить статус пользователя по номеру строки"""
        try:
            record = self.worksheet.row_values(row)
            headers = self.worksheet.row_values(1)

            data = {}
            for idx, header in enumerate(headers):
                if idx < len(record):
                    data[header] = record[idx]

            return {
                'статус': data.get('статус', 'Не указан'),
                'письмо о готовности': data.get('письмо о готовности', 'Не отправлено'),
                'фио': f"{data.get('Фамилия ', '')} {data.get('Имя', '')} {data.get('Отчество', '')}",
                'дата_рождения': data.get('Дата рождения', ''),
                'email': data.get('Эл. Адрес', '')
            }
        except Exception as e:
            logger.error(f"Ошибка получения статуса: {e}")
            return {}

    def get_users_for_notification(self) -> List[Dict]:
        """
        Получить пользователей, которым нужно отправить уведомления
        Проверяет изменения статуса на: "напечатано", "новое фото!", "уточнение статуса"
        """
        try:
            all_records = self.get_all_records()
            users_to_notify = []

            for idx, record in enumerate(all_records, start=2):
                telegram_id = record.get('Telegram ID', '')
                if not telegram_id:
                    continue

                status = record.get('статус', '').strip().lower()
                row_key = f"{record.get('Фамилия')}_{record.get('Имя')}_{record.get('Дата рождения')}"

                # Проверяем, изменился ли статус
                if row_key in self.last_statuses:
                    old_status = self.last_statuses[row_key]
                    if old_status != status and status in ['напечатано', 'новое фото!', 'уточнение статуса',
                                                           'проблема с фото']:
                        users_to_notify.append({
                            'telegram_id': int(telegram_id),
                            'status': record.get('статус', ''),
                            'fio': f"{record.get('Фамилия', '')} {record.get('Имя', '')} {record.get('Отчество', '')}",
                            'row': idx
                        })

                # Обновляем последний статус
                self.last_statuses[row_key] = status

            return users_to_notify
        except Exception as e:
            logger.error(f"Ошибка получения пользователей для уведомлений: {e}")
            return []

    def find_card_by_number(self, card_number: str) -> Optional[Dict]:
        """
        Поиск карты по номеру на вкладке "Продление доступов"
        Если найдено несколько карт с одним номером - возвращает самую позднюю дату
        """
        try:
            # Переключаемся на вкладку "Продление доступов"
            client = gspread.authorize(Credentials.from_service_account_file(
                self.credentials_file,
                scopes=[
                    'https://www.googleapis.com/auth/spreadsheets',
                    'https://www.googleapis.com/auth/drive'
                ]
            ))
            spreadsheet = client.open_by_key(self.spreadsheet_id)
            renewal_worksheet = spreadsheet.worksheet("Продление доступов")

            # Получаем заголовки и данные
            headers = renewal_worksheet.row_values(1)
            clean_headers = [h.strip() for h in headers]
            all_values = renewal_worksheet.get_all_values()

            logger.info(f"🔍 Ищем номер карты: '{card_number}'")
            logger.info(f"📋 Заголовки вкладки 'Продление доступов': {clean_headers}")

            # Нормализуем номер карты (убираем пробелы, приводим к верхнему регистру)
            card_number_norm = card_number.strip().upper()

            # Ищем столбцы "Номер карты" и "Продление ДО"
            if "Номер карты" not in clean_headers:
                logger.error("Столбец 'Номер карты' не найден!")
                return None
            if "Продление ДО" not in clean_headers:
                logger.error("Столбец 'Продление ДО' не найден!")
                return None

            card_col_idx = clean_headers.index("Номер карты")
            expiry_col_idx = clean_headers.index("Продление ДО")

            # Собираем все совпадения
            matches = []

            for idx, row in enumerate(all_values[1:], start=2):  # Пропускаем заголовки
                if card_col_idx < len(row):
                    row_card_number = str(row[card_col_idx]).strip().upper()

                    # Отладка первых 3 строк
                    if idx <= 4:
                        logger.info(f"Строка {idx}: Номер карты='{row_card_number}'")

                    if row_card_number == card_number_norm:
                        expiry_date = row[expiry_col_idx] if expiry_col_idx < len(row) else ""
                        matches.append({
                            'card_number': row_card_number,
                            'expiry_date': expiry_date.strip(),
                            'row': idx
                        })
                        logger.info(f"✅ Найдено совпадение в строке {idx}: дата={expiry_date.strip()}")

            # Если не найдено ни одного совпадения
            if len(matches) == 0:
                logger.warning(f"❌ Карта с номером '{card_number}' не найдена")
                return None

            # Если найдено одно совпадение
            if len(matches) == 1:
                logger.info(f"✅ Найдена 1 карта")
                return matches[0]

            # Если найдено несколько совпадений - выбираем самую позднюю дату
            logger.info(f"⚠️ Найдено {len(matches)} карт с номером '{card_number}'. Выбираем самую позднюю дату.")

            # Функция для парсинга даты в формате ДД.ММ.ГГГГ
            def parse_date(date_str):
                try:
                    # Убираем пробелы
                    date_str = date_str.strip()
                    if not date_str:
                        return datetime.min

                    # Парсим дату в формате ДД.ММ.ГГГГ
                    parts = date_str.split('.')
                    if len(parts) == 3:
                        day, month, year = parts
                        return datetime(int(year), int(month), int(day))
                    else:
                        logger.warning(f"Неверный формат даты: '{date_str}'")
                        return datetime.min
                except Exception as e:
                    logger.warning(f"Ошибка парсинга даты '{date_str}': {e}")
                    return datetime.min

            # Сортируем совпадения по дате (от новой к старой)
            matches_sorted = sorted(matches, key=lambda x: parse_date(x['expiry_date']), reverse=True)

            # Возвращаем самую позднюю дату
            latest_match = matches_sorted[0]
            logger.info(f"✅ Выбрана самая поздняя дата: {latest_match['expiry_date']}")

            return latest_match

        except Exception as e:
            logger.error(f"Ошибка поиска карты: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def subscribe_user(self, telegram_id: int, username: Optional[str] = None, subscription_type: str = "all") -> bool:
        """
        Добавляет или обновляет запись в лист "подписчики"
        Сохраняет только Telegram ID, @username (если есть) и дату подписки
        """
        try:
            try:
                sheet = self.spreadsheet.worksheet("подписчики")
            except gspread.exceptions.WorksheetNotFound:
                sheet = self.spreadsheet.add_worksheet(title="подписчики", rows=1000, cols=5)
                headers = ["Telegram ID", "@username", "Дата подписки", "Тип подписки"]
                sheet.update("A1:D1", [headers])
                logger.info("Создан лист 'подписчики'")

            # Ищем существующую запись по Telegram ID
            tmp = self.worksheet
            self.worksheet = sheet
            records = self.get_all_records()
            self.worksheet = tmp
            row_number = None
            for i, row in enumerate(records, start=2):
                if str(row.get("Telegram ID", "")) == str(telegram_id):
                    row_number = i
                    break

            current_date = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

            row_data = [
                str(telegram_id),
                f"@{username}" if username else "",
                current_date,
                subscription_type
            ]

            if row_number:
                # обновляем дату (и username на всякий случай)
                sheet.update(f"A{row_number}:D{row_number}", [row_data])
                logger.info(f"Обновлена дата подписки для {telegram_id}")
            else:
                sheet.append_row(row_data)
                logger.info(f"Новый подписчик добавлен: {telegram_id}")

            return True

        except Exception as e:
            logger.error(f"Ошибка при подписке в лист 'подписчики': {e}")
            return False

    def find_user_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        """Поиск пользователя по Telegram ID в основной таблице"""
        try:
            all_records = self.get_all_records()
            for idx, record in enumerate(all_records, start=2):
                if str(record.get('Telegram ID', '')).strip() == str(telegram_id):
                    return {
                        'row': idx,
                        'data': record
                    }
            return None
        except Exception as e:
            logger.error(f"Ошибка поиска по Telegram ID: {e}")
            return None

    def unlink_telegram_id(self, telegram_id: int) -> bool:
        """Удаляет Telegram ID из всех записей (на случай дубликатов)"""
        try:
            all_records = self.get_all_records()
            headers = self.worksheet.row_values(1)
            if "Telegram ID" not in headers:
                return False
            col = headers.index("Telegram ID") + 1

            updated = False
            for idx, record in enumerate(all_records, start=2):
                if str(record.get('Telegram ID', '')).strip() == str(telegram_id):
                    self.worksheet.update_cell(idx, col, "")
                    updated = True
                    logger.info(f"Telegram ID удалён из строки {idx}")
            return updated
        except Exception as e:
            logger.error(f"Ошибка отвязки Telegram ID: {e}")
            return False


# =============================================================================
# ИНИЦИАЛИЗАЦИЯ БОТА И GOOGLE SHEETS
# =============================================================================

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
sheets_manager = GoogleSheetsManager(GOOGLE_CREDENTIALS_FILE, SPREADSHEET_ID, WORKSHEET_NAME)

# Хранилище для временных данных пользователей
user_data_storage = {}

# =============================================================================
# ОБРАБОТЧИКИ КОМАНД
# =============================================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    is_admin = sheets_manager.is_admin(message.from_user.username)
    """Команда /start - приветствие"""
    welcome_text = """
👋 **Добро пожаловать в бот Сообщества выпускников НИУ ВШЭ – Санкт-Петербург!**

Я помогу вам:
✅ Проверить статус выпускнической карты
✅ Получать автоматические уведомления о готовности карты
✅ Узнать информацию о мероприятиях и скидках

🔹 **Выпускническая карта** — это карта лояльности, которая дает вам:
• Доступ в корпуса университета
• Скидки в партнерских заведениях
• Участие в мероприятиях для выпускников

Выберите действие в меню ниже:
"""
    if is_admin:
        welcome_text += "\n\n🔐 **Вы вошли как администратор** — доступна кнопка рассылки."
    await message.answer(welcome_text, reply_markup=get_main_menu(is_admin), parse_mode="Markdown")


@dp.message(F.text == "📢 Рассылка сообщения")
async def admin_broadcast_start(message: types.Message, state: FSMContext):
    if not sheets_manager.is_admin(message.from_user.username):
        await message.answer("⛔ Доступ запрещён.")
        return

    await state.set_state(AdminBroadcastStates.waiting_choice)
    await message.answer("📢 Выберите тип рассылки:", reply_markup=get_broadcast_choice_keyboard())


@dp.message(AdminBroadcastStates.waiting_choice)
async def process_broadcast_choice(message: types.Message, state: FSMContext):
    if not sheets_manager.is_admin(message.from_user.username):
        await message.answer("⛔ Доступ запрещён.")
        return

    if message.text == "📢 Разослать ВСЕМ подписчикам":
        await state.update_data(filter_program=None)
        await state.set_state(AdminBroadcastStates.waiting_message)
        await message.answer(
            "✅ Рассылка **всем** подписчикам с полной подпиской.\n\n"
            "Отправьте сообщение (текст + фото), которое нужно разослать:",
            reply_markup=ReplyKeyboardRemove()
        )


    elif message.text == "🔍 Выбрать по образовательной программе":
        programs = sheets_manager.get_unique_programs()
        if not programs:
            await message.answer("❌ Нет образовательных программ в базе.")
            await state.clear()
            return

        await state.update_data(programs_list=programs, current_page=0)
        await message.answer(
            "Выберите образовательную программу для рассылки:",
            reply_markup=get_programs_keyboard(0, programs)

        )
        # состояние остаётся waiting_choice — дальше обрабатываем callback

    elif message.text == "↩️ Отмена":
        await state.clear()
        await message.answer("❌ Рассылка отменена.", reply_markup=get_main_menu(True))

@dp.callback_query(F.data.startswith("admin_prog_page_"))
async def process_program_page(callback: types.CallbackQuery, state: FSMContext):
    if not sheets_manager.is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён.")
        return

    page = int(callback.data.split("_")[-1])
    data = await state.get_data()
    programs = data.get("programs_list", [])

    await callback.message.edit_text(
        "Выберите образовательную программу для рассылки:",
        reply_markup=get_programs_keyboard(page, programs)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_prog_select_"))
async def process_program_select(callback: types.CallbackQuery, state: FSMContext):
    if not sheets_manager.is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён.")
        return

    index = int(callback.data.split("_")[-1])
    data = await state.get_data()
    programs = data.get("programs_list", [])

    if index >= len(programs):
        await callback.answer("Ошибка выбора")
        return

    selected_program = programs[index]
    await state.update_data(filter_program=selected_program)

    await callback.message.edit_text(
        f"✅ Выбрана программа: **{selected_program}**\n\n"
        "Теперь отправьте сообщение (текст + фото), которое нужно разослать:"
    )
    await state.set_state(AdminBroadcastStates.waiting_message)
    await callback.answer()


@dp.callback_query(F.data == "admin_prog_cancel")
async def process_program_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Выбор программы отменён.")
    await callback.answer()

@dp.message(AdminBroadcastStates.waiting_message)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    if not sheets_manager.is_admin(message.from_user.username):
        await message.answer("⛔ Доступ запрещён.")
        return

    data = await state.get_data()
    filter_program = data.get("filter_program")

    # Получаем всех подписчиков с полной подпиской
    subscribers = sheets_manager.get_subscribers_with_full_subscription()

    sent_count = 0
    for sub in subscribers:
        tg_id = sub.get("Telegram ID", "")
        if type(tg_id) != int:
            continue
        tg_id = int(tg_id)
        # Фильтр по программе
        if filter_program:
            user_prog = sheets_manager.get_user_program_by_telegram_id(tg_id)
            if user_prog != filter_program:
                continue

        try:
            # Копируем сообщение целиком (текст + все фото + форматирование)
            await bot.copy_message(
                chat_id=tg_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            sent_count += 1
            await asyncio.sleep(0.35)  # защита от лимитов
        except Exception as e:
            logger.warning(f"Не удалось отправить админ-рассылку {tg_id}: {e}")

    await message.answer(
        f"✅ Рассылка завершена!\n\n"
        f"Отправлено: **{sent_count}** подписчикам",
        reply_markup=get_main_menu(True)
    )
    await state.clear()

@dp.callback_query(F.data == "main_menu")
async def back_to_main_menu(callback: types.CallbackQuery, state: FSMContext):
    """Возврат в главное меню по inline-кнопке"""
    await state.clear()
    await callback.message.answer("Главное меню:", reply_markup=get_main_menu())
    await callback.answer()

@dp.message(F.text == "📊 Проверить статус готовности карты")
async def check_status_start(message: types.Message, state: FSMContext):
    """Проверка статуса — сначала по Telegram ID"""
    user_record = sheets_manager.find_user_by_telegram_id(message.from_user.id)

    if user_record:
        # Уже привязан → сразу показываем статус
        status_data = sheets_manager.get_user_status(user_record['row'])
        await message.answer(
            format_status_message(status_data),
            reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
            parse_mode="Markdown"
        )
        return

    # Не привязан → начинаем ввод данных
    await state.update_data(subscription_mode=False)  # чистая проверка
    await message.answer(
        "Для проверки статуса введите вашу **фамилию**:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(RegistrationStates.waiting_surname)
    await message.answer(" ", reply_markup=get_back_to_main_inline())

@dp.message(F.text == "🔔 Хочу получать уведомления о мероприятиях!")
async def subscribe_start(message: types.Message, state: FSMContext):
    """Подписка на все уведомления — сначала по Telegram ID"""
    user_record = sheets_manager.find_user_by_telegram_id(message.from_user.id)

    if user_record:
        # Уже привязан → сразу оформляем подписку "all"
        username = message.from_user.username
        subscribed = sheets_manager.subscribe_user(message.from_user.id, username, "all")
        status_data = sheets_manager.get_user_status(user_record['row'])

        await message.answer(
            "✅ Вы уже привязаны к карте и успешно подписаны на **все** уведомления!\n\n"
            f"{format_status_message(status_data)}",
            reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
            parse_mode="Markdown"
        )
        return

    # Не привязан → начинаем ввод
    await state.update_data(subscription_mode=True)
    await message.answer(
        "Для подписки на уведомления введите вашу **фамилию**:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(RegistrationStates.waiting_surname)
    await message.answer(" ", reply_markup=get_back_to_main_inline())

@dp.message(F.text == "🗞️ Подписаться на уведомления о готовности карты")
async def subscribe_ready_only_start(message: types.Message, state: FSMContext):
    """Подписка только на готовность карты — сначала по Telegram ID"""
    user_record = sheets_manager.find_user_by_telegram_id(message.from_user.id)

    if user_record:
        username = message.from_user.username
        subscribed = sheets_manager.subscribe_user(message.from_user.id, username, "ready_only")
        status_data = sheets_manager.get_user_status(user_record['row'])

        await message.answer(
            "✅ Вы уже привязаны к карте и успешно подписаны **только** на уведомления о готовности карты!\n\n"
            f"{format_status_message(status_data)}",
            reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
            parse_mode="Markdown"
        )
        return

    # Не привязан → начинаем ввод
    await state.update_data(subscription_mode="ready_only")
    await message.answer(
        "Для подписки на уведомления о готовности карты введите вашу **фамилию**:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(RegistrationStates.waiting_surname)
    await message.answer(" ", reply_markup=get_back_to_main_inline())

@dp.message(F.text == "📝 Заполнить заявку на получение карты")
async def application_form(message: types.Message):
    """Обработчик кнопки заполнения заявки"""
    form_text = """
📝 **Заявка на получение карты выпускника**

К сожалению, в данный момент не представляется возможным заполнить заявление на изготовление или продление карты выпускника непосредственно в боте.

Пожалуйста, заполните заявку на официальном сайте НИУ ВШЭ – Санкт-Петербург:

🔗 **Ссылка на форму заявки:**
[Получить карту выпускника](https://spb.hse.ru/alumni/polls/374994674.html)

После заполнения заявки вы сможете отследить статус готовности вашей карты в этом боте, нажав кнопку "📊 Проверить статус готовности карты".

По вопросам обращайтесь: alumnispb@hse.ru
"""
    await message.answer(form_text, parse_mode="Markdown", reply_markup=get_back_to_main_inline())


@dp.message(F.text == "📅 Проверить срок действия карты")
async def check_card_validity_start(message: types.Message, state: FSMContext):
    """Начало проверки срока действия карты"""
    validity_text = """
📅 **Проверка срока действия карты**

Для проверки срока действия вашей карты выпускника введите **номер карты**.

Номер карты указан на самой карте.

Введите номер карты:
"""
    await message.answer(
        validity_text,
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(CardValidityStates.waiting_card_number)
    await message.answer(" ", reply_markup=get_back_to_main_inline())

@dp.message(F.text == "❓ Частые вопросы")
async def cmd_faq(message: types.Message):
    text = "❓ **Частые вопросы**\n\nВыберите вопрос:"
    await message.answer(text, reply_markup=get_faq_inline_menu(), parse_mode="Markdown")

FAQ_ANSWERS = {
    "q1": (
        "**1. Где я могу забрать карту выпускника?**\n\n"
        "Забрать пропуск выпускника можно по будням (пн-пт) с 10:00 до 18:00 в корпусе на Кантемировской, 3А "
        "(офис Центра Развития Карьеры - №1007). Офис находится на 1 этаже корпуса Латт"
    ),
    "q2": (
        "**2. Могу ли я забрать карту выпускника в другом городе или корпусе?**\n\n"
        "Забрать карту выпускника можно только в нашем офисе на Кантемировской.\n\n"
        "Отправка карты почтой/доставкой в другие города или корпуса не предусмотрена.\n\n"
        "Если Ваш рабочий график не позволяет приехать в указанные часы в корпус университета, мы готовы обсудить возможности "
        "передачи карты другим способом, например через вашего представителя.\n\n"
        "Напишите нам на почту alumnispb@hse.ru и мы постараемся все организовать и помочь!"
    ),
    "q3": (
        "**3. Что дает карта выпускника?**\n\n"
        "С картой выпускника вы можете посещать все четыре кампуса Вышки — в Санкт-Петербурге, Нижнем Новгороде, Москве и Перми, "
        "а также пользоваться коворкингами, посещать мероприятия и лекции (карта работает так же, как и студенческий пропуск, — "
        "только теперь без учёбы).\n\n"
        "Сам пластик действует бессрочно, однако электронный доступ к нему необходимо продлевать каждые полгода.\n\n"
        "QR-код на карте ведёт на страницу со скидками и партнёрскими предложениями — можете смело пользоваться ими."
    ),
    "q4": (
        "**4. Сколько действует карта?**\n\n"
        "Срок действия карты составляет 6 месяцев с даты выпуска. Датой выпуска карты считается дата получения письма о готовности пропуска."
    ),
    "q5": (
        "**5. Как с нами связаться?**\n\n"
        "📧 **Email:** alumnispb@hse.ru\n"
        "📱 **Мобильный телефон:** +7 812 6445910, доб. (61676)"
    ),
    "q6": (
        "**6. Как продлить карту?**\n\n"
        "По истечению срока действия карты, заполните заявку на продление на официальном [сайте](https://www.hse.ru/polls/810595477.html)\n\n"
        "Срок действия карты будет продлеваться на полгода. Для дальнейшего продления пропуска вам необходимо будет вновь "
        "заполнить форму на сайте."
    ),
    "q7": (
        "**7. Что делать при утере карты?**\n\n"
        "Для блокировки утерянной карты и выпуска новой вам необходимо заполнить заявление об утере, которое вы можете найти по [ссылке](https://clck.ru/3STvQt) и направить его нам "
        "на почту alumnispb@hse.ru\n\n"
        "После получения от вас заполненных документов мы заблокируем старую карту и запустим процедуру выпуска новой."
    ),
    "q8": (
        "**8. Мне срочно нужна карта, что делать?**\n\n"
        "Если вам необходимо в ближайшее время попасть в Вышку, мы можем оформить Вам разовый пропуск.\n"
        "Для этого напишите нам на почту alumnispb@hse.ru письмо с указанием:\n"
        "— ФИО\n "
        "— целей и причин визита\n"
        "— почта\n"
        "— даты рождения\n"
        "— номера телефона\n"
        "— даты и времени планируемоговизита\n"
        "— наименование планируемого к посещению корпуса"
    ),
}


@dp.callback_query(F.data == "faq:list")
async def faq_list(callback: types.CallbackQuery):
    text = "❓ **Частые вопросы**\n\nВыберите вопрос:"
    await callback.message.answer(text, reply_markup=get_faq_inline_menu(), parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data.startswith("faq:"))
async def faq_answer(callback: types.CallbackQuery):
    key = callback.data.split(":", 1)[1]  # q1..q8
    if key == "list":
        await faq_list(callback)
        return

    answer = FAQ_ANSWERS.get(key)
    if not answer:
        await callback.answer("Не нашла этот вопрос.", show_alert=True)
        return

    await callback.message.answer(answer, reply_markup=get_faq_answer_nav(), parse_mode="Markdown")
    await callback.answer()

@dp.message(F.text == "📞 Контакты")
async def cmd_contacts(message: types.Message):
    """Контакты"""
    contacts_text = """
📞 **Контакты Сообщества выпускников НИУ ВШЭ – Санкт-Петербург**

📧 **Email:** alumnispb@hse.ru
🌐 **Сайт:** [Сообщество выпускников Питерской Вышки](https://spb.hse.ru/alumni/)
📍 **Адрес:** ул. Кантемировская, д. 3А, корпус латт, офис 1007
🔗 **Мы в социальных сетях:** 
- ВКонтакте: 
[Сообщество выпускников Питерской Вышки](https://vk.com/alumni_hsespb)

- Telegram: 
[Сообщество выпускников Питерской Вышки](https://t.me/alumni_hsespb)

⏰ **Часы работы:** пн-пт 10:00-18:00

📱 **Мобильный телефон:** +7 812 6445910, доб. (61676)

По всем вопросам обращайтесь к нам — мы всегда рады помочь! 😊
"""
    await message.answer(contacts_text, parse_mode="Markdown", reply_markup=get_back_to_main_inline())



# =============================================================================
# КЛАВИАТУРЫ
# =============================================================================
def get_back_to_main_inline():
    """Inline-кнопка возврата в главное меню (встроена в сообщение)"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Главное меню", callback_data="main_menu")]
        ]
    )

def get_faq_inline_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1. Где забрать карту?", callback_data="faq:q1")],
            [InlineKeyboardButton(text="2. Можно забрать в другом городе/корпусе?", callback_data="faq:q2")],
            [InlineKeyboardButton(text="3. Что дает карта выпускника?", callback_data="faq:q3")],
            [InlineKeyboardButton(text="4. Сколько действует карта?", callback_data="faq:q4")],
            [InlineKeyboardButton(text="5. Как с нами связаться?", callback_data="faq:q5")],
            [InlineKeyboardButton(text="6. Как продлить карту?", callback_data="faq:q6")],
            [InlineKeyboardButton(text="7. Что делать при утере карты?", callback_data="faq:q7")],
            [InlineKeyboardButton(text="8. Мне срочно нужно попасть в Вышку, что делать?", callback_data="faq:q8")],
            [InlineKeyboardButton(text="↩️ Главное меню", callback_data="main_menu")],
        ]
    )


def get_faq_answer_nav():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К списку вопросов", callback_data="faq:list")],
            [InlineKeyboardButton(text="↩️ Главное меню", callback_data="main_menu")],
        ]
    )

def get_main_menu(is_admin: bool = False):
    keyboard = [
        [KeyboardButton(text="📊 Проверить статус готовности карты")],
        [KeyboardButton(text="📅 Проверить срок действия карты")],
        [KeyboardButton(text="📝 Заполнить заявку на получение карты")],
        [KeyboardButton(text="🔔 Хочу получать уведомления о мероприятиях!")],
        [KeyboardButton(text="🗞️ Подписаться на уведомления о готовности карты")],
        [KeyboardButton(text="❓ Частые вопросы")],
        [KeyboardButton(text="📞 Контакты")]
    ]
    if is_admin:
        keyboard.insert(3, [KeyboardButton(text="📢 Рассылка сообщения")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_broadcast_choice_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📢 Разослать ВСЕМ подписчикам")],
            [KeyboardButton(text="🔍 Выбрать по образовательной программе")],
            [KeyboardButton(text="↩️ Отмена")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )


def get_programs_keyboard(page: int = 0, programs: List[str] = None) -> InlineKeyboardMarkup:
    """Генерирует Inline-клавиатуру с пагинацией по 10 программ"""
    if not programs:
        return InlineKeyboardMarkup(inline_keyboard=[])

    per_page = 10
    total_pages = (len(programs) + per_page - 1) // per_page
    start = page * per_page
    end = min(start + per_page, len(programs))

    keyboard = []

    # 10 кнопок с программами
    for i in range(start, end):
        prog = programs[i]
        keyboard.append([
            InlineKeyboardButton(
                text=prog[:60] + "..." if len(prog) > 60 else prog,  # обрезаем длинные названия
                callback_data=f"admin_prog_select_{i}"
            )
        ])

    # Кнопки навигации
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="← Назад", callback_data=f"admin_prog_page_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперёд →", callback_data=f"admin_prog_page_{page + 1}"))

    if nav_row:
        keyboard.append(nav_row)

    # Кнопка отмены
    keyboard.append([InlineKeyboardButton(text="↩️ Отмена", callback_data="admin_prog_cancel")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_clarification_menu():
    """Меню для уточнения ситуации с картой"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Утеря карты")],
            [KeyboardButton(text="Я сотрудник")],
            [KeyboardButton(text="↩️ Главное меню")]
        ],
        resize_keyboard=True
    )


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

def validate_date(date_str: str) -> bool:
    """Проверка формата даты ДД.ММ.ГГГГ"""
    pattern = r'^\d{2}\.\d{2}\.\d{4}$'
    if not re.match(pattern, date_str):
        return False
    try:
        day, month, year = map(int, date_str.split('.'))
        datetime(year, month, day)
        return True
    except ValueError:
        return False


def format_status_message(status_data: Dict) -> str:
    """Форматирование сообщения о статусе"""
    status = status_data.get('статус', 'Не указан')

    # Определяем сообщение в зависимости от статуса
    status_messages = {
        'напечатано': '✅ Ваша карта напечатана и готова к выдаче!',
        'ушло в печать': '🖨️ Ваша карта отправлена в печать.',
        'проблема с фото': '⚠️ Обнаружена проблема с фотографией. Пожалуйста, свяжитесь с нами.',
        'не отправлено еще': '⏳ Заявка принята, карта еще не отправлена в печать.',
        'новое фото!': '📸 Требуется новое фото. Пожалуйста, загрузите новое фото.',
        'уточнение статуса': '❓ Требуется уточнение статуса вашей карты.',
        'сотрудник': 'Вы являетесь сотрудником университета.',
        'ст.магистр': 'Статус: студент-магистр.',
        'уже есть пропуск': 'У вас уже есть действующий пропуск.',
        'блокировка': '🚫 Карта заблокирована.',
        'не вшэ': 'Вы не являетесь выпускником НИУ ВШЭ.',
        'ошибка заявки': '❌ Обнаружена ошибка в заявке.',
        'ждем приказа': '⏳ Ожидаем приказы о зачислении.'
    }

    message = f"""
📋 **Статус выпускнической карты**

👤 **ФИО:** {status_data.get('фио', 'Не указано')}
📅 **Дата рождения:** {status_data.get('дата_рождения', 'Не указана')}

📊 **Текущий статус:** {status}

{status_messages.get(status.lower(), f'Статус: {status}')}
"""

    # Добавляем инструкции в зависимости от статуса
    if status.lower() == 'напечатано':
        message += """
📍 **Где забрать:**
Забрать пропуск выпускника можно по будням (пн-пт) с 10:00 до 18:00 в корпусе на Кантемировской, 3А (офис Центра по работе с выпускниками - №1007). Офис находится на 1 этаже корпуса Латт, как к нам попасть, смотрите в видео-инструкции.

Если Ваш рабочий график не позволяет приехать в указанные часы в корпус университета, мы готовы обсудить возможности передачи карты другим способом, например через вашего представителя. Напишите нам на почту alumnispb@hse.ru и мы постараемся все организовать и помочь!


"""
    elif status.lower() == 'проблема с фото':
        message += """
Ваше фото не соответствует требованиям службы безопасности. Просим вас направить нам на почтe: alumnispb@hse.ru другую фотографию для пропуска, где хорошо видно ваше лицо: портретное фото в анфас (без водяных знаков, наклонов и поворотов головы, головных уборов, очков), хорошего качества в формате jpg, png. Предоставляемая фотография должна быть с белым фоном - формат максимально приближен к фото на документы.
"""
    elif status.lower() == 'новое фото!':
        message += """
Мы получили Вашу фотографию, отправили в печать! Следите за обновлениями.
"""
    elif status.lower() == 'ждем приказа':
        message += """
В данный момент мы не можем выполнить ваш запрос, так как вы завершили обучение в этом году.

Подать заявку на оформление карты выпускника можно будет осенью — после выхода приказов о зачислении. Это необходимо для предотвращения дублирования пропусков, поскольку, согласно внутренним правилам НИУ ВШЭ, студент не может одновременно иметь две действующие карты с разными статусами.

В случае возникновения дополнительных вы можете написать нам на почту alumnispb@hse.ru
"""

    elif status.lower() == 'уточнение статуса':
        message += """
Согласно имеющейся у нас информации, в настоящий момент Вы являетесь сотрудником НИУ ВШЭ - Санкт-Петербург.

Просим вас уточнить актуальность этих данных, отправив нам письмо на почту alumnispb@hse.ru. Так как согласно внутренним правилам НИУ ВШЭ — Санкт-Петербург нельзя  одновременно иметь на руках и пропуск сотрудника, и пропуск выпускника.
"""

    elif status.lower() == 'уже есть пропуск':
        message += """
В нашей базе данных есть информация о том, что вы получали пропуск ранее. Просим вас подтвердить актуальность этих данных по почте  alumnispb@hse.ru

Так как согласно внутренним правилам НИУ ВШЭ — Санкт-Петербург нельзя  одновременно иметь на руках и пропуск сотрудника, и пропуск выпускника.
"""

    elif status.lower() == 'ст.магистр':
        message += """
Согласно нашим данным  в настоящее время вы обучаетесь в магистратуре. Согласно внутренним правилам НИУ ВШЭ — Санкт-Петербург нельзя  одновременно иметь на руках 2 пропуска: и студента, и выпускника.

Просим подтвердить актуальность данной информации по почте alumnispb@hse.ru. 
"""

    return message


# =============================================================================
# FSM ОБРАБОТЧИКИ - РЕГИСТРАЦИЯ/ПРОВЕРКА СТАТУСА
# =============================================================================

@dp.message(RegistrationStates.waiting_surname)
async def process_surname(message: types.Message, state: FSMContext):
    """Обработка фамилии"""
    surname = message.text.strip()
    await state.update_data(surname=surname)
    await message.answer("Введите ваше **имя**:", parse_mode="Markdown")
    await state.set_state(RegistrationStates.waiting_name)


@dp.message(RegistrationStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    """Обработка имени"""
    name = message.text.strip()
    await state.update_data(name=name)
    await message.answer("Введите ваше **отчество**:", parse_mode="Markdown")
    await state.set_state(RegistrationStates.waiting_patronymic)


@dp.message(RegistrationStates.waiting_patronymic)
async def process_patronymic(message: types.Message, state: FSMContext):
    """Обработка отчества"""
    patronymic = message.text.strip()
    await state.update_data(patronymic=patronymic)
    await message.answer(
        "Введите вашу **дату рождения** в формате ДД.ММ.ГГГГ\n"
        "Например: 15.03.2000",
        parse_mode="Markdown"
    )
    await state.set_state(RegistrationStates.waiting_birthdate)


@dp.message(RegistrationStates.waiting_birthdate)
async def process_birthdate(message: types.Message, state: FSMContext):
    """Обработка даты рождения и поиск в базе"""
    birthdate = message.text.strip()

    # Проверка формата даты
    if not validate_date(birthdate):
        await message.answer(
            "❌ Неверный формат даты.\n"
            "Пожалуйста, введите дату в формате ДД.ММ.ГГГГ\n"
            "Например: 15.03.2000"
        )
        return

    # Получаем все данные пользователя
    user_data = await state.get_data()
    surname = user_data.get('surname')
    name = user_data.get('name')
    patronymic = user_data.get('patronymic')
    subscription_mode = user_data.get('subscription_mode', False)

    # Поиск в Google Sheets
    await message.answer("🔍 Ищу вашу заявку в базе данных...")

    user_record = sheets_manager.find_user(surname, name, patronymic, birthdate)

    if not user_record:
        await message.answer(
            "❌ К сожалению, заявка с такими данными не найдена.\n\n"
            "Пожалуйста, проверьте правильность введенных данных и попробуйте еще раз.\n\n"
            "Если вы еще не подавали заявку, заполните форму на сайте:\n"
            "[Получить карту выпускника](https://spb.hse.ru/alumni/polls/374994674.html)\n\n"
            "По вопросам обращайтесь: alumni@hse.ru",
            reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
            parse_mode="Markdown"
        )
        await state.clear()
        return

    # Проверяем, есть ли несколько записей с одинаковыми данными
    if user_record.get('multiple'):
        status_data = sheets_manager.get_user_status(user_record['row'])
        if status_data.get('статус', '').lower() == 'уточнение статуса':
            await message.answer(
                "❓ **Требуется уточнение статуса вашей карты**\n\n"
                "Обнаружено несколько записей с вашими данными.\n"
                "Пожалуйста, выберите причину:",
                reply_markup=get_clarification_menu(),
                parse_mode="Markdown"
            )
            await state.update_data(user_row=user_record['row'])
            await state.set_state(ClarificationStates.waiting_reason)
            return

    # Получаем статус карты
    status_data = sheets_manager.get_user_status(user_record['row'])
    # Связываем Telegram ID с записью в основной таблице (как было раньше)
    linked = sheets_manager.update_telegram_id(user_record['row'], message.from_user.id)
    if subscription_mode:

        # 2. Добавляем/обновляем в список подписчиков (минимальные данные)
        username = message.from_user.username
        subscribed = sheets_manager.subscribe_user(message.from_user.id, username, subscription_mode)

        status_msg = format_status_message(status_data)

        if linked and subscribed:
            if subscription_mode == "ready_only":
                reply_text = (
                    "✅ Вы подписаны **только** на уведомления о готовности карты.\n\n"
                    f"Текущий статус:\n\n{status_msg}"
                )
            else:
                reply_text = (
                    "✅ Вы подписаны на уведомления о мероприятиях и изменениях статуса карты.\n\n"
                    f"Текущий статус:\n\n{status_msg}"
                )
            await message.answer(reply_text,
                                 reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
                                 parse_mode="Markdown")
        else:
            await message.answer(
                "⚠️ Подписка оформлена частично (возможно технические неполадки).\n"
                "Вы всё равно можете проверять статус карты в любое время.\n\n"
                "Если уведомления не приходят — напишите alumnispb@hse.ru",
                reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
                parse_mode="Markdown"
            )

        await state.clear()
    else:
        # Режим проверки статуса
        await message.answer(
            format_status_message(status_data),
            reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
            parse_mode="Markdown"
        )

    await state.clear()


# =============================================================================
# ОБРАБОТЧИК ПРОВЕРКИ СРОКА ДЕЙСТВИЯ КАРТЫ
# =============================================================================

@dp.message(CardValidityStates.waiting_card_number)
async def process_card_number(message: types.Message, state: FSMContext):
    """Обработка номера карты для проверки срока действия"""
    card_number = message.text.strip()

    # Проверка формата номера (должен содержать цифры и/или буквы)
    if not re.match(r'^[A-Za-z0-9]+$', card_number):
        await message.answer(
            "❌ Неверный формат номера карты.\n\n"
            "Номер карты должен содержать только цифры и английские буквы без пробелов.\n"
            "Например: 0063173В или 00B478AF\n\n"
            "Попробуйте еще раз:"
        )
        return

    # Поиск карты в Google Sheets
    await message.answer("🔍 Ищу вашу карту в базе данных...")

    card_info = sheets_manager.find_card_by_number(card_number)

    if not card_info:
        await message.answer(
            "❌ **Карта не найдена**\n\n"
            "Не удалось найти вашу карту в базе данных.\n\n"
            "Пожалуйста, проверьте правильность введенного номера карты или напишите нам на почту:\n"
            "📧 alumnihsespb@hse.ru",
            reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
            parse_mode="Markdown"
        )
        await state.clear()
        return

    # Карта найдена - показываем срок действия
    expiry_date = card_info.get('expiry_date', 'Не указано')
    if expiry_date == '':
        expiry_date = 'Не указано'
    validity_message = f"""
✅ **Карта найдена!**

🎫 **Номер карты:** {card_info.get('card_number')}
📅 **Действительна до:** {expiry_date}

Если срок действия карты истекает или уже истек, вы можете продлить её, заполнив форму на сайте:
🔗 [Получить карту выпускника](https://spb.hse.ru/alumni/polls/374994674.html)

По вопросам обращайтесь: alumnihsespb@hse.ru
"""

    await message.answer(
        validity_message,
        reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
        parse_mode="Markdown"
    )
    await state.clear()


# =============================================================================
# ОБРАБОТЧИК УТОЧНЕНИЯ СТАТУСА
# =============================================================================

@dp.message(ClarificationStates.waiting_reason)
async def process_clarification(message: types.Message, state: FSMContext):
    """Обработка причины уточнения"""
    reason = message.text.strip()

    if reason in ["Утеря карты", "Я сотрудник"]:
        await message.answer(
            "📧 **Спасибо за информацию!**\n\n"
            "Для решения вашего вопроса, пожалуйста, свяжитесь с нами:\n\n"
            "📧 Email: alumnispb@hse.ru\n"
            "📞 Телефон: +7 812 6445910, доб. (61676)\n\n"
            "Мы обработаем ваш запрос в ближайшее время.",
            reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username)),
            parse_mode="Markdown"
        )
        await state.clear()
    elif reason == "↩️ Главное меню":
        await message.answer(
            "Вы вернулись в главное меню.",
            reply_markup=get_main_menu(sheets_manager.is_admin(message.from_user.username))
        )
        await state.clear()
    else:
        await message.answer(
            "Пожалуйста, выберите один из вариантов на клавиатуре.",
            reply_markup=get_clarification_menu()
        )


# =============================================================================
# ФОНОВАЯ ЗАДАЧА - ПРОВЕРКА УВЕДОМЛЕНИЙ
# =============================================================================

async def check_notifications():
    """Проверка изменений статусов и отправка уведомлений"""
    while True:
        try:
            now = datetime.now()
            # Проверяем каждый день в установленное время
            if now.hour == CHECK_TIME.hour and now.minute == CHECK_TIME.minute:
                logger.info("🔔 Начинаю проверку уведомлений...")

                users_to_notify = sheets_manager.get_users_for_notification()

                for user in users_to_notify:
                    try:
                        status_data = sheets_manager.get_user_status(user['row'])
                        notification_text = f"""
🔔 **Обновление статуса вашей карты!**

{format_status_message(status_data)}
"""
                        await bot.send_message(
                            user['telegram_id'],
                            notification_text,
                            parse_mode="Markdown"
                        )
                        logger.info(f"✅ Уведомление отправлено пользователю {user['telegram_id']}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки уведомления пользователю {user['telegram_id']}: {e}")

                # Ждем минуту, чтобы не отправлять уведомления повторно
                await asyncio.sleep(60)
                # Ежемесячная рассылка — 1-е число каждого месяца в 12:00
            if now.day == 1 and now.hour == 12 and now.minute == 00:
                logger.info("Запускается ежемесячная рассылка о статусе 'напечатано'")
                await monthly_printed_notification()
                await asyncio.sleep(70)  # защита от повторного срабатывания
            # Проверяем каждые 30 секунд
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче проверки уведомлений: {e}")
            await asyncio.sleep(60)


async def monthly_printed_notification():
    try:
        subs_sheet = sheets_manager.spreadsheet.worksheet("подписчики")
        main_sheet = sheets_manager.worksheet

        subs_data = subs_sheet.get_all_records()
        main_data = sheets_manager.get_all_records()

        sent_count = 0

        for sub in subs_data:
            tg_id_str = sub.get("Telegram ID", "")
            if not tg_id_str:
                continue
            tg_id = int(tg_id_str)

            last_sent = sub.get("Последнее уведомление «напечатано»", "")
            if last_sent and last_sent.strip():  # уже отправляли
                continue

            # ищем запись в основной таблице
            found = False
            for row_idx, record in enumerate(main_data, start=2):
                if str(record.get("Telegram ID", "")) == str(tg_id):
                    status = str(record.get("статус", "")).strip().lower()
                    if status == "напечатано":
                        fio = f"{record.get('Фамилия', '')} {record.get('Имя', '')} {record.get('Отчество', '')}".strip()

                        text = f"""
🔔 **Ваша выпускническая карта готова!**

Уважаемый(ая) {fio}!

По данным на сегодня ваша карта имеет статус **«напечатано»**.

Приглашаем забрать её в офисе Центра Развития Карьеры:
📍 Кантемировская, 3А, офис 1007 (1 этаж, корпус Латт)
🕙 пн–пт 10:00–18:00

Если не можете приехать в рабочее время — напишите нам, обсудим варианты.

С уважением,
Центр по работе с выпускниками НИУ ВШЭ — Санкт-Петербург
alumnispb@hse.ru
"""

                        try:
                            await bot.send_message(tg_id, text, parse_mode="Markdown")
                            # Записываем дату отправки
                            subs_sheet.update_cell(subs_data.index(sub) + 2, 9, datetime.now().strftime("%d.%m.%Y"))
                            sent_count += 1
                            logger.info(f"Отправлено уведомление о готовности {tg_id}")
                            await asyncio.sleep(0.4)  # защита от rate limit
                        except Exception as e:
                            logger.error(f"Не удалось отправить {tg_id}: {e}")

                    found = True
                    break

            if not found:
                logger.info(f"Не нашли Telegram ID {tg_id} в основной таблице")

        if sent_count > 0:
            logger.info(f"Ежемесячная рассылка завершена. Отправлено: {sent_count}")

    except Exception as e:
        logger.error(f"Ошибка в monthly_printed_notification: {e}")


# =============================================================================
# ЗАПУСК БОТА
# =============================================================================

async def main():
    """Главная функция запуска бота"""
    logger.info("🚀 Запуск бота...")

    # Подключение к Google Sheets
    if not sheets_manager.connect():
        logger.error("❌ Не удалось подключиться к Google Sheets. Бот не запущен.")
        return

    # Запуск фоновой задачи проверки уведомлений
    asyncio.create_task(check_notifications())

    logger.info("✅ Бот запущен и готов к работе!")

    # Запуск polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
