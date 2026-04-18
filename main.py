import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.upload import VkUpload
import telebot
import threading
import sqlite3
import os
import sys
import random
import requests
import time
from datetime import datetime, timedelta

# --- НАСТРОЙКИ (ВСТАВЬ СВОИ ДАННЫЕ) ---
VK_TOKEN = ""
TG_BOT_TOKEN = ""
TG_CHAT_ID = ""
TARGET_CHAT_NAME = ""
DB_FILE = "data/users.db"

# --- ИНИЦИАЛИЗАЦИЯ БД ---
def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, vk_name TEXT)')
    cursor.execute('''CREATE TABLE IF NOT EXISTS jobs 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                       tg_id INTEGER, 
                       text TEXT, 
                       send_at TIMESTAMP)''')
    conn.commit()
    conn.close()

def get_user_name(tg_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT vk_name FROM users WHERE tg_id = ?', (tg_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else None

# --- ИНИЦИАЛИЗАЦИЯ КЛИЕНТОВ ---
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
upload = VkUpload(vk_session)
tg_bot = telebot.TeleBot(TG_BOT_TOKEN)
target_peer_id = None

# --- ФУНКЦИИ ОТПРАВКИ ---

def send_to_vk_final(sender_name, text, attachments=""):
    """Универсальная отправка в ВК"""
    if target_peer_id:
        try:
            vk.messages.send(
                peer_id=target_peer_id,
                random_id=random.randint(1, 2147483647),
                message=f"От: {sender_name}\nСообщение: {text}",
                attachment=attachments
            )
        except Exception as e:
            print(f"Ошибка отправки в ВК: {e}")

def send_media_to_tg(name, text, attachments, chat_id, is_test=False):
    """Универсальная отправка из ВК в ТГ (с загрузкой файлов)"""
    prefix = "✅ [ТЕСТ ПРИ ЗАПУСКЕ]\n" if is_test else ""
    
    if text:
        tg_bot.send_message(chat_id, f"{prefix}От: {name}\nСообщение: {text}")
    elif is_test and not attachments:
        tg_bot.send_message(chat_id, f"{prefix}От: {name}\n[Пустое сообщение]")

    for a in attachments:
        try:
            if a['type'] == 'photo':
                best_url = max(a['photo']['sizes'], key=lambda s: s['width'])['url']
                tg_bot.send_photo(chat_id, best_url, caption=f"📸 Фото от {name}")
            elif a['type'] == 'doc':
                r = requests.get(a['doc']['url'], stream=True)
                fname = a['doc']['title']
                t_path = f"data/dl_{random.randint(1,999)}_{fname}"
                with open(t_path, 'wb') as f:
                    for chunk in r.iter_content(8192): f.write(chunk)
                with open(t_path, 'rb') as doc:
                    tg_bot.send_document(chat_id, doc, caption=f"📎 Файл от {name}: {fname}")
                os.remove(t_path)
        except Exception as e:
            tg_bot.send_message(chat_id, f"❌ Ошибка вложения: {e}")

# --- ФОНОВЫЙ ПЛАНИРОВЩИК ---
def scheduler_worker():
    while True:
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('SELECT id, tg_id, text FROM jobs WHERE send_at <= ?', (now_str,))
            tasks = cursor.fetchall()
            for j_id, tg_id, txt in tasks:
                name = get_user_name(tg_id) or "Инкогнито"
                send_to_vk_final(f"{name} ⏰", txt)
                cursor.execute('DELETE FROM jobs WHERE id = ?', (j_id,))
                conn.commit()
            conn.close()
        except Exception as e: print(f"БД Ошибка: {e}")
        time.sleep(30)

# --- ОБРАБОТЧИКИ ТЕЛЕГРАМ ---

@tg_bot.message_handler(commands=['help'])
def handle_help(message):
    help_text = (
        "📖 **ПОЛНЫЙ ГАЙД (VK ↔ TG)**\n\n"
        "🔹 **/name [Имя]** — регистрация подписи.\n"
        "🔹 **/say [Текст]** — мгновенная отправка.\n"
        "🔹 **/later [Время] [Текст]** — отложенная отправка.\n"
        "   • `/later 15:00 Текст` (на время)\n"
        "   • `/later +30 Текст` (через 30 мин)\n"
        "🔹 **Медиа** — прикрепите фото/файл и напишите `/say Текст` в подписи.\n\n"
        "⚙️ **00:00:** Бот перезагружается для обслуживания и присылает тест связи."
    )
    tg_bot.reply_to(message, help_text, parse_mode='Markdown')

@tg_bot.message_handler(commands=['name'])
def handle_name(message):
    parts = message.text.split(' ', 1)
    if len(parts) < 2:
        tg_bot.reply_to(message, "⚠️ Напишите: /name Ваше Имя")
        return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO users (tg_id, vk_name) VALUES (?, ?)', (message.from_user.id, parts[1]))
    conn.commit()
    conn.close()
    tg_bot.reply_to(message, f"✅ Имя {parts[1]} сохранено!")

@tg_bot.message_handler(commands=['later'])
def handle_later(message):
    name = get_user_name(message.from_user.id)
    if not name:
        tg_bot.reply_to(message, "❌ Сначала введите /name")
        return
    try:
        parts = message.text.split(' ', 2)
        t_val, content = parts[1], parts[2]
        now = datetime.now()
        if t_val.startswith('+'):
            send_at = now + timedelta(minutes=int(t_val[1:]))
        else:
            t_obj = datetime.strptime(t_val, "%H:%M").time()
            send_at = datetime.combine(now.date(), t_obj)
            if send_at < now: send_at += timedelta(days=1)
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO jobs (tg_id, text, send_at) VALUES (?, ?, ?)', 
                       (message.from_user.id, content, send_at.strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()
        tg_bot.reply_to(message, f"🕒 Запланировано на {send_at.strftime('%H:%M')}")
    except:
        tg_bot.reply_to(message, "⚠️ Формат: `/later 15:00 Текст`", parse_mode='Markdown')

@tg_bot.message_handler(content_types=['text', 'photo', 'document'])
def handle_tg_to_vk(message):
    if str(message.chat.id) != str(TG_CHAT_ID): return
    text = message.text or message.caption
    if not text or not text.startswith('/say'): return
    name = get_user_name(message.from_user.id)
    if not name:
        tg_bot.reply_to(message, "❌ Введите /name")
        return
    
    msg_text = text.split(' ', 1)[1] if len(text.split(' ', 1)) > 1 else ""
    atts = []
    try:
        f_info = None
        if message.photo:
            f_info = tg_bot.get_file(message.photo[-1].file_id)
            ext, is_doc = ".jpg", False
        elif message.document:
            f_info = tg_bot.get_file(message.document.file_id)
            ext, is_doc = os.path.splitext(message.document.file_name)[1], True
        
        if f_info:
            r = requests.get(f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{f_info.file_path}")
            t_name = f"data/up_{random.randint(1,999)}{ext}"
            with open(t_name, 'wb') as f: f.write(r.content)
            if is_doc:
                d = upload.document_messages(t_name, title=message.document.file_name, peer_id=target_peer_id)
                atts.append(f"doc{d['doc']['owner_id']}_{d['doc']['id']}")
            else:
                p = upload.photo_messages(t_name)[0]
                atts.append(f"photo{p['owner_id']}_{p['id']}")
            os.remove(t_name)
        
        send_to_vk_final(name, msg_text, ",".join(atts))
        tg_bot.reply_to(message, "✅ В ВК!")
    except Exception as e: tg_bot.reply_to(message, f"❌ Ошибка: {e}")

# --- ВК ЛОГИКА ---
def vk_listener():
    lp = VkLongPoll(vk_session)
    for event in lp.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.peer_id == target_peer_id and not event.from_me:
            u = vk.users.get(user_ids=event.user_id)[0]
            name = f"{u['first_name']} {u['last_name']}"
            msg = vk.messages.getById(message_ids=[event.message_id])['items'][0]
            send_media_to_tg(name, event.text, msg.get('attachments', []), TG_CHAT_ID)

def main():
    global target_peer_id
    init_db()
    convs = vk.messages.getConversations(count=100)['items']
    for c in convs:
        if c['conversation'].get('chat_settings', {}).get('title') == TARGET_CHAT_NAME:
            target_peer_id = c['conversation']['peer']['id']
            break
    if not target_peer_id: sys.exit("Чат не найден")

    # Умный тест
    history = vk.messages.getHistory(peer_id=target_peer_id, count=1)['items'][0]
    u_id = history.get('from_id')
    u_name = "Система"
    if u_id > 0:
        res = vk.users.get(user_ids=u_id)[0]
        u_name = f"{res['first_name']} {res['last_name']}"
    send_media_to_tg(u_name, history.get('text'), history.get('attachments', []), TG_CHAT_ID, is_test=True)

    threading.Thread(target=lambda: tg_bot.infinity_polling(), daemon=True).start()
    threading.Thread(target=scheduler_worker, daemon=True).start()
    vk_listener()

if __name__ == '__main__':
    main()
