import asyncio
import sqlite3
from datetime import datetime, timedelta
import httpx
from cachetools import TTLCache
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ================== НАСТРОЙКИ ==================

TELEGRAM_TOKEN = "7728656883:AAEme2lmHObvqMOoifogEYRiy3LTyk2W5bE"
FOOTBALL_DATA_TOKEN = "ec0171bdf2db4f6baf095fb95ce0deb0"

LEAGUES = {
    "apl": {"id": "PL", "name": "АПЛ", "logo": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "laliga": {"id": "PD", "name": "Ла Лига", "logo": "🇪🇸"},
    "bundesliga": {"id": "BL1", "name": "Бундеслига", "logo": "🇩🇪"},
    "seriea": {"id": "SA", "name": "Серия А", "logo": "🇮🇹"},
    "ucl": {"id": "CL", "name": "Лига Чемпионов", "logo": "🏆"}
}

# Кэш для разных типов данных
cache = {
    'standings': TTLCache(maxsize=50, ttl=900),
    'matches': TTLCache(maxsize=100, ttl=300),
    'live': TTLCache(maxsize=20, ttl=30),
}

UTC_TZ = pytz.UTC
MSK_TZ = pytz.timezone('Europe/Moscow')

# ================== БАЗА ДАННЫХ ==================

conn = sqlite3.connect("football_bot.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER, team TEXT)")
cursor.execute("CREATE TABLE IF NOT EXISTS goal_subscriptions (user_id INTEGER, match_id INTEGER, PRIMARY KEY (user_id, match_id))")
conn.commit()

# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С API ==================

async def fetch_matches(competition_id, date_from, date_to):
    cache_key = f"matches_{competition_id}_{date_from}_{date_to}"
    if cache_key in cache['matches']:
        return cache['matches'][cache_key]

    url = "https://api.football-data.org/v4/matches"
    params = {
        "competitions": competition_id,
        "dateFrom": date_from,
        "dateTo": date_to
    }
    headers = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                data = resp.json()
                matches = data.get("matches", [])
                cache['matches'][cache_key] = matches
                return matches
            else:
                print(f"⚠️ Ошибка API матчей: {resp.status_code}")
                return []
    except Exception as e:
        print(f"❌ Ошибка запроса матчей: {e}")
        return []

async def fetch_standings(competition_id):
    cache_key = f"standings_{competition_id}"
    if cache_key in cache['standings']:
        return cache['standings'][cache_key]

    url = f"https://api.football-data.org/v4/competitions/{competition_id}/standings"
    headers = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if "standings" in data and len(data["standings"]) > 0:
                    table = data["standings"][0]["table"]
                    cache['standings'][cache_key] = table
                    return table
            print(f"⚠️ Ошибка таблицы: {resp.status_code}")
            return []
    except Exception as e:
        print(f"❌ Ошибка standings: {e}")
        return []

async def fetch_live_matches():
    cache_key = "live_matches"
    if cache_key in cache['live']:
        return cache['live'][cache_key]

    url = "https://api.football-data.org/v4/matches"
    params = {"status": "LIVE"}
    headers = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                data = resp.json()
                matches = data.get("matches", [])
                cache['live'][cache_key] = matches
                return matches
            else:
                print(f"⚠️ Ошибка API live: {resp.status_code}")
                return []
    except Exception as e:
        print(f"❌ Ошибка запроса live: {e}")
        return []

# ================== ВРЕМЯ ==================

def utc_to_msk(utc_time_str):
    try:
        if utc_time_str.endswith('Z'):
            utc_time_str = utc_time_str[:-1] + '+00:00'
        utc_dt = datetime.fromisoformat(utc_time_str)
        if utc_dt.tzinfo is None:
            utc_dt = UTC_TZ.localize(utc_dt)
        msk_dt = utc_dt.astimezone(MSK_TZ)
        return msk_dt
    except Exception as e:
        print(f"❌ Ошибка преобразования времени: {e}")
        return None

# ================== ДАННЫЕ ЛИГИ ЧЕМПИОНОВ 2025/26 ==================

UCL_PLAYOFF = {
    "round_of_16": {
        "name": "1/8 финала (первые матчи)",
        "dates": "10–11 марта 2026",
        "matches": [
            {"home": "Реал Мадрид", "away": "Манчестер Сити", "agg": "3:0", "first": "3:0"},
            {"home": "ПСЖ", "away": "Челси", "agg": "5:2", "first": "5:2"},
            {"home": "Бавария", "away": "Аталанта", "agg": "6:1", "first": "6:1"},
            {"home": "Атлетико Мадрид", "away": "Тоттенхэм", "agg": "5:2", "first": "5:2"},
            {"home": "Буде-Глимт", "away": "Спортинг", "agg": "3:0", "first": "3:0"},
            {"home": "Галатасарай", "away": "Ливерпуль", "agg": "1:0", "first": "1:0"},
            {"home": "Ньюкасл", "away": "Барселона", "agg": "1:1", "first": "1:1"},
            {"home": "Байер", "away": "Арсенал", "agg": "1:1", "first": "1:1"}
        ]
    },
    "quarterfinals": {
        "name": "1/4 финала",
        "dates": "1–2 и 8–9 апреля 2026",
        "matches": [{"info": "Жеребьёвка после 1/8 финала"}]
    },
    "semifinals": {
        "name": "1/2 финала",
        "dates": "22–23 и 29–30 апреля 2026",
        "matches": [{"info": "Пары определятся позже"}]
    },
    "final": {
        "name": "ФИНАЛ",
        "date": "30 мая 2026, Будапешт",
        "match": {"info": "Финалисты станут известны позднее"}
    }
}

# ================== МЕНЮ ==================

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏴󠁧󠁢󠁥󠁮󠁧󠁿 АПЛ", callback_data="league_apl"),
         InlineKeyboardButton("🇪🇸 Ла Лига", callback_data="league_laliga")],
        [InlineKeyboardButton("🇩🇪 Бундеслига", callback_data="league_bundesliga"),
         InlineKeyboardButton("🇮🇹 Серия А", callback_data="league_seriea")],
        [InlineKeyboardButton("🏆 Лига Чемпионов", callback_data="league_ucl")],
        [InlineKeyboardButton("🔴 LIVE матчи", callback_data="live")],
        [InlineKeyboardButton("⚽ LIVE голы", callback_data="goal_live")],
        [InlineKeyboardButton("⭐ Мои подписки", callback_data="my_subs")]
    ])

