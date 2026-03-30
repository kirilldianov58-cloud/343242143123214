#!/usr/bin/env python3
"""
Telegram-бот для визажиста
Версия: 7.1 – исправлена кнопка "Услуги" (без HTML)
"""

import asyncio
import logging
import sqlite3
import re
from datetime import datetime, date, timedelta
import pytz

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "8615339487:AAE34fezdBoQ1Dof5eoCzZi4bAwMpSrdrY0"
ADMIN_IDS = [6298119477]          # ЗАМЕНИТЕ НА СВОЙ ID
REVIEW_CHANNEL_ID = -1003884818442  # ЗАМЕНИТЕ НА ID КАНАЛА

WORK_START_HOUR = 10
WORK_END_HOUR = 20
WORK_DAYS = [0, 1, 2, 3, 4, 5, 6]  # 0=пн, 1=вт, ..., 6=вс

CHITA_TZ = pytz.timezone("Asia/Chita")

# ==================== КАСТОМНЫЕ ЭМОДЗИ ====================
EMOJI_CHECKMARK = '<tg-emoji emoji-id="5262832270573582269">✅</tg-emoji>'
EMOJI_CLOCK = '<tg-emoji emoji-id="5258258882022612173">⏰</tg-emoji>'
EMOJI_CALENDAR = '<tg-emoji emoji-id="5258105663359294787">📅</tg-emoji>'
EMOJI_BULLET = '<tg-emoji emoji-id="4918327330239152795">•</tg-emoji>'
EMOJI_PHONE = '<tg-emoji emoji-id="5467539229468793355">📞</tg-emoji>'

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect("makeup_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            duration INTEGER DEFAULT 60
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            service_id INTEGER,
            appointment_date TEXT,
            appointment_time TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            review_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(telegram_id),
            FOREIGN KEY (service_id) REFERENCES services(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            appointment_id INTEGER,
            service_id INTEGER,
            text TEXT NOT NULL,
            photo_file_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            published_at TIMESTAMP,
            rejection_reason TEXT,
            FOREIGN KEY (user_id) REFERENCES users(telegram_id)
        )
    """)
    # Обновляем услуги
    cursor.execute("DELETE FROM services")
    services = [
        ("Дневной макияж", 2000, 60),
        ("Вечерний макияж", 2500, 90),
        ("Креативный макияж", 2800, 90),
        ("Экспресс макияж", 1500, 30),
    ]
    cursor.executemany("INSERT INTO services (name, price, duration) VALUES (?, ?, ?)", services)
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

def db_query(query, params=None, fetch_one=False, fetch_all=False):
    conn = sqlite3.connect("makeup_bot.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if params:
        cursor.execute(query, params)
    else:
        cursor.execute(query)
    result = None
    if fetch_one:
        result = cursor.fetchone()
    elif fetch_all:
        result = cursor.fetchall()
    else:
        conn.commit()
        result = cursor.lastrowid
    conn.close()
    return result

# ==================== FSM СОСТОЯНИЯ ====================
class BookingState(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    choosing_name = State()
    entering_phone = State()

class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# ==================== УДАЛЕНИЕ СТАРЫХ СООБЩЕНИЙ ====================
last_message_ids = {}

async def safe_delete(chat_id, message_id):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except:
        pass

async def delete_previous(chat_id, context):
    if chat_id in last_message_ids:
        await safe_delete(chat_id, last_message_ids[chat_id])
        del last_message_ids[chat_id]

async def send_or_edit_message(target, text, parse_mode=None, reply_markup=None):
    if isinstance(target, CallbackQuery):
        chat_id = target.message.chat.id
        bot = target.bot
        await delete_previous(chat_id, bot)
        sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
        last_message_ids[chat_id] = sent.message_id
    elif isinstance(target, Message):
        chat_id = target.chat.id
        bot = target.bot
        await delete_previous(chat_id, bot)
        sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
        last_message_ids[chat_id] = sent.message_id
    else:
        raise TypeError("target должен быть Message или CallbackQuery")

async def delete_user_message(message: Message):
    try:
        await message.delete()
    except:
        pass

# ==================== КЛАВИАТУРЫ ====================
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Записаться", callback_data="book")
    builder.button(text="💇‍♀️ Услуги", callback_data="services")
    builder.button(text="📋 Мои записи", callback_data="my_appointments")
    builder.button(text="⭐ Отзывы", callback_data="reviews")
    builder.button(text="📝 Оставить отзыв", callback_data="write_review")
    builder.button(text="📱 Канал с отзывами", callback_data="reviews_channel")
    builder.adjust(2)
    return builder.as_markup()

def admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Записи на сегодня", callback_data="admin_today")
    builder.button(text="🗓 Записи на завтра", callback_data="admin_tomorrow")
    builder.button(text="📝 Все активные записи", callback_data="admin_all")
    builder.button(text="✅ Отзывы на модерацию", callback_data="admin_pending_reviews")
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(2)
    return builder.as_markup()

def services_keyboard():
    services = db_query("SELECT id, name, price FROM services", fetch_all=True)
    builder = InlineKeyboardBuilder()
    for s in services:
        builder.button(text=f"{s['name']} - {s['price']} ₽", callback_data=f"service_{s['id']}")
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()

def pending_reviews_keyboard():
    reviews = db_query("SELECT id, user_id, text FROM reviews WHERE status='pending'", fetch_all=True)
    builder = InlineKeyboardBuilder()
    for r in reviews:
        user = db_query("SELECT full_name, username FROM users WHERE telegram_id=?", (r['user_id'],), fetch_one=True)
        name = user['full_name'] or user['username'] or str(r['user_id'])
        builder.button(text=f"{name}: {r['text'][:30]}...", callback_data=f"review_{r['id']}")
    builder.button(text="🔙 Назад", callback_data="back_admin")
    builder.adjust(1)
    return builder.as_markup()

def review_action_keyboard(review_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Опубликовать", callback_data=f"publish_review_{review_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_review_{review_id}")
    builder.button(text="🔙 Назад", callback_data="admin_pending_reviews")
    builder.adjust(2)
    return builder.as_markup()

def channel_link_keyboard():
    channel_url = f"https://t.me/c/{str(REVIEW_CHANNEL_ID)[4:]}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть канал с отзывами", url=channel_url)]
    ])

def back_button(callback_data="back_main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data=callback_data)]
    ])

# ==================== ОБРАБОТЧИКИ ====================
async def cancel_booking(message: Message, state: FSMContext):
    await state.clear()
    await delete_user_message(message)
    await send_or_edit_message(message, f"{EMOJI_CHECKMARK} Бронирование отменено.", parse_mode="HTML", reply_markup=main_menu_keyboard())

async def start_command(message: Message, state: FSMContext):
    await state.clear()
    await delete_user_message(message)
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    db_query("INSERT OR IGNORE INTO users (telegram_id, username, full_name) VALUES (?, ?, ?)",
             (user_id, username, full_name))
    await send_or_edit_message(message,
        f"{EMOJI_CHECKMARK} Добро пожаловать! {EMOJI_CHECKMARK}\n\n"
        "Я бот визажиста. Вы можете записаться на услуги.\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )

async def main_menu_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data == "book":
        await start_booking(callback.message, state)
    elif callback.data == "services":
        await show_services(callback)
    elif callback.data == "my_appointments":
        await show_my_appointments(callback, callback.from_user.id)
    elif callback.data == "reviews":
        await show_reviews(callback)
    elif callback.data == "write_review":
        await start_review(callback.message, state)
    elif callback.data == "reviews_channel":
        await send_reviews_channel_link(callback)
    elif callback.data == "back_main":
        await send_or_edit_message(callback, "Выберите действие:", parse_mode="HTML", reply_markup=main_menu_keyboard())
    elif callback.data == "back_admin":
        await send_or_edit_message(callback, "Админ-панель:", parse_mode="HTML", reply_markup=admin_keyboard())

async def show_services(callback: CallbackQuery):
    services = db_query("SELECT name, price FROM services", fetch_all=True)
    text = "💇‍♀️ Наши услуги:\n\n"
    for s in services:
        text += f"• {s['name']} — {s['price']} ₽\n"
    await send_or_edit_message(callback, text, parse_mode=None, reply_markup=main_menu_keyboard())

async def show_my_appointments(callback: CallbackQuery, user_id: int):
    appointments = db_query("""
        SELECT a.appointment_date, a.appointment_time, s.name as service_name, a.status 
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        WHERE a.user_id = ? AND a.status IN ('pending', 'confirmed')
        ORDER BY a.appointment_date, a.appointment_time
    """, (user_id,), fetch_all=True)
    if not appointments:
        await send_or_edit_message(callback, "У вас пока нет активных записей.", parse_mode="HTML", reply_markup=main_menu_keyboard())
        return
    text = f"{EMOJI_CALENDAR} Ваши записи:\n\n"
    for app in appointments:
        text += f"{EMOJI_CALENDAR} {app['appointment_date']} {EMOJI_CLOCK} {app['appointment_time']}\n"
        text += f"💇 {app['service_name']}\n"
        text += f"Статус: {app['status']}\n\n"
    await send_or_edit_message(callback, text, parse_mode="HTML", reply_markup=main_menu_keyboard())

async def show_reviews(callback: CallbackQuery):
    reviews = db_query("""
        SELECT r.text, r.photo_file_id, u.full_name 
        FROM reviews r 
        JOIN users u ON r.user_id = u.telegram_id 
        WHERE r.status = 'published' 
        ORDER BY r.published_at DESC LIMIT 5
    """, fetch_all=True)
    if not reviews:
        await send_or_edit_message(callback, "Пока нет отзывов. Станьте первым!", parse_mode="HTML", reply_markup=main_menu_keyboard())
        return
    text = "⭐ Последние отзывы:\n\n"
    for r in reviews:
        text += f"⭐ {r['full_name']}\n{r['text']}\n\n"
        if r['photo_file_id']:
            # фото отправляем отдельно, но для простоты пока опускаем
            pass
    await send_or_edit_message(callback, text, parse_mode="HTML", reply_markup=main_menu_keyboard())

# ==================== ОТЗЫВЫ (СБОР) ====================
async def start_review(message: Message, state: FSMContext):
    await delete_user_message(message)
    await state.set_state(ReviewState.waiting_for_text)
    await send_or_edit_message(message,
        "✍️ Напишите ваш отзыв о работе визажиста.\n\n"
        "Вы можете поделиться впечатлениями, а после этого прикрепить фото (необязательно).\n\n"
        "Нажмите /cancel, чтобы отменить.",
        parse_mode="HTML",
        reply_markup=back_button("back_main")
    )

async def review_text_received(message: Message, state: FSMContext):
    await delete_user_message(message)
    await state.update_data(review_text=message.text)
    await send_or_edit_message(message,
        "📸 Теперь отправьте фото результата (или нажмите /skip, чтобы пропустить).\n\n"
        "Нажмите /cancel, чтобы отменить.",
        parse_mode="HTML",
        reply_markup=back_button("back_main")
    )
    await state.set_state(ReviewState.waiting_for_photo)

async def review_photo_received(message: Message, state: FSMContext):
    await delete_user_message(message)
    if message.photo:
        file_id = message.photo[-1].file_id
        await state.update_data(photo_file_id=file_id)
    else:
        await state.update_data(photo_file_id=None)

    data = await state.get_data()
    user_id = message.from_user.id

    db_query("""
        INSERT INTO reviews (user_id, text, photo_file_id, status)
        VALUES (?, ?, ?, 'pending')
    """, (user_id, data['review_text'], data.get('photo_file_id')))

    await send_or_edit_message(message,
        f"{EMOJI_CHECKMARK} Спасибо! Ваш отзыв отправлен на модерацию и будет опубликован после проверки.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )
    await state.clear()

async def review_skip_photo(message: Message, state: FSMContext):
    if message.text == "/skip":
        await delete_user_message(message)
        await state.update_data(photo_file_id=None)
        data = await state.get_data()
        user_id = message.from_user.id
        db_query("""
            INSERT INTO reviews (user_id, text, photo_file_id, status)
            VALUES (?, ?, ?, 'pending')
        """, (user_id, data['review_text'], None))
        await send_or_edit_message(message,
            f"{EMOJI_CHECKMARK} Спасибо! Ваш отзыв отправлен на модерацию.",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
        await state.clear()

async def send_reviews_channel_link(callback: CallbackQuery):
    await send_or_edit_message(callback,
        "📢 Здесь публикуются отзывы наших клиентов после модерации.",
        parse_mode="HTML",
        reply_markup=channel_link_keyboard()
    )

# ==================== БРОНИРОВАНИЕ (FSM) ====================
async def start_booking(message: Message, state: FSMContext):
    await delete_user_message(message)
    services = db_query("SELECT id, name FROM services", fetch_all=True)
    if not services:
        await send_or_edit_message(message, "Услуги временно недоступны.", parse_mode="HTML", reply_markup=main_menu_keyboard())
        return
    builder = InlineKeyboardBuilder()
    for s in services:
        builder.button(text=s['name'], callback_data=f"service_{s['id']}")
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    await send_or_edit_message(message, "Выберите услугу:", reply_markup=builder.as_markup())
    await state.set_state(BookingState.choosing_service)

async def service_chosen(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    service_id = int(callback.data.split("_")[1])
    await state.update_data(service_id=service_id)
    await callback.message.edit_text(
        f"{EMOJI_CALENDAR} Введите дату в формате ДД.ММ.ГГГГ (например, 25.12.2025):\n"
        f"{EMOJI_CLOCK} Время будет указано по Чите (UTC+9)",
        parse_mode="HTML",
        reply_markup=back_button("book")
    )
    await state.set_state(BookingState.choosing_date)

async def date_chosen(message: Message, state: FSMContext):
    await delete_user_message(message)
    date_str = message.text.strip()
    if not re.match(r"\d{2}\.\d{2}\.\d{4}", date_str):
        await send_or_edit_message(message, "Неверный формат. Введите дату как ДД.ММ.ГГГГ", parse_mode="HTML", reply_markup=back_button("book"))
        return
    try:
        selected_date = datetime.strptime(date_str, "%d.%m.%Y").date()
        if selected_date < date.today():
            await send_or_edit_message(message, "Дата не может быть в прошлом. Выберите другую.", parse_mode="HTML", reply_markup=back_button("book"))
            return
        if selected_date.weekday() not in WORK_DAYS:
            await send_or_edit_message(message, "В этот день я не работаю. Выберите другой день.", parse_mode="HTML", reply_markup=back_button("book"))
            return
    except ValueError:
        await send_or_edit_message(message, "Некорректная дата.", parse_mode="HTML", reply_markup=back_button("book"))
        return
    await state.update_data(date=date_str)
    free_times = [f"{h}:00" for h in range(WORK_START_HOUR, WORK_END_HOUR)]
    builder = InlineKeyboardBuilder()
    for t in free_times:
        builder.button(text=t, callback_data=f"time_{t}")
    builder.button(text="🔙 Назад", callback_data="book")
    builder.adjust(1)
    await send_or_edit_message(message,
        f"{EMOJI_CLOCK} Выберите время (по Чите):\nДоступные слоты: {WORK_START_HOUR}:00 – {WORK_END_HOUR-1}:00",
        reply_markup=builder.as_markup()
    )
    await state.set_state(BookingState.choosing_time)

async def time_chosen(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    time_str = callback.data.split("_")[1]
    await state.update_data(time=time_str)
    await callback.message.edit_text(
        "Введите ваше имя (как к вам обращаться):",
        parse_mode="HTML",
        reply_markup=back_button("book")
    )
    await state.set_state(BookingState.choosing_name)

async def name_entered(message: Message, state: FSMContext):
    await delete_user_message(message)
    name = message.text.strip()
    if not name:
        await send_or_edit_message(message, "Пожалуйста, введите имя.", parse_mode="HTML", reply_markup=back_button("book"))
        return
    await state.update_data(name=name)
    await send_or_edit_message(message, f"{EMOJI_PHONE} Введите ваш номер телефона для связи:", parse_mode="HTML", reply_markup=back_button("book"))
    await state.set_state(BookingState.entering_phone)

async def phone_entered(message: Message, state: FSMContext):
    await delete_user_message(message)
    phone = message.text.strip()
    if not re.search(r"\d", phone):
        await send_or_edit_message(message, "Пожалуйста, введите корректный номер телефона.", parse_mode="HTML", reply_markup=back_button("book"))
        return
    await state.update_data(phone=phone)
    data = await state.get_data()
    user_id = message.from_user.id

    db_query("UPDATE users SET full_name=?, phone=? WHERE telegram_id=?", (data['name'], phone, user_id))
    db_query("""
        INSERT INTO appointments (user_id, service_id, appointment_date, appointment_time)
        VALUES (?, ?, ?, ?)
    """, (user_id, data['service_id'], data['date'], data['time']))

    await send_or_edit_message(message,
        f"{EMOJI_CHECKMARK} Ваша запись создана! Ожидайте подтверждения администратора.",
        parse_mode="HTML",
        reply_markup=back_button("back_main")
    )
    await state.clear()

    # Уведомление администратору
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                f"{EMOJI_CALENDAR} Новая запись от {data['name']}\n"
                f"{EMOJI_PHONE} {phone}\n"
                f"Услуга: {db_query('SELECT name FROM services WHERE id=?', (data['service_id'],), fetch_one=True)['name']}\n"
                f"Дата: {data['date']} {EMOJI_CLOCK} {data['time']} (Чита)",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin_id}: {e}")

# ==================== АДМИНИСТРИРОВАНИЕ ====================
async def admin_command(message: Message):
    await delete_user_message(message)
    if message.from_user.id not in ADMIN_IDS:
        await send_or_edit_message(message, "У вас нет прав администратора.", parse_mode="HTML")
        return
    await send_or_edit_message(message, "Админ-панель:", parse_mode="HTML", reply_markup=admin_keyboard())

async def admin_today(callback: CallbackQuery):
    today_str = date.today().strftime("%d.%m.%Y")
    appointments = db_query("""
        SELECT a.appointment_time, s.name as service_name, u.full_name, u.phone
        FROM appointments a 
        JOIN services s ON a.service_id = s.id 
        JOIN users u ON a.user_id = u.telegram_id 
        WHERE a.appointment_date=? AND a.status!='completed'
        ORDER BY a.appointment_time
    """, (today_str,), fetch_all=True)
    if not appointments:
        await callback.message.edit_text(f"На сегодня ({today_str}) записей нет.", parse_mode="HTML", reply_markup=admin_keyboard())
        return
    text = f"{EMOJI_CALENDAR} Записи на {today_str}:\n\n"
    for app in appointments:
        text += f"{EMOJI_CLOCK} {app['appointment_time']} – {app['service_name']}\n"
        text += f"👤 {app['full_name']}\n"
        text += f"{EMOJI_PHONE} {app['phone']}\n\n"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

async def admin_tomorrow(callback: CallbackQuery):
    tomorrow_str = (date.today() + timedelta(days=1)).strftime("%d.%m.%Y")
    appointments = db_query("""
        SELECT a.appointment_time, s.name as service_name, u.full_name, u.phone
        FROM appointments a 
        JOIN services s ON a.service_id = s.id 
        JOIN users u ON a.user_id = u.telegram_id 
        WHERE a.appointment_date=? AND a.status!='completed'
        ORDER BY a.appointment_time
    """, (tomorrow_str,), fetch_all=True)
    if not appointments:
        await callback.message.edit_text(f"На завтра ({tomorrow_str}) записей нет.", parse_mode="HTML", reply_markup=admin_keyboard())
        return
    text = f"{EMOJI_CALENDAR} Записи на {tomorrow_str}:\n\n"
    for app in appointments:
        text += f"{EMOJI_CLOCK} {app['appointment_time']} – {app['service_name']}\n"
        text += f"👤 {app['full_name']}\n"
        text += f"{EMOJI_PHONE} {app['phone']}\n\n"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

async def admin_all(callback: CallbackQuery):
    appointments = db_query("""
        SELECT a.appointment_date, a.appointment_time, s.name as service_name, u.full_name, u.phone
        FROM appointments a 
        JOIN services s ON a.service_id = s.id 
        JOIN users u ON a.user_id = u.telegram_id 
        WHERE a.status IN ('pending', 'confirmed')
        ORDER BY a.appointment_date, a.appointment_time
    """, fetch_all=True)
    if not appointments:
        await callback.message.edit_text("Нет активных записей.", parse_mode="HTML", reply_markup=admin_keyboard())
        return
    text = f"{EMOJI_CALENDAR} Все активные записи:\n\n"
    for app in appointments:
        text += f"{EMOJI_CALENDAR} {app['appointment_date']} {EMOJI_CLOCK} {app['appointment_time']} – {app['service_name']}\n"
        text += f"👤 {app['full_name']}\n"
        text += f"{EMOJI_PHONE} {app['phone']}\n\n"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

async def admin_pending_reviews(callback: CallbackQuery):
    await callback.message.edit_text("Выберите отзыв для модерации:", parse_mode="HTML", reply_markup=pending_reviews_keyboard())

async def review_detail(callback: CallbackQuery):
    review_id = int(callback.data.split("_")[1])
    review = db_query("SELECT text, photo_file_id, user_id FROM reviews WHERE id=?", (review_id,), fetch_one=True)
    if not review:
        await callback.answer("Отзыв не найден", show_alert=True)
        return
    user = db_query("SELECT full_name, username FROM users WHERE telegram_id=?", (review['user_id'],), fetch_one=True)
    name = user['full_name'] or user['username'] or "Клиент"
    text = f"✍️ Отзыв от {name}\n\n{review['text']}"
    if review['photo_file_id']:
        await callback.message.answer_photo(photo=review['photo_file_id'], caption=text, parse_mode="HTML")
    else:
        await callback.message.answer(text, parse_mode="HTML")
    await callback.message.answer("Что делаем с отзывом?", parse_mode="HTML", reply_markup=review_action_keyboard(review_id))

async def publish_review(callback: CallbackQuery):
    review_id = int(callback.data.split("_")[2])
    review = db_query("SELECT text, photo_file_id, user_id FROM reviews WHERE id=?", (review_id,), fetch_one=True)
    if not review:
        await callback.answer("Отзыв не найден", show_alert=True)
        return
    db_query("UPDATE reviews SET status='published', published_at=CURRENT_TIMESTAMP WHERE id=?", (review_id,))
    user = db_query("SELECT full_name FROM users WHERE telegram_id=?", (review['user_id'],), fetch_one=True)
    name = user['full_name'] if user else "Клиент"
    caption = f"⭐ Отзыв от {name}\n\n{review['text']}"
    try:
        if review['photo_file_id']:
            await callback.bot.send_photo(chat_id=REVIEW_CHANNEL_ID, photo=review['photo_file_id'], caption=caption, parse_mode="HTML")
        else:
            await callback.bot.send_message(chat_id=REVIEW_CHANNEL_ID, text=caption, parse_mode="HTML")
        await callback.message.answer("✅ Отзыв опубликован в канале.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка публикации в канал: {e}")
        await callback.message.answer("❌ Не удалось опубликовать отзыв. Проверьте права бота в канале.", parse_mode="HTML")
        return
    await callback.message.edit_text("Модерация завершена.", parse_mode="HTML", reply_markup=admin_keyboard())

async def reject_review(callback: CallbackQuery):
    review_id = int(callback.data.split("_")[2])
    db_query("UPDATE reviews SET status='rejected' WHERE id=?", (review_id,))
    await callback.message.answer("❌ Отзыв отклонён.", parse_mode="HTML")
    await callback.message.edit_text("Модерация завершена.", parse_mode="HTML", reply_markup=admin_keyboard())

# ==================== ЗАПУСК ====================
async def main():
    global bot
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(start_command, Command("start"))
    dp.message.register(cancel_booking, Command("cancel"))
    dp.message.register(admin_command, Command("admin"))
    dp.callback_query.register(main_menu_callback, F.data.in_({"book", "services", "my_appointments", "reviews", "write_review", "reviews_channel", "back_main", "back_admin"}))
    dp.callback_query.register(admin_today, F.data == "admin_today")
    dp.callback_query.register(admin_tomorrow, F.data == "admin_tomorrow")
    dp.callback_query.register(admin_all, F.data == "admin_all")
    dp.callback_query.register(admin_pending_reviews, F.data == "admin_pending_reviews")
    dp.callback_query.register(review_detail, F.data.startswith("review_"))
    dp.callback_query.register(publish_review, F.data.startswith("publish_review_"))
    dp.callback_query.register(reject_review, F.data.startswith("reject_review_"))
    dp.callback_query.register(service_chosen, F.data.startswith("service_"))
    dp.callback_query.register(time_chosen, F.data.startswith("time_"))
    dp.message.register(date_chosen, BookingState.choosing_date)
    dp.message.register(name_entered, BookingState.choosing_name)
    dp.message.register(phone_entered, BookingState.entering_phone)
    dp.message.register(review_text_received, ReviewState.waiting_for_text)
    dp.message
