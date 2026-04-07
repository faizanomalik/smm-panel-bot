import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import json
import os
import threading
import queue
import time
import sys

# --- 1. CONFIGURATION (Environment Variables) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SMM_API_KEY = os.environ.get("SMM_API_KEY")
SMM_URL = os.environ.get("SMM_URL", "https://easysmmpanel.com/api/v2")

# Safely handle the Admin ID
admin_env = os.environ.get("ADMIN_ID")
ADMIN_ID = int(admin_env) if admin_env and admin_env.isdigit() else 0

# Failsafe: Prevent crash if Railway variables aren't set yet
if not BOT_TOKEN or not SMM_API_KEY or ADMIN_ID == 0:
    print("CRITICAL ERROR: Missing Environment Variables!")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# --- 2. MEMORY & DATABASE (Railway Persistent Storage Fix) ---
# We use an environment variable for the file path, defaulting to the local folder if not set
DATA_FILE = os.environ.get("DATA_PATH", "bot_data.json")

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"allowed_users": [ADMIN_ID], "channels": {}}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    # Ensure the directory exists before saving (Crucial for Railway Volumes)
    os.makedirs(os.path.dirname(os.path.abspath(DATA_FILE)), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def is_allowed(user_id):
    data = load_data()
    return user_id in data["allowed_users"]

# --- 3. CRASH-PROOF SMM API FUNCTIONS ---
def api_request(payload):
    try:
        response = requests.post(SMM_URL, data=payload, timeout=15)
        return response.json()
    except Exception as e:
        print(f"API Connection Error: {e}")
        return {"error": "Could not connect to SMM Panel."}

def get_services():
    return api_request({"key": SMM_API_KEY, "action": "services"})

def place_order(service_id, link, quantity):
    return api_request({
        "key": SMM_API_KEY,
        "action": "add",
        "service": service_id,
        "link": link,
        "quantity": quantity
    })

def check_balance():
    return api_request({"key": SMM_API_KEY, "action": "balance"})

def check_status(order_id):
    return api_request({"key": SMM_API_KEY, "action": "status", "order": order_id})

# --- 4. THE BACKGROUND QUEUE SYSTEM ---
order_queue = queue.Queue()

def queue_worker():
    while True:
        order_data = order_queue.get() 
        post_link = order_data["link"]
        
        response = place_order(
            service_id=order_data["service_id"],
            link=post_link,
            quantity=order_data["quantity"]
        )
        
        if "order" in response:
            bot.send_message(ADMIN_ID, f"🚀 <b>Auto-Order Placed!</b>\nLink: {post_link}\nOrder ID: {response['order']}", parse_mode="HTML")
        else:
            bot.send_message(ADMIN_ID, f"⚠️ <b>Auto-Order Failed!</b>\nLink: {post_link}\nError: {response.get('error', 'Unknown Error')}", parse_mode="HTML")
        
        time.sleep(10) 
        order_queue.task_done()

threading.Thread(target=queue_worker, daemon=True).start()

# --- 5. USER MANAGEMENT (/adduser, /users) ---
@bot.message_handler(commands=['adduser'])
def add_new_user(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "Only the Master Admin can use this command.")
        return
    try:
        new_user_id = int(message.text.split()[1])
        data = load_data()
        if new_user_id not in data["allowed_users"]:
            data["allowed_users"].append(new_user_id)
            save_data(data)
            bot.reply_to(message, f"✅ User ID {new_user_id} has been granted access.")
        else:
            bot.reply_to(message, "⚠️ This user is already on the allowed list.")
    except (IndexError, ValueError):
        bot.reply_to(message, "❌ Incorrect format. Please use: `/adduser <Telegram_ID>`")

@bot.message_handler(commands=['users'])
def manage_users(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "Only the Master Admin can use this command.")
        return

    data = load_data()
    users = data.get("allowed_users", [])

    if len(users) <= 1:
        bot.reply_to(message, "You are the only user with access right now.")
        return

    markup = InlineKeyboardMarkup()
    for uid in users:
        if uid != ADMIN_ID:  
            markup.add(InlineKeyboardButton(f"❌ Remove ID: {uid}", callback_data=f"rmuser_{uid}"))

    bot.reply_to(message, "👥 <b>Authorized Users:</b>\nClick a button below to revoke their access instantly.", parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rmuser_'))
def remove_user_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Access Denied.")
        return

    uid_to_remove = int(call.data.split('_')[1])
    data = load_data()

    if uid_to_remove in data["allowed_users"]:
        data["allowed_users"].remove(uid_to_remove)
        save_data(data)
        bot.answer_callback_query(call.id, f"User {uid_to_remove} removed!")
        
        users = data.get("allowed_users", [])
        if len(users) <= 1:
            bot.edit_message_text("No extra users have access.", call.message.chat.id, call.message.message_id)
        else:
            markup = InlineKeyboardMarkup()
            for uid in users:
                if uid != ADMIN_ID:
                    markup.add(InlineKeyboardButton(f"❌ Remove ID: {uid}", callback_data=f"rmuser_{uid}"))
            bot.edit_message_text("👥 <b>Authorized Users:</b>\nClick a button below to revoke their access instantly.", call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=markup)

# --- 6. CORE COMMANDS & PAGINATION ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_allowed(message.from_user.id):
        bot.reply_to(message, "Access Denied.")
        return
    
    welcome_text = (
        "Welcome to the Admin Dashboard!\n\n"
        "<b>Orders & Info:</b>\n"
        "/services - View SMM Panel services\n"
        "/balance - Check panel funds\n"
        "/status - Check an order status\n\n"
        "<b>Automation:</b>\n"
        "/setup_auto - Add a new channel for auto-orders\n"
        "/channels - View or remove active channels\n\n"
        "<b>Admin Tools (Master Only):</b>\n"
        "/adduser - Grant access to a new Telegram ID\n"
        "/users - View and remove users"
    )
    bot.reply_to(message, welcome_text, parse_mode="HTML")

@bot.message_handler(commands=['balance'])
def show_balance(message):
    if not is_allowed(message.from_user.id):
        return
    bot.send_message(message.chat.id, "Checking balance...")
    bal_data = check_balance()
    if "balance" in bal_data:
        bot.reply_to(message, f"💰 <b>Current Balance:</b> {bal_data['balance']} {bal_data['currency']}", parse_mode="HTML")
    else:
        bot.reply_to(message, f"❌ Error fetching balance: {bal_data.get('error', 'Unknown Error')}")

@bot.message_handler(commands=['status'])
def order_status_start(message):
    if not is_allowed(message.from_user.id):
        return
    msg = bot.reply_to(message, "Please enter the Order ID you want to check:")
    bot.register_next_step_handler(msg, process_status_step)

def process_status_step(message):
    order_id = message.text.strip()
    status_data = check_status(order_id)
    if "status" in status_data:
        text = f"📦 <b>Order ID:</b> {order_id}\n📊 <b>Status:</b> {status_data['status']}\n📉 <b>Remains:</b> {status_data.get('remains', 'N/A')}\n💲 <b>Charge:</b> {status_data.get('charge', 'N/A')} {status_data.get('currency', '')}"
        bot.reply_to(message, text, parse_mode="HTML")
    else:
        bot.reply_to(message, f"❌ Error checking status: {status_data.get('error', 'Incorrect Order ID')}")

def get_services_page(page_num):
    services = get_services()
    if "error" in services:
        return "❌ Could not reach the SMM Panel. Please try again later.", None
    items_per_page = 10
    total_pages = (len(services) + items_per_page - 1) // items_per_page
    start_idx = page_num * items_per_page
    end_idx = start_idx + items_per_page
    page_services = services[start_idx:end_idx]
    
    text = f"📋 <b>Available Services (Page {page_num + 1}/{total_pages}):</b>\n\n"
    for srv in page_services:
        text += f"ID: {srv['service']} | {srv['name']} | Rate: ${srv['rate']}\n\n"
        
    markup = InlineKeyboardMarkup()
    buttons = []
    if page_num > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page_{page_num - 1}"))
    if page_num < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page_num + 1}"))
    if buttons:
        markup.row(*buttons)
    return text, markup

@bot.message_handler(commands=['services'])
def list_services(message):
    if not is_allowed(message.from_user.id):
        return
    bot.send_message(message.chat.id, "Fetching services...")
    text, markup = get_services_page(0)
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('page_'))
def handle_pagination(call):
    if not is_allowed(call.from_user.id):
        return
    page_num = int(call.data.split('_')[1])
    text, markup = get_services_page(page_num)
    bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=text, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)