def league_menu(league_key):
    league = LEAGUES[league_key]
    if league_key == "ucl":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🏆 Плей-офф 2025/26", callback_data="ucl_playoff")],
            [InlineKeyboardButton("📅 Матчи (48ч)", callback_data=f"matches_{league_key}")],
            [InlineKeyboardButton("📊 Таблица", callback_data=f"table_{league_key}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Матчи (48ч)", callback_data=f"matches_{league_key}")],
            [InlineKeyboardButton("📊 Таблица", callback_data=f"table_{league_key}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ])

# ================== START ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ <b>Футбольный бот PRO</b>\n\n<i>Выберите лигу:</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )

# ================== МАТЧИ ЗА 48 ЧАСОВ ==================

async def matches_next_48h(update, league_key):
    league = LEAGUES[league_key]
    date_from = datetime.now().strftime("%Y-%m-%d")
    date_to = (datetime.now() + timedelta(hours=48)).strftime("%Y-%m-%d")

    cache_key = f"matches_{league['id']}_{date_from}_{date_to}"
    cached_matches = cache['matches'].get(cache_key)
    if cached_matches is not None:
        matches = cached_matches
        loading_msg = None
    else:
        loading_msg = await update.message.reply_text(f"⏳ Загружаю матчи {league['name']}...")
        matches = await fetch_matches(league["id"], date_from, date_to)

    if not matches:
        text = f"📅 <b>{league['logo']} {league['name']}</b>\n\n<i>Нет матчей с {date_from} по {date_to}</i>"
        if loading_msg:
            await loading_msg.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    text = f"{league['logo']} <b>МАТЧИ {league['name']}</b>\n"
    text += f"<i>{date_from} – {date_to} (МСК)</i>\n\n"

    for match in matches:
        msk_time = utc_to_msk(match["utcDate"])
        if msk_time:
            time_str = msk_time.strftime("%H:%M")
            date_str = msk_time.strftime("%d.%m")
        else:
            time_str = "??:??"
            date_str = "??.??"

        home = match["homeTeam"]["name"]
        away = match["awayTeam"]["name"]
        status = match["status"]

        if status == "FINISHED":
            score_h = match["score"]["fullTime"]["home"] or 0
            score_a = match["score"]["fullTime"]["away"] or 0
            text += f"✅ {date_str} {time_str}  <b>{home}</b> {score_h}-{score_a} <b>{away}</b>\n"
        elif status in ["IN_PLAY", "PAUSED"]:
            text += f"🔴 {date_str} {time_str}  <b>{home}</b> vs <b>{away}</b> (в игре)\n"
        else:
            text += f"⏳ {date_str} {time_str}  <b>{home}</b> vs <b>{away}</b>\n"

    if loading_msg:
        await loading_msg.edit_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ================== ТАБЛИЦА ==================

async def show_table(update, league_key):
    league = LEAGUES[league_key]

    cache_key = f"standings_{league['id']}"
    cached_table = cache['standings'].get(cache_key)
    if cached_table is not None:
        table = cached_table
        loading_msg = None
    else:
        loading_msg = await update.message.reply_text(f"⏳ Загружаю таблицу {league['name']}...")
        table = await fetch_standings(league["id"])

    if not table:
        text = f"📊 <b>{league['logo']} {league['name']}</b>\n\n<i>Нет данных таблицы</i>"
        if loading_msg:
            await loading_msg.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    text = f"{league['logo']} <b>ТАБЛИЦА {league['name']}</b>\n\n"
    for row in table[:10]:
        team = row["team"]["name"]
        pos = row["position"]
        pts = row["points"]
        played = row["playedGames"]
        won = row["won"]
        draw = row["draw"]
        lost = row["lost"]
        text += f"<b>{pos}.</b> {team}\n   {pts} очков | И:{played} В:{won} Н:{draw} П:{lost}\n\n"

    if loading_msg:
        await loading_msg.edit_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ================== LIVE МАТЧИ (ОБЩИЙ СПИСОК) ==================

async def live_matches(update):
    cache_key = "live_matches"
    cached = cache['live'].get(cache_key)
    if cached is not None:
        matches = cached
        loading_msg = None
    else:
        loading_msg = await update.message.reply_text("⏳ Загружаю live‑матчи...")
        matches = await fetch_live_matches()

    if not matches:
        text = "🔴 <b>LIVE матчи</b>\n\n<i>Сейчас нет матчей в прямом эфире</i>"
        if loading_msg:
            await loading_msg.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    text = "🔴 <b>LIVE МАТЧИ</b>\n\n"
    for match in matches:
        league_name = match.get("competition", {}).get("name", "Неизвестная лига")
        home = match["homeTeam"]["name"]
        away = match["awayTeam"]["name"]
        status = match["status"]
        score_h = match["score"]["fullTime"]["home"] or match["score"]["halfTime"]["home"] or 0
        score_a = match["score"]["fullTime"]["away"] or match["score"]["halfTime"]["away"] or 0
        minute = match.get("minute", "")
        if not minute and "IN_PLAY" in status:
            minute = "идет"
        elif status == "PAUSED":
            minute = "перерыв"
        else:
            minute = ""

        text += f"⚽ <b>{home}</b> {score_h}–{score_a} <b>{away}</b>"
        if minute:
            text += f"  ({minute})"
        text += f"\n   <i>{league_name}</i>\n\n"

    if loading_msg:
        await loading_msg.edit_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ================== LIVE ГОЛЫ (ПОДПИСКА) ==================

async def goal_live_menu(update):
    matches = await fetch_live_matches()
    if not matches:
        await update.message.reply_text(
            "⚽ <b>LIVE голы</b>\n\n<i>Сейчас нет матчей в прямом эфире</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu()
        )
        return

    text = "⚽ <b>Выберите матч для подписки на голы:</b>\n\n"
    keyboard = []
    for match in matches:
        match_id = match["id"]
        home = match["homeTeam"]["name"]
        away = match["awayTeam"]["name"]
        league = match.get("competition", {}).get("name", "Неизвестная лига")
        text += f"• {home} vs {away} ({league})\n"
        keyboard.append([InlineKeyboardButton(
            f"🔔 {home} – {away}",
            callback_data=f"goal_sub_{match_id}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def goal_subscribe(update, match_id):
    user_id = update.from_user.id
    try:
        cursor.execute("INSERT OR IGNORE INTO goal_subscriptions (user_id, match_id) VALUES (?, ?)", (user_id, match_id))
        conn.commit()
        await update.message.reply_text(
            f"✅ Вы подписались на уведомления о голах в этом матче!",
            reply_markup=main_menu()
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка подписки: {e}")

async def goal_unsubscribe(update, match_id):
    user_id = update.from_user.id
    cursor.execute("DELETE FROM goal_subscriptions WHERE user_id=? AND match_id=?", (user_id, match_id))
    conn.commit()
    await update.message.reply_text(
        f"❌ Вы отписались от уведомлений о голах в этом матче.",
        reply_markup=main_menu()
    )

# ================== ПРОСМОТР ПОДПИСОК НА ГОЛЫ ==================

async def my_goal_subscriptions(update, user_id):
    cursor.execute("SELECT match_id FROM goal_subscriptions WHERE user_id=?", (user_id,))
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text(
            "⚽ <b>Ваши подписки на голы</b>\n\n<i>У вас нет подписок.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu()
        )
        return

    # Для отображения названий команд нужно получить информацию о матчах (можно из кэша или запросить)
    # Упростим: покажем только ID матчей, или сделаем доп. запрос. Но для демо используем ID.
    text = "⚽ <b>Ваши подписки на голы:</b>\n\n"
    keyboard = []
    for (match_id,) in rows:
        text += f"• Матч ID: {match_id}\n"
        keyboard.append([InlineKeyboardButton(f"❌ Отписаться от матча {match_id}", callback_data=f"goal_unsub_{match_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================== ЛИГА ЧЕМПИОНОВ – ПЛЕЙ-ОФФ ==================

async def ucl_playoff(update):
    text = "🏆 <b>ЛИГА ЧЕМПИОНОВ 2025/26 – ПЛЕЙ-ОФФ</b>\n\n"

    r16 = UCL_PLAYOFF["round_of_16"]
    text += f"<b>{r16['name']}</b>  ({r16['dates']})\n"
    for m in r16["matches"]:
        text += f"   {m['home']} – {m['away']}  {m['agg']} ({m['first']})\n"
    text += "\n"

    qf = UCL_PLAYOFF["quarterfinals"]
    text += f"<b>{qf['name']}</b>  ({qf['dates']})\n"
    for m in qf["matches"]:
        text += f"   {m['info']}\n"
    text += "\n"

    sf = UCL_PLAYOFF["semifinals"]
    text += f"<b>{sf['name']}</b>  ({sf['dates']})\n"
    for m in sf["matches"]:
        text += f"   {m['info']}\n"
    text += "\n"

    final = UCL_PLAYOFF["final"]
    text += f"<b>{final['name']}</b>  ({final['date']})\n"
    text += f"   {final['match']['info']}\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ================== ПОДПИСКИ (на команды) ==================

async def subscribe_team(user_id, team):
    cursor.execute("SELECT * FROM subscriptions WHERE user_id=? AND team=?", (user_id, team))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO subscriptions VALUES (?,?)", (user_id, team))
        conn.commit()
        return True
    return False

async def unsubscribe_team(user_id, team):
    cursor.execute("DELETE FROM subscriptions WHERE user_id=? AND team=?", (user_id, team))
    conn.commit()

async def my_subscriptions(update, user_id):
    cursor.execute("SELECT team FROM subscriptions WHERE user_id=?", (user_id,))
    subs = [row[0] for row in cursor.fetchall()]

    cursor.execute("SELECT match_id FROM goal_subscriptions WHERE user_id=?", (user_id,))
    goal_subs = [row[0] for row in cursor.fetchall()]

    if not subs and not goal_subs:
        await update.message.reply_text(
            "⭐ <b>У вас нет подписок</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu()
        )
        return

    text = "⭐ <b>МОИ ПОДПИСКИ</b>\n\n"
    if subs:
        text += "<b>Команды:</b>\n"
        for team in subs:
            text += f"• {team}\n"
        text += "\n"
    if goal_subs:
        text += "<b>Матчи (уведомления о голах):</b>\n"
        for mid in goal_subs:
            text += f"• ID матча: {mid}\n"
        text += "\n"

    keyboard = []
    if subs:
        for team in subs:
            keyboard.append([InlineKeyboardButton(f"❌ Отписаться от команды {team}", callback_data=f"unsub_team_{team}")])
    if goal_subs:
        for mid in goal_subs:
            keyboard.append([InlineKeyboardButton(f"❌ Отписаться от матча {mid}", callback_data=f"goal_unsub_{mid}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================== ОБРАБОТЧИК КНОПОК ==================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "back_to_main":
        await query.message.reply_text(
            "<b>Выберите лигу:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu()
        )
        return

    if data.startswith("league_"):
        league_key = data.replace("league_", "")
        league = LEAGUES[league_key]
        await query.message.reply_text(
            f"{league['logo']} <b>{league['name']}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=league_menu(league_key)
        )
        return

    if data.startswith("matches_"):
        league_key = data.replace("matches_", "")
        await matches_next_48h(query, league_key)
        return

    if data.startswith("table_"):
        league_key = data.replace("table_", "")
        await show_table(query, league_key)
        return

    if data == "ucl_playoff":
        await ucl_playoff(query)
        return

    if data == "live":
        await live_matches(query)
        return

    if data == "goal_live":
        await goal_live_menu(query)
        return

    if data.startswith("goal_sub_"):
        match_id = int(data.replace("goal_sub_", ""))
        await goal_subscribe(query, match_id)
        return

    if data.startswith("goal_unsub_"):
        match_id = int(data.replace("goal_unsub_", ""))
        await goal_unsubscribe(query, match_id)
        return

    if data == "my_subs":
        await my_subscriptions(query, user_id)
        return

    if data.startswith("sub_team_"):
        team = data.replace("sub_team_", "")
        if await subscribe_team(user_id, team):
            await query.message.reply_text(
                f"✅ <b>Подписка на команду {team} оформлена!</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu()
            )
        else:
            await query.message.reply_text(
                f"ℹ️ <b>Вы уже подписаны на {team}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu()
            )
        return

    if data.startswith("unsub_team_"):
        team = data.replace("unsub_team_", "")
        await unsubscribe_team(user_id, team)
        await query.message.reply_text(
            f"❌ <b>Отписка от команды {team} выполнена</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu()
        )
        return

# ================== ФОНОВАЯ ЗАДАЧА ПРОВЕРКИ МАТЧЕЙ ==================

last_scores = {}
notified_start = set()

async def match_checker(app):
    print("🔄 Запущен проверщик матчей (голы и старты)")
    while True:
        try:
            matches = await fetch_live_matches()
            for match in matches:
                fixture_id = match["id"]
                home = match["homeTeam"]["name"]
                away = match["awayTeam"]["name"]
                status = match["status"]
                hs = match["score"]["fullTime"]["home"] or match["score"]["halfTime"]["home"] or 0
                aw = match["score"]["fullTime"]["away"] or match["score"]["halfTime"]["away"] or 0
                score = f"{hs}-{aw}"

                # Уведомление о старте матча (если только начался)
                if status in ["IN_PLAY", "LIVE"] and fixture_id not in notified_start:
                    cursor.execute("SELECT user_id FROM goal_subscriptions WHERE match_id=?", (fixture_id,))
                    users = cursor.fetchall()
                    for (user_id,) in users:
                        try:
                            await app.bot.send_message(
                                chat_id=user_id,
                                text=f"🏁 <b>Матч начался!</b>\n\n{home} vs {away}",
                                parse_mode=ParseMode.HTML
                            )
                        except Exception as e:
                            print(f"Ошибка отправки уведомления о старте: {e}")
                    notified_start.add(fixture_id)

                # Уведомление о голе (изменение счёта)
                if fixture_id not in last_scores:
                    last_scores[fixture_id] = score

                if last_scores[fixture_id] != score:
                    cursor.execute("SELECT user_id FROM goal_subscriptions WHERE match_id=?", (fixture_id,))
                    users = cursor.fetchall()
                    for (user_id,) in users:
                        try:
                            await app.bot.send_message(
                                chat_id=user_id,
                                text=f"⚽ <b>ГОЛ!</b>\n\n{home} {hs}-{aw} {away}",
                                parse_mode=ParseMode.HTML
                            )
                        except Exception as e:
                            print(f"Ошибка отправки уведомления о голе: {e}")
                    last_scores[fixture_id] = score

        except Exception as e:
            print(f"Ошибка в match_checker: {e}")

        await asyncio.sleep(30)

# ================== ЗАПУСК ==================

def main():
    print("=" * 60)
    print("⚽ ФУТБОЛЬНЫЙ БОТ PRO (с live‑матчами и уведомлениями о голах)")
    print("=" * 60)
    print(f"✅ База данных: football_bot.db")
    print("✅ Асинхронный, с кэшированием, время МСК")
    print("✅ Добавлены live‑матчи и подписка на голы")
    print("=" * 60)

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Запускаем фоновую задачу в том же цикле
    loop = asyncio.get_event_loop()
    loop.create_task(match_checker(app))

    print("🚀 Бот запущен! Откройте Telegram и отправьте /start")
    app.run_polling()

if __name__ == "__main__":
    main()