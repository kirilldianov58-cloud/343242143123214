#!/usr/bin/env python3
"""
Telegram-бот для визажиста
Версия: 1.1 – исправлена ошибка с cancel_booking
"""

import asyncio
import logging
import sqlite3
import re
from datetime import datetime, date, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== КОНФИГУРАЦИЯ (ЗАМЕНИТЕ НА СВОИ) ====================
BOT_TOKEN = "8615339487:AAE34fezdBoQ1Dof5eoCzZi4bAwMpSrdrY0"
ADMIN_IDS = [6298119477]  # Замените на свой Telegram ID
REVIEW_CHANNEL_ID = -1003884818442  # Замените на ID канала

WORK_START_HOUR = 10
WORK_END_HOUR = 20
WORK_DAYS = [0, 1, 2, 3, 4, 5, 6]

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
    cursor.execute("SELECT COUNT(*) FROM services")
    if cursor.fetchone()[0] == 0:
        services = [
            ("Свадебный макияж", 5000, 120),
            ("Вечерний макияж", 4000, 90),
            ("Дневной макияж", 3500, 60),
            ("Макияж для фотосессии", 4500, 90),
            ("Коррекция бровей", 1000, 30),
            ("Окрашивание бровей", 800, 30),
            ("Наращивание ресниц", 2000, 90),
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
    entering_phone = State()

class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# ==================== КЛАВИАТУРЫ ====================
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Записаться", callback_data="book")
    builder.button(text="💇‍♀️ Услуги", callback_data="services")
    builder.button(text="📋 Мои записи", callback_data="my_appointments")
    builder.button(text="⭐ Отзывы", callback_data="reviews")
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

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================
async def start_command(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    db_query("INSERT OR IGNORE INTO users (telegram_id, username, full_name) VALUES (?, ?, ?)",
             (user_id, username, full_name))
    await message.answer(
        "✨ Добро пожаловать! ✨\n\n"
        "Я бот визажиста. Вы можете записаться на услуги.\n\n"
        "Выберите действие:",
        reply_markup=main_menu_keyboard()
    )

async def cancel_booking(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Бронирование отменено.", reply_markup=main_menu_keyboard())

async def main_menu_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data == "book":
        await start_booking(callback.message, state)
    elif callback.data == "services":
        await show_services(callback.message)
    elif callback.data == "my_appointments":
        await show_my_appointments(callback.message, callback.from_user.id)
    elif callback.data == "reviews":
        await show_reviews(callback.message)
    elif callback.data == "back_main":
        await callback.message.edit_text("Выберите действие:", reply_markup=main_menu_keyboard())
    elif callback.data == "back_admin":
        await callback.message.edit_text("Админ-панель:", reply_markup=admin_keyboard())

async def show_services(message: Message):
    services = db_query("SELECT name, price FROM services", fetch_all=True)
    text = "💇‍♀️ *Наши услуги:*\n\n"
    for s in services:
        text += f"• {s['name']} — {s['price']} ₽\n"
    await message.answer(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def show_my_appointments(message: Message, user_id: int):
    appointments = db_query("""
        SELECT a.appointment_date, a.appointment_time, s.name as service_name, a.status 
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        WHERE a.user_id = ? AND a.status IN ('pending', 'confirmed')
        ORDER BY a.appointment_date, a.appointment_time
    """, (user_id,), fetch_all=True)
    if not appointments:
        await message.answer("У вас пока нет активных записей.", reply_markup=main_menu_keyboard())
        return
    text = "📋 *Ваши записи:*\n\n"
    for app in appointments:
        text += f"🗓 {app['appointment_date']} {app['appointment_time']}\n"
        text += f"💇 {app['service_name']}\n"
        text += f"Статус: {app['status']}\n\n"
    await message.answer(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def show_reviews(message: Message):
    reviews = db_query("""
        SELECT r.text, r.photo_file_id, u.full_name 
        FROM reviews r 
        JOIN users u ON r.user_id = u.telegram_id 
        WHERE r.status = 'published' 
        ORDER BY r.published_at DESC LIMIT 5
    """, fetch_all=True)
    if not reviews:
        await message.answer("Пока нет отзывов. Станьте первым!", reply_markup=main_menu_keyboard())
        return
    for r in reviews:
        text_review = f"⭐ *{r['full_name']}*\n{r['text']}"
        if r['photo_file_id']:
            await message.answer_photo(photo=r['photo_file_id'], caption=text_review, parse_mode="Markdown")
        else:
            await message.answer(text_review, parse_mode="Markdown")
    await message.answer("Выберите действие:", reply_markup=main_menu_keyboard())

# ==================== БРОНИРОВАНИЕ (FSM) ====================
async def start_booking(message: Message, state: FSMContext):
    services = db_query("SELECT id, name FROM services", fetch_all=True)
    if not services:
        await message.answer("Услуги временно недоступны.", reply_markup=main_menu_keyboard())
        return
    builder = InlineKeyboardBuilder()
    for s in services:
        builder.button(text=s['name'], callback_data=f"service_{s['id']}")
    builder.button(text="🔙 Назад", callback_data="back_main")
    await message.answer("Выберите услугу:", reply_markup=builder.as_markup())
    await state.set_state(BookingState.choosing_service)

async def service_chosen(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    service_id = int(callback.data.split("_")[1])
    await state.update_data(service_id=service_id)
    await callback.message.edit_text("Введите дату в формате ДД.ММ.ГГГГ (например, 25.12.2025):")
    await state.set_state(BookingState.choosing_date)

async def date_chosen(message: Message, state: FSMContext):
    date_str = message.text.strip()
    if not re.match(r"\d{2}\.\d{2}\.\d{4}", date_str):
        await message.answer("Неверный формат. Введите дату как ДД.ММ.ГГГГ")
        return
    try:
        selected_date = datetime.strptime(date_str, "%d.%m.%Y").date()
        if selected_date < date.today():
            await message.answer("Дата не может быть в прошлом. Выберите другую.")
            return
        if selected_date.weekday() not in WORK_DAYS:
            await message.answer("В этот день я не работаю. Выберите другой день.")
            return
    except ValueError:
        await message.answer("Некорректная дата.")
        return
    await state.update_data(date=date_str)
    free_times = [f"{h}:00" for h in range(WORK_START_HOUR, WORK_END_HOUR)]
    builder = InlineKeyboardBuilder()
    for t in free_times:
        builder.button(text=t, callback_data=f"time_{t}")
    builder.button(text="🔙 Назад", callback_data="back_main")
    await message.answer("Выберите время:", reply_markup=builder.as_markup())
    await state.set_state(BookingState.choosing_time)

async def time_chosen(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    time_str = callback.data.split("_")[1]
    await state.update_data(time=time_str)
    data = await state.get_data()
    service = db_query("SELECT name FROM services WHERE id=?", (data['service_id'],), fetch_one=True)
    text = f"📝 *Подтверждение записи*\n\nУслуга: {service['name']}\nДата: {data['date']}\nВремя: {time_str}\n\nДля подтверждения укажите ваш номер телефона:"
    await callback.message.edit_text(text, parse_mode="Markdown")
    await state.set_state(BookingState.entering_phone)

async def phone_entered(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not re.search(r"\d", phone):
        await message.answer("Пожалуйста, введите корректный номер телефона.")
        return
    await state.update_data(phone=phone)
    data = await state.get_data()
    user_id = message.from_user.id
    db_query("""
        INSERT INTO appointments (user_id, service_id, appointment_date, appointment_time)
        VALUES (?, ?, ?, ?)
    """, (user_id, data['service_id'], data['date'], data['time']))
    await message.answer("✅ Ваша запись создана! Ожидайте подтверждения администратора.", reply_markup=main_menu_keyboard())
    await state.clear()
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(admin_id, f"📅 Новая запись от {message.from_user.full_name} на {data['date']} {data['time']}")
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin_id}: {e}")

# ==================== АДМИНИСТРИРОВАНИЕ ====================
async def admin_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав администратора.")
        return
    await message.answer("Админ-панель:", reply_markup=admin_keyboard())

async def admin_today(callback: CallbackQuery):
    today_str = date.today().strftime("%d.%m.%Y")
    appointments = db_query("""
        SELECT a.appointment_time, s.name as service_name, u.full_name 
        FROM appointments a 
        JOIN services s ON a.service_id = s.id 
        JOIN users u ON a.user_id = u.telegram_id 
        WHERE a.appointment_date=? AND status!='completed'
        ORDER BY a.appointment_time
    """, (today_str,), fetch_all=True)
    if not appointments:
        await callback.message.edit_text(f"На сегодня ({today_str}) записей нет.", reply_markup=admin_keyboard())
        return
    text = f"📅 *Записи на {today_str}:*\n\n"
    for app in appointments:
        text += f"🕒 {app['appointment_time']} – {app['service_name']}\n👤 {app['full_name']}\n\n"
    await callback.message.edit_text(text.strip(), parse_mode="Markdown", reply_markup=admin_keyboard())

async def admin_tomorrow(callback: CallbackQuery):
    tomorrow_str = (date.today() + timedelta(days=1)).strftime("%d.%m.%Y")
    appointments = db_query("""
        SELECT a.appointment_time, s.name as service_name, u.full_name 
        FROM appointments a 
        JOIN services s ON a.service_id = s.id 
        JOIN users u ON a.user_id = u.telegram_id 
        WHERE a.appointment_date=? AND status!='completed'
        ORDER BY a.appointment_time
    """, (tomorrow_str,), fetch_all=True)
    if not appointments:
        await callback.message.edit_text(f"На завтра ({tomorrow_str}) записей нет.", reply_markup=admin_keyboard())
        return
    text = f"📅 *Записи на {tomorrow_str}:*\n\n"
    for app in appointments:
        text += f"🕒 {app['appointment_time']} – {app['service_name']}\n👤 {app['full_name']}\n\n"
    await callback.message.edit_text(text.strip(), parse_mode="Markdown", reply_markup=admin_keyboard())

async def admin_all(callback: CallbackQuery):
    appointments = db_query("""
        SELECT a.appointment_date, a.appointment_time, s.name as service_name, u.full_name 
        FROM appointments a 
        JOIN services s ON a.service_id = s.id 
        JOIN users u ON a.user_id = u.telegram_id 
        WHERE a.status IN ('pending', 'confirmed')
        ORDER BY a.appointment_date, a.appointment_time
    """, fetch_all=True)
    if not appointments:
        await callback.message.edit_text("Нет активных записей.", reply_markup=admin_keyboard())
        return
    text = "📋 *Все активные записи:*\n\n"
    for app in appointments:
        text += f"📅 {app['appointment_date']} {app['appointment_time']} – {app['service_name']}\n👤 {app['full_name']}\n\n"
    await callback.message.edit_text(text.strip(), parse_mode="Markdown", reply_markup=admin_keyboard())

async def admin_pending_reviews(callback: CallbackQuery):
    await callback.message.edit_text("Выберите отзыв для модерации:", reply_markup=pending_reviews_keyboard())

async def review_detail(callback: CallbackQuery):
    review_id = int(callback.data.split("_")[1])
    review = db_query("SELECT text, photo_file_id, user_id FROM reviews WHERE id=?", (review_id,), fetch_one=True)
    if not review:
        await callback.answer("Отзыв не найден", show_alert=True)
        return
    user = db_query("SELECT full_name, username FROM users WHERE telegram_id=?", (review['user_id'],), fetch_one=True)
    name = user['full_name'] or user['username'] or "Клиент"
    text = f"✍️ *Отзыв от {name}*\n\n{review['text']}"
    if review['photo_file_id']:
        await callback.message.answer_photo(photo=review['photo_file_id'], caption=text, parse_mode="Markdown")
    else:
        await callback.message.answer(text, parse_mode="Markdown")
    await callback.message.answer("Что делаем с отзывом?", reply_markup=review_action_keyboard(review_id))

async def publish_review(callback: CallbackQuery):
    review_id = int(callback.data.split("_")[2])
    review = db_query("SELECT text, photo_file_id, user_id FROM reviews WHERE id=?", (review_id,), fetch_one=True)
    if not review:
        await callback.answer("Отзыв не найден", show_alert=True)
        return
    db_query("UPDATE reviews SET status='published', published_at=CURRENT_TIMESTAMP WHERE id=?", (review_id,))
    user = db_query("SELECT full_name FROM users WHERE telegram_id=?", (review['user_id'],), fetch_one=True)
    name = user['full_name'] if user else "Клиент"
    caption = f"⭐ *Отзыв от {name}*\n\n{review['text']}"
    try:
        if review['photo_file_id']:
            await callback.bot.send_photo(chat_id=REVIEW_CHANNEL_ID, photo=review['photo_file_id'], caption=caption, parse_mode="Markdown")
        else:
            await callback.bot.send_message(chat_id=REVIEW_CHANNEL_ID, text=caption, parse_mode="Markdown")
        await callback.message.answer("✅ Отзыв опубликован в канале.")
    except Exception as e:
        logger.error(f"Ошибка публикации в канал: {e}")
        await callback.message.answer("❌ Не удалось опубликовать отзыв. Проверьте права бота в канале.")
        return
    await callback.message.edit_text("Модерация завершена.", reply_markup=admin_keyboard())

async def reject_review(callback: CallbackQuery):
    review_id = int(callback.data.split("_")[2])
    db_query("UPDATE reviews SET status='rejected' WHERE id=?", (review_id,))
    await callback.message.answer("❌ Отзыв отклонён.")
    await callback.message.edit_text("Модерация завершена.", reply_markup=admin_keyboard())

# ==================== ЗАПУСК ====================
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(start_command, Command("start"))
    dp.message.register(cancel_booking, Command("cancel"))
    dp.message.register(admin_command, Command("admin"))
    dp.callback_query.register(main_menu_callback, F.data.in_({"book", "services", "my_appointments", "reviews", "back_main", "back_admin"}))
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
    dp.message.register(phone_entered, BookingState.entering_phone)

    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