# --- 7. AUTO-ORDER CHANNELS MANAGEMENT (/setup_auto, /channels) ---
@bot.message_handler(commands=['setup_auto'])
def setup_auto_start(message):
    if not is_allowed(message.from_user.id):
        return
    instruction_text = "Let's set up an auto-order channel.\n<b>Please provide ONE of the following:</b>\n1. Forward a message from the channel\n2. The channel link (e.g., https://t.me/yourchannel)\n3. The channel @username"
    msg = bot.reply_to(message, instruction_text, parse_mode="HTML")
    bot.register_next_step_handler(msg, process_channel_step)

def process_channel_step(message):
    channel_id = None
    channel_name = None

    if message.forward_from_chat and message.forward_from_chat.type == 'channel':
        channel_id = str(message.forward_from_chat.id)
        channel_name = message.forward_from_chat.username
    elif message.text:
        text = message.text.strip()
        username_to_check = None
        if text.startswith('https://t.me/'):
            username_to_check = "@" + text.split('t.me/')[-1]
        elif text.startswith('t.me/'):
            username_to_check = "@" + text.split('t.me/')[-1]
        elif text.startswith('@'):
            username_to_check = text
        
        if username_to_check:
            try:
                chat_info = bot.get_chat(username_to_check)
                if chat_info.type == 'channel':
                    channel_id = str(chat_info.id)
                    channel_name = chat_info.username
                else:
                    bot.reply_to(message, "❌ That username belongs to a user or group, not a channel. Setup cancelled.")
                    return
            except Exception:
                bot.reply_to(message, "❌ Could not find that channel. Make sure it is public and the spelling is correct, or add me as an Admin to the channel first. Setup cancelled.")
                return

    if not channel_id or not channel_name:
        bot.reply_to(message, "❌ I couldn't detect a valid public channel from your message. Please try again. Setup cancelled.")
        return
    
    msg = bot.reply_to(message, f"✅ Channel detected: @{channel_name}\n<b>Now, enter the SMM Service ID you want to trigger automatically:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_service_step, channel_id, channel_name)

def process_service_step(message, channel_id, channel_name):
    service_id = message.text.strip()
    if not service_id.isdigit():
        bot.reply_to(message, "Service ID must be a number. Setup cancelled.")
        return
    msg = bot.reply_to(message, f"Service ID set to {service_id}.\n<b>Finally, enter the quantity per post:</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_quantity_step, channel_id, channel_name, service_id)

def process_quantity_step(message, channel_id, channel_name, service_id):
    quantity = message.text.strip()
    if not quantity.isdigit():
        bot.reply_to(message, "Quantity must be a number. Setup cancelled.")
        return
    
    data = load_data()
    data["channels"][channel_id] = {
        "username": channel_name,
        "service_id": int(service_id),
        "quantity": int(quantity)
    }
    save_data(data)
    bot.reply_to(message, f"✅ Setup complete for @{channel_name}!\nService ID: {service_id}\nQuantity: {quantity}")

@bot.message_handler(commands=['channels'])
def manage_channels(message):
    if not is_allowed(message.from_user.id):
        return

    data = load_data()
    channels = data.get("channels", {})

    if not channels:
        bot.reply_to(message, "No channels are currently set up for auto-orders.")
        return

    markup = InlineKeyboardMarkup()
    text = "📢 <b>Active Auto-Order Channels:</b>\n\n"
    
    for cid, info in channels.items():
        username = info.get("username", "Unknown")
        text += f"• <b>@{username}</b> (Service: {info['service_id']} | Qty: {info['quantity']})\n"
        markup.add(InlineKeyboardButton(f"❌ Remove @{username}", callback_data=f"rmchan_{cid}"))

    bot.reply_to(message, text, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rmchan_'))
def remove_channel_callback(call):
    if not is_allowed(call.from_user.id):
        bot.answer_callback_query(call.id, "Access Denied.")
        return

    cid_to_remove = call.data.split('_')[1]
    data = load_data()

    if cid_to_remove in data.get("channels", {}):
        del data["channels"][cid_to_remove]
        save_data(data)
        bot.answer_callback_query(call.id, "Channel removed successfully!")

        channels = data.get("channels", {})
        if not channels:
            bot.edit_message_text("No channels are currently set up for auto-orders.", call.message.chat.id, call.message.message_id)
        else:
            markup = InlineKeyboardMarkup()
            text = "📢 <b>Active Auto-Order Channels:</b>\n\n"
            for cid, info in channels.items():
                username = info.get("username", "Unknown")
                text += f"• <b>@{username}</b> (Service: {info['service_id']} | Qty: {info['quantity']})\n"
                markup.add(InlineKeyboardButton(f"❌ Remove @{username}", callback_data=f"rmchan_{cid}"))
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=markup)
    else:
        bot.answer_callback_query(call.id, "Channel not found.")

# --- 8. BACKGROUND CHANNEL LISTENER ---
@bot.channel_post_handler(func=lambda message: True)
def handle_channel_post(message):
    channel_id = str(message.chat.id)
    data = load_data()
    
    if channel_id in data["channels"]:
        channel_info = data["channels"][channel_id]
        username = channel_info["username"]
        post_id = message.message_id
        
        post_link = f"https://t.me/{username}/{post_id}"
        
        order_queue.put({
            "service_id": channel_info["service_id"],
            "link": post_link,
            "quantity": channel_info["quantity"]
        })

print("Bot successfully connected and running...")
bot.infinity_polling()