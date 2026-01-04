import sys
import os
import logging
import re
import base64
import json
import requests # Need requests library
import subprocess # For Git commands
import redis # Redis library
import pickle
from datetime import datetime, timedelta

# --- CONFIGURATION & SETUP ---

base_dir = os.path.dirname(os.path.abspath(__file__))

# Try to load .env file (optional, for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass # python-dotenv not installed or not needed in prod

API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_ID')
MAINTAINER_GROUP_ID = os.getenv('MAINTAINER_GROUP_ID')
REDIS_URL = os.getenv('REDIS_URL') # Redis Connection String

# GITHUB CONFIG
GH_TOKEN = os.getenv('GITHUB_TOKEN')
GH_REPO = os.getenv('GITHUB_REPO')       # e.g., AfterlifeOS/vendor_signed
GH_PATH = os.getenv('GITHUB_FILE_PATH')  # e.g., signed.mk
GH_BRANCH = os.getenv('GITHUB_BRANCH')   # Optional: Leave empty to use Default Branch (main/master)

# SELF-UPDATE CONFIG (For templates.json only now)
BOT_REPO = os.getenv('BOT_REPO') # e.g. AfterlifeOS/maintainer-bot-source

# WELCOME LINKS
LINK_DEVICE_LIST = os.getenv('LINK_DEVICE_LIST', 'https://google.com')
LINK_BRINGUP_GUIDE = os.getenv('LINK_BRINGUP_GUIDE', 'https://google.com')

# CONFIGURATION
REJECTION_COOLDOWN_DAYS = 7 # User must wait X days after rejection to apply again

if not API_TOKEN or not ADMIN_CHAT_ID:
    print("❌ Error: Configuration missing in .env!")
    sys.exit(1)

try:
    from telegram import (
        Update, 
        ReplyKeyboardMarkup, 
        ReplyKeyboardRemove, 
        InlineKeyboardMarkup, 
        InlineKeyboardButton,
        ForceReply
    )
    from telegram.constants import ParseMode
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        filters,
        ConversationHandler,
        ContextTypes,
        CallbackQueryHandler,
        BasePersistence, 
        PersistenceInput
    )
except ImportError as e:
    print(f"❌ Error importing telegram library: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)

# --- REDIS PERSISTENCE CLASS ---
class RedisPersistence(BasePersistence):
    def __init__(self, url):
        self.redis = redis.from_url(url)
        super().__init__(store_data=PersistenceInput(bot_data=True, user_data=True, chat_data=True, callback_data=False))

    async def get_bot_data(self):
        data = self.redis.get("bot_data")
        return pickle.loads(data) if data else {}

    async def update_bot_data(self, data):
        self.redis.set("bot_data", pickle.dumps(data))

    async def refresh_bot_data(self, bot_data):
        return await self.get_bot_data()

    async def get_user_data(self):
        data = self.redis.get("user_data")
        return pickle.loads(data) if data else {}

    async def update_user_data(self, user_id, data):
        # We need to load all user data, update specific, and save back? 
        # BasePersistence structure treats user_data as a whole dict usually in memory.
        # Efficient Redis impl would use HSET, but for compatibility with BasePersistence simple dump:
        # NOTE: PTB saves all user_data as a dict {user_id: data}.
        # For simplicity and migration, we will treat the whole user_data storage as one pickle blob unless massive.
        pass # PTB Default implementation handles in-memory and calls flush() which calls update_user_data with ALL data?
             # No, update_user_data is called with (user_id, data).
             
        # Optimized for PTB: We will use a Hash Map in Redis. Key="user_data", Field=user_id
        self.redis.hset("user_data", str(user_id), pickle.dumps(data))

    async def get_chat_data(self):
        data = self.redis.get("chat_data")
        return pickle.loads(data) if data else {}

    async def update_chat_data(self, chat_id, data):
        self.redis.hset("chat_data", str(chat_id), pickle.dumps(data))
        
    async def get_callback_data(self):
        return None
    async def update_callback_data(self, data):
        pass
    async def get_conversations(self, name):
        data = self.redis.get(f"conv_{name}")
        return pickle.loads(data) if data else {}
    async def update_conversation(self, name, key, new_state):
        # Load, Update, Save
        current = await self.get_conversations(name)
        current[key] = new_state
        self.redis.set(f"conv_{name}", pickle.dumps(current))
    async def flush(self):
        pass # Redis sets are atomic/immediate enough

    # Override getting full dicts for init
    async def get_user_data(self):
        # Return all user data as {int(id): data}
        raw = self.redis.hgetall("user_data")
        return {int(k): pickle.loads(v) for k, v in raw.items()}
        
    async def get_chat_data(self):
        raw = self.redis.hgetall("chat_data")
        return {int(k): pickle.loads(v) for k, v in raw.items()}

# --- WELCOME HANDLER ---
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ensure this only runs in the configured Maintainer Group
    if str(update.effective_chat.id) != str(MAINTAINER_GROUP_ID):
        return

    for member in update.message.new_chat_members:
        # Don't welcome the bot itself
        if member.id == context.bot.id:
            continue
            
        mention = member.mention_html()
        
        msg = (
            f"👋 <b>Konnichiwa, {mention}!</b>\n"
            "Welcome to the team.\n\n"
            "Before performing your tasks, please strictly follow these points:\n\n"
            
            "<b>1. ℹ️ General Information</b>\n"
            "Check <code>/notes</code> and <code>/help</code> for specific project details.\n\n"
            
            "<b>2. 📝 Device Registration</b>\n"
            f"Please fill your device name in the <a href=\"{LINK_DEVICE_LIST}\">Device List Topic</a>.\n"
            "<i>Example:</i>\n"
            "<code>Username: @MufasaXz</code>\n"
            "<code>Device: Xiaomi Pad 6 (pipa)</code>\n\n"
            
            "<b>3. 🛠️ Bring-up Guidelines</b>\n"
            f"Refer to the <a href=\"{LINK_BRINGUP_GUIDE}\">Bring-up Trees Guide</a> for adaptation standards.\n\n"
            
            "<b>4. 🏗️ CI / Build Infrastructure</b>\n"
            "If you need to use our CI for official builds, please tag admins:\n"
            "<b>@xSkyyHinohara @Romeo_Delta_Whiskey</b> for access steps.\n\n"
            
            "<i>Enjoy your stay, Sir.</i> 🚀"
        )
        
        try:
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Failed to send welcome message: {e}")

# --- GITHUB HELPER FUNCTION ---
def add_maintainer_to_github(maintainer_alias):
    if not GH_TOKEN or not GH_REPO or not GH_PATH:
        return False, "❌ GitHub Config missing in .env"

    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # Handle custom branch if set
    params = {}
    if GH_BRANCH:
        params['ref'] = GH_BRANCH

    try:
        # 1. GET Current File
        r = requests.get(url, headers=headers, params=params)
        if r.status_code != 200:
            return False, f"❌ Failed to fetch file: {r.status_code} {r.reason}"
        
        file_data = r.json()
        sha = file_data['sha']
        content_b64 = file_data['content']
        
        # Decode content
        current_content = base64.b64decode(content_b64).decode('utf-8')
        
        # 2. Check for duplicates
        if maintainer_alias in current_content.splitlines():
             return True, "⚠️ Maintainer alias already exists in file. Skipped commit."

        # 3. Append new alias
        # Ensure we start on a new line if file doesn't end with one
        if not current_content.endswith('\n'):
            current_content += "\n"
        
        new_content = current_content + maintainer_alias + "\n"
        
        # 4. Commit (PUT)
        commit_msg = f"Add maintainer: {maintainer_alias}"
        payload = {
            "message": commit_msg,
            "content": base64.b64encode(new_content.encode('utf-8')).decode('utf-8'),
            "sha": sha
        }
        if GH_BRANCH:
            payload['branch'] = GH_BRANCH

        put_resp = requests.put(url, headers=headers, json=payload)
        
        if put_resp.status_code in [200, 201]:
            return True, f"✅ Successfully committed <b>{maintainer_alias}</b> to GitHub!"
        else:
            return False, f"❌ Commit failed: {put_resp.status_code} {put_resp.text}"

    except Exception as e:
        return False, f"❌ GitHub Error: {str(e)}"

# --- CONVERSATION STATES ---
(RULES_AGREEMENT, SOURCE_TYPE_CHECK, PRIVATE_REASON, PRIVATE_ACCESS_AGREEMENT,
 FULL_NAME, MAINTAINER_ALIAS, GITHUB_URL, DEVICE_INFO, 
 DEVICE_TREE, DEVICE_COMMON, VENDOR_TREE, VENDOR_COMMON, 
 KERNEL_SOURCE, SUPPORT_LINK, OFFICIAL_ROMS, DURATION, 
 CONTRIBUTION, WHY_JOIN, SUITABILITY) = range(19)

# --- HELPER FUNCTIONS ---
def is_valid_url(url):
    if url.lower() == 'none': return True
    pattern = re.compile(r'^https?://(www\.)?(github|gitlab|t\.me|bitbucket|gitea|codeberg)\.com/.+')
    return bool(pattern.match(url))

def format_link(url, text="Link"):
    if url.lower() == 'none' or not url:
        return "<i>None</i>"
    return f'<a href="{url}">{text}</a>'

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return ConversationHandler.END

    user = update.message.from_user
    logger.info(f"User {user.first_name} started conversation.")
    
    # 0. USERNAME CHECK (Must have @username)
    if not user.username:
        await update.message.reply_text(
            "⛔ <b>No Username Detected</b>\n\n"
            "To apply for a Maintainer position, you <b>MUST</b> set a Telegram Username first.\n"
            "This is required for us to contact you and manage permissions.\n\n"
            "<i>Please set a username in your Telegram Settings and try /start again.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # 1. COOLDOWN CHECK (Rejection Waiting Period)
    cooldowns = context.bot_data.get('rejected_cooldowns', {})
    if user.id in cooldowns:
        data = cooldowns[user.id]
        # Handle Format Compatibility
        if isinstance(data, datetime):
            expiry_date = data
        else:
            expiry_date = data.get('expiry')

        if datetime.now() < expiry_date:
            # Still in cooldown
            formatted_date = expiry_date.strftime("%Y-%m-%d %H:%M UTC")
            await update.message.reply_text(
                "⏳ <b>Application Cooldown</b>\n\n"
                "Your previous application was recently declined.\n"
                f"You must wait until <b>{formatted_date}</b> before applying again.\n\n"
                "<i>Please use this time to improve your sources or skills.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        else:
            # Cooldown expired, clean up
            del cooldowns[user.id]
            context.bot_data['rejected_cooldowns'] = cooldowns

    # Initialize bot_data storage for pending apps if not exists
    if 'pending_apps' not in context.bot_data:
        context.bot_data['pending_apps'] = {}

    # 1. ANTI-SPAM CHECK
    if user.id in context.bot_data['pending_apps']:
        await update.message.reply_text(
            "⚠️ <b>Active Application Found</b>\n\n"
            "You already have a pending application being reviewed.\n"
            "Please wait for the admin's decision before applying again.", 
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    rules = (
        "<b>🔮 AfterlifeOS Maintainer Application</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Welcome! To ensure the quality of our project, please review and accept the following requirements:\n\n"
        "1. <b>⚠️ Update Policy:</b> You must provide updates regularly.\n"
        "2. <b>🛡️ Integrity:</b> Preserve commit authorship. Force-pushes are allowed.\n"
        "3. <b>📱 Ownership:</b> You must physically own the device.\n"
        "4. <b>🔒 Confidentiality:</b> Do not leak internal resources.\n"
        "5. <b>⚙️ Infrastructure:</b> Official builds must use Afterlife CI.\n\n"
        "<i>Do you agree to these terms?</i>"
    )
    
    keyboard = [["✅ I Accept the Terms", "❌ Decline"]]
    await update.message.reply_text(
        rules, 
        parse_mode=ParseMode.HTML, 
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
        disable_web_page_preview=True
    )
    return RULES_AGREEMENT

async def rules_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "✅ I Accept the Terms":
        # New Flow: Check Source Code Privacy
        keyboard = [["🌍 Public", "🔒 Private"]]
        await update.message.reply_text(
            "<b>Source Code Availability</b>\n\n"
            "Are your device trees (Device, Vendor, Kernel) currently <b>Public</b> or <b>Private</b>?",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return SOURCE_TYPE_CHECK
    
    await update.message.reply_text("❌ <b>Application Declined.</b>", reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    return ConversationHandler.END

async def check_source_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data['source_type'] = text

    if text == "🌍 Public":
        # Proceed to normal flow
        await update.message.reply_text(
            "<b>Step 1/11: Identity</b>\n"
            "Please enter your <b>Real Name</b>:\n\n"
            "💡 <i>Example: John Doe</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
            disable_web_page_preview=True
        )
        return FULL_NAME
    elif text == "🔒 Private":
        await update.message.reply_text(
            "<b>🔒 Private Sources</b>\n\n"
            "Please explain <b>WHY</b> your sources are private at this moment:\n\n"
            "💡 <i>Example: 'It is still in early bring-up' or 'I am cleaning up the commits.'</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove()
        )
        return PRIVATE_REASON
    else:
        await update.message.reply_text(
            "⚠️ Please select one of the buttons.\n\n"
            "Are your device trees (Device, Vendor, Kernel) currently <b>Public</b> or <b>Private</b>?",
            reply_markup=ReplyKeyboardMarkup([["🌍 Public", "🔒 Private"]], one_time_keyboard=True, resize_keyboard=True),
            parse_mode=ParseMode.HTML
        )
        return SOURCE_TYPE_CHECK

async def get_private_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['private_reason'] = update.message.text
    
    keyboard = [["✅ Yes, I Agree", "❌ No, I Refuse"]]
    await update.message.reply_text(
        "<b>⚠️ Access Requirement</b>\n\n"
        "Since your sources are private, we require <b>READ ACCESS</b> to your repositories for review purposes.\n"
        "If accepted, you must invite our Lead Developers to your private repo.\n\n"
        "<b>Do you agree to provide access if requested?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return PRIVATE_ACCESS_AGREEMENT

async def check_private_agreement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "✅ Yes, I Agree":
        await update.message.reply_text(
            "<b>Step 1/11: Identity</b>\n"
            "Please enter your <b>Real Name</b>:\n\n"
            "💡 <i>Example: John Doe</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
            disable_web_page_preview=True
        )
        return FULL_NAME
    else:
        await update.message.reply_text(
            "❌ <b>Application Declined.</b>\n"
            "We cannot accept maintainers who refuse to share sources with the core team.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    await update.message.reply_text(
        "<b>Step 2/11: Identity</b>\n"
        "Please enter your <b>Maintainer Name</b> (The name that will appear in the ROM):\n\n"
        "💡 <i>Example: johndoe01</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return MAINTAINER_ALIAS

async def get_maintainer_alias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['maintainer_alias'] = update.message.text
    await update.message.reply_text(
        "<b>Step 3/11: Socials</b>\n"
        "Provide your <b>GitHub Username</b>:\n"
        "<i>(Just the username, e.g., 'johndoe')</i>\n\n"
        "💡 <i>Example: johndoe</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return GITHUB_URL

async def get_github(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_input = update.message.text.strip()
    
    # 1. CLEAN INPUT (Extract username from URL if necessary)
    # Remove 'https://', 'github.com/', trailing slashes, and '@'
    username = raw_input.replace('https://', '').replace('http://', '').replace('www.', '').replace('github.com/', '').replace('@', '').rstrip('/')
    
    # Basic Validation: Username should not contain slashes or spaces after cleaning
    if '/' in username or ' ' in username or not username:
        await update.message.reply_text("⚠️ Invalid format. Please enter just your GitHub username (e.g. <code>johndoe</code>):", parse_mode=ParseMode.HTML)
        return GITHUB_URL

    # 2. GITHUB API CHECK
    try:
        api_url = f"https://api.github.com/users/{username}"
        headers = {"Authorization": f"token {GH_TOKEN}"} if GH_TOKEN else {}
        
        r = requests.get(api_url, headers=headers, timeout=5)
        
        if r.status_code == 404:
            await update.message.reply_text(
                f"❌ <b>GitHub User Not Found!</b>\n\n"
                f"The user '<code>{username}</code>' does not exist on GitHub.\n"
                "Please check the username and try again:", 
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            return GITHUB_URL
            
        elif r.status_code != 200:
            # API Error (Rate limit, etc) - Warn but maybe allow? Or ask again.
            # Let's allow it but warn, or just retry. For safety, let's ask for retry if it's a server error.
            # But to be user friendly, if API is down, maybe we shouldn't block.
            # Let's just log and proceed if it's not a 404.
            logger.warning(f"GitHub API Error for {username}: {r.status_code}")

    except Exception as e:
        logger.warning(f"GitHub API Check failed: {e}")
        # Continue if API fails (soft fail)

    context.user_data['github_user'] = username # Save the clean username
    
    await update.message.reply_text(
        "<b>Step 4/11: Device Details</b>\n"
        "Enter <b>Device Name & Codename</b>:\n\n"
        "💡 <i>Example: Xiaomi Redmi Note 10 (mojito)</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return DEVICE_INFO

async def get_device_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['device'] = update.message.text
    await update.message.reply_text(
        "<b>Step 5/11: Source Code</b>\n"
        "1️⃣ Link to your <b>Device Tree</b>:\n\n"
        "💡 <i>Example: https://github.com/MyUser/device_xiaomi_mojito</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return DEVICE_TREE

async def get_dt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_valid_url(update.message.text):
        await update.message.reply_text("⚠️ Invalid URL. Try again:", disable_web_page_preview=True)
        return DEVICE_TREE
    context.user_data['dt'] = update.message.text
    
    await update.message.reply_text(
        "2️⃣ Link to <b>Device Common Tree</b>:\n"
        "<i>(Type 'None' if not applicable)</i>\n\n"
        "💡 <i>Example: https://github.com/MyUser/device_xiaomi_sm6115-common</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return DEVICE_COMMON

async def get_dt_common(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['dt_c'] = update.message.text
    await update.message.reply_text(
        "3️⃣ Link to <b>Vendor Tree</b>:\n\n"
        "💡 <i>Example: https://github.com/MyUser/vendor_xiaomi_mojito</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return VENDOR_TREE

async def get_vt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_valid_url(update.message.text):
        await update.message.reply_text("⚠️ Invalid URL. Try again:", disable_web_page_preview=True)
        return VENDOR_TREE
    context.user_data['vt'] = update.message.text
    
    await update.message.reply_text(
        "4️⃣ Link to <b>Vendor Common Tree</b>:\n"
        "<i>(Type 'None' if not applicable)</i>\n\n"
        "💡 <i>Example: https://github.com/MyUser/vendor_xiaomi_sm6115-common</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return VENDOR_COMMON

async def get_vt_common(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['vt_c'] = update.message.text
    await update.message.reply_text(
        "5️⃣ Link to <b>Kernel Source</b>:\n\n"
        "💡 <i>Example: https://github.com/MyUser/kernel_xiaomi_mojito</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return KERNEL_SOURCE

async def get_kernel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_valid_url(update.message.text):
        await update.message.reply_text("⚠️ Invalid URL. Try again:", disable_web_page_preview=True)
        return KERNEL_SOURCE
    context.user_data['kernel'] = update.message.text
    
    await update.message.reply_text(
        "<b>Step 6/11: Community</b>\n"
        "Provide your <b>Device Support Group/Channel</b> link:\n"
        "<i>(Type 'None' if you don't have one yet)</i>\n\n"
        "💡 <i>Example: https://t.me/Mypocox3Group</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return SUPPORT_LINK

async def get_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['support'] = update.message.text
    
    await update.message.reply_text(
        "<b>Step 7/11: Experience</b>\n"
        "How many ROMs do you currently maintain with an <b>Official</b> tag?\n\n"
        "💡 <i>Example Answer: 'Currently 2 (LineageOS and EvolutionX)' or 'None, this is my first time.'</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return OFFICIAL_ROMS

async def get_official_roms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['official_roms'] = update.message.text
    await update.message.reply_text(
        "<b>Step 8/11: Experience</b>\n"
        "How long have you been maintaining that ROM/Device?\n\n"
        "💡 <i>Example Answer: 'I have been maintaining LineageOS for 1 year and PixelExperience for 6 months.'</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return DURATION

async def get_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['duration'] = update.message.text
    await update.message.reply_text(
        "<b>Step 9/11: Source Knowledge</b>\n"
        "Are you a contributor to the device sources (DT/VT/Kernel)?\n\n"
        "❗ <b>IMPORTANT:</b>\n"
        "• If <b>YES</b>: You <u>MUST</u> provide example commit links.\n"
        "• If <b>NO</b>: Just state that you adapt/fork existing sources.\n\n"
        "💡 <i>Example Answer: 'Yes, I fixed the FOD implementation. Commit: https://github.com/.../commit/xyz'</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return CONTRIBUTION

async def get_contribution(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['contribution'] = update.message.text
    
    await update.message.reply_text(
        "<b>Step 10/11: Motivation</b>\n"
        "Why have you chosen to apply for <b>AfterlifeOS</b> specifically?\n\n"
        "💡 <i>Example Answer: 'I love the unique UI design of AfterlifeOS and I want to provide a stable build for my community.'</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return WHY_JOIN

async def get_why_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['why_join'] = update.message.text
    await update.message.reply_text(
        "<b>Step 11/11: Self Assessment</b>\n"
        "Do you feel you are a suitable addition to our team? Why?\n\n"
        "💡 <i>Example Answer: 'Yes, because I am very active, responsive to bug reports, and willing to learn new things to improve the source.'</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return SUITABILITY

async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['suitability'] = update.message.text
    user = update.message.from_user
    username = f"@{user.username}" if user.username else "No Username"
    
    data = context.user_data
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # SAVE DATA FOR ADMIN ACTION
    # We re-assign the dictionary to context.bot_data to ensure PicklePersistence detects the change
    current_apps = context.bot_data.get('pending_apps', {})
    current_apps[user.id] = {
        'maintainer_alias': data['maintainer_alias'],
        'name': data['name']
    }
    context.bot_data['pending_apps'] = current_apps

    # Construct Source Info Segment
    source_type = data.get('source_type', 'Unknown')
    source_info = f"<b>📂 SOURCE CODE ({source_type})</b>\n"
    
    if source_type == "🔒 Private":
        p_reason = data.get('private_reason', 'None provided')
        source_info += (
            f"<i>⚠️ Private Reason: \"{p_reason}\"</i>\n"
            f"<i>✅ User agreed to give read access.</i>\n"
        )
    
    source_info += (
        f"├ <b>Device Tree:</b> {format_link(data['dt'])}\n"
        f"├ <b>DT Common:</b> {format_link(data['dt_c'])}\n"
        f"├ <b>Vendor Tree:</b> {format_link(data['vt'])}\n"
        f"├ <b>VT Common:</b> {format_link(data['vt_c'])}\n"
        f"└ <b>Kernel:</b> {format_link(data['kernel'])}\n\n"
    )

    # Construct GitHub Link
    gh_user = data.get('github_user', 'Unknown')
    gh_link = f"https://github.com/{gh_user}"
    gh_display = f'<a href="{gh_link}">{gh_user}</a>'

    admin_msg = (
        "<b>🚀 NEW MAINTAINER APPLICATION</b>\n"
        f"<i>Received: {date_str}</i>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>👤 APPLICANT DETAILS</b>\n"
        f"├ <b>Name:</b> {data['name']}\n"
        f"├ <b>Maintainer Alias:</b> {data['maintainer_alias']}\n"
        f"├ <b>User:</b> {username}\n"
        f"├ <b>ID:</b> {user.id}\n"
        f"└ <b>GitHub:</b> {gh_display}\n\n"
        
        "<b>📱 DEVICE INFO</b>\n"
        f"├ <b>Model:</b> <code>{data['device']}</code>\n"
        f"└ <b>Support:</b> {format_link(data['support'], 'Group Link')}\n\n"
        
        f"{source_info}"
        
        "<b>📝 EXPERIENCE & BACKGROUND</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<b>🔰 Official ROMs:</b>\n"
        f"└ <i>{data['official_roms']}</i>\n\n"
        f"<b>⏳ Duration:</b>\n"
        f"└ <i>{data['duration']}</i>\n\n"
        f"<b>🛠 Contribution:</b>\n"
        f"└ <i>{data['contribution']}</i>\n\n"

        "<b>🎤 INTERVIEW SESSION</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<b>❓ Why AfterlifeOS?</b>\n"
        f"<i>\"{data['why_join']}\"</i>\n\n"
        f"<b>❓ Why You? (Suitability)</b>\n"
        f"<i>\"{data['suitability']}\"</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "#AfterlifeOS #Recruitment"
    )

    user_msg = (
        "✅ <b>Application Submitted!</b>\n\n"
        "Thank you for completing the interview.\n"
        "Your responses have been forwarded to the AfterlifeOS Administration.\n\n"
        "<i>We will review your application and get back to you soon.</i> 🚀"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"pre_accept:{user.id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"pre_reject:{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        sent_msg = await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, 
            text=admin_msg, 
            parse_mode=ParseMode.HTML, 
            disable_web_page_preview=True,
            reply_markup=reply_markup
        )
        
        # PIN THE MESSAGE (LOUD)
        try:
            await sent_msg.pin(disable_notification=False)
        except Exception as e:
            logger.error(f"Failed to pin message: {e}")
            
        await update.message.reply_text(user_msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        # Force Save (Redis updates automatically on flush/update, but flush ensures it)
        await context.application.persistence.flush()

    except Exception as e:
        logger.error(f"Failed to send: {e}")
        await update.message.reply_text("❌ Error sending application.")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return ConversationHandler.END

    await update.message.reply_text("🚫 <b>Operation Cancelled.</b>", parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove(), disable_web_page_preview=True)
    return ConversationHandler.END

async def get_suitability(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await finalize(update, context)

# --- GITHUB API HELPERS (No Local Git) ---
def get_github_headers():
    return {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def download_file_from_github(filename):
    """Downloads a file from the BOT_REPO and saves it locally."""
    if not GH_TOKEN or not BOT_REPO:
        logger.warning(f"⚠️ GitHub Sync Config Missing. Using local {filename} only.")
        return False

    url = f"https://api.github.com/repos/{BOT_REPO}/contents/{filename}"
    # Use default branch for Bot Data (Do not use GH_BRANCH here)
    params = {}
    
    try:
        r = requests.get(url, headers=get_github_headers(), params=params)
        if r.status_code == 200:
            content = base64.b64decode(r.json()['content'])
            
            # Write safely to local path
            local_path = os.path.join(base_dir, filename)
            # Write binary for pickle, text for json (handled by mode 'wb')
            with open(local_path, 'wb') as f:
                f.write(content)
            logger.info(f"✅ Downloaded {filename} from GitHub.")
            return True
        elif r.status_code == 404:
            logger.info(f"ℹ️ {filename} not found on GitHub. Starting fresh.")
        else:
            logger.error(f"❌ Failed to download {filename}: {r.status_code}")
    except Exception as e:
        logger.error(f"❌ Error downloading {filename}: {e}")
    return False

def upload_file_to_github(filename, commit_msg):
    """Reads a local file and uploads/updates it on BOT_REPO only if content changed."""
    if not GH_TOKEN or not BOT_REPO:
        return

    local_path = os.path.join(base_dir, filename)
    if not os.path.exists(local_path):
        return

    url = f"https://api.github.com/repos/{BOT_REPO}/contents/{filename}"
    headers = get_github_headers()
    # Use default branch for Bot Data
    params = {}

    try:
        # 1. Read Local Content
        with open(local_path, 'rb') as f:
            local_content = f.read()
        
        # 2. Get Remote Content & SHA
        sha = None
        r_get = requests.get(url, headers=headers, params=params)
        
        if r_get.status_code == 200:
            file_data = r_get.json()
            sha = file_data['sha']
            remote_content_b64 = file_data['content']
            
            # Decode and Compare
            # GitHub API adds newlines to base64 strings, clean them before decoding might be safer, 
            # but standard b64decode handles it.
            remote_content = base64.b64decode(remote_content_b64)
            
            if local_content == remote_content:
                logger.info(f"zzz {filename} unchanged. Skipping push.")
                return # EXIT EARLY - No Change
        
        # 3. Prepare Payload
        content_b64 = base64.b64encode(local_content).decode('utf-8')
        
        payload = {
            "message": commit_msg,
            "content": content_b64
        }
        if sha: payload['sha'] = sha
        
        # 4. PUT (Commit)
        r_put = requests.put(url, headers=headers, json=payload)
        if r_put.status_code in [200, 201]:
            logger.info(f"☁️ Synced {filename} to GitHub.")
        else:
            logger.error(f"❌ Sync failed for {filename}: {r_put.status_code} {r_put.text}")

    except Exception as e:
        logger.error(f"❌ Upload Error {filename}: {e}")

# Remove generic sync function to avoid mass-pushing
# def sync_data_to_cloud(): ... (Removed)

# --- TEMPLATE MANAGEMENT ---
TEMPLATES_FILE = os.path.join(base_dir, 'templates.json')

DEFAULT_TEMPLATES = {
    "source": "❌ <b>Source Code Issue:</b> The provided device/vendor trees or kernel source are incomplete, inaccessible, or do not meet our standards.",
    "ownership": "❌ <b>Device Ownership:</b> We require maintainers to physically own the device. Proof of ownership was insufficient.",
    "history": "❌ <b>Maintainer History:</b> Your maintenance history or activity levels do not meet our current requirements.",
    "quality": "❌ <b>Quality Standards:</b> The ROM stability or provided builds do not meet AfterlifeOS quality criteria.",
    "duplicate": "❌ <b>Device Taken:</b> This device already has an active maintainer. We are not looking for a co-maintainer at this time.",
    "other": "❌ <b>Application Declined:</b> Thank you for your interest, but we cannot accept your application at this time."
}

def load_templates():
    # Attempt download from Cloud first
    download_file_from_github('templates.json')
    
    if not os.path.exists(TEMPLATES_FILE):
        return DEFAULT_TEMPLATES
        
    try:
        with open(TEMPLATES_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading templates: {e}")
        return DEFAULT_TEMPLATES

def save_templates(templates):
    try:
        with open(TEMPLATES_FILE, 'w') as f:
            json.dump(templates, f, indent=4)
        
        # Trigger Cloud Sync
        upload_file_to_github('templates.json', 'Update rejection templates [Bot]')
        return True
    except Exception as e:
        logger.error(f"Error saving templates: {e}")
        return False

# Load templates into memory on start
rejection_templates = load_templates()

# --- ADMIN TEMPLATE COMMANDS ---
async def check_admin(update: Update):
    # STRICT: Allow ONLY if the message is sent IN the designated Admin Group
    if str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
        # We can optionally reply in DM saying "Go to the group", or just ignore/reject.
        if update.effective_chat.type == 'private':
            await update.message.reply_text("⛔ Admin commands can only be used in the Maintainer Admin Group.")
        return False
    return True

async def show_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin(update): return
    
    msg = "<b>📂 Current Rejection Templates:</b>\n\n"
    for key, text in rejection_templates.items():
        msg += f"🔑 <b>{key}</b>:\n{text}\n\n"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def add_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin(update): return
    
    args = context.args
    reply = update.message.reply_to_message

    # Logic: Get Key and Message
    if reply:
        # Mode Reply: Format is "/add_template <key>"
        if not args:
            await update.message.reply_text("⚠️ Usage (Reply Mode): /add_template <key>")
            return
        key = args[0]
        # Get text with HTML formatting automatically
        message = reply.text_html or reply.caption_html
        if not message:
             await update.message.reply_text("⚠️ The replied message has no text.")
             return
    else:
        # Mode Manual: Format is "/add_template <key> <text>"
        if len(args) < 2:
            await update.message.reply_text("⚠️ Usage:\n1. Reply to a formatted message: /add_template <key>\n2. Manual: /add_template <key> <html_text>")
            return
        key = args[0]
        message = ' '.join(args[1:])
    
    if key in rejection_templates:
        await update.message.reply_text(f"⚠️ Template '{key}' already exists. Use /edit_template to modify.")
        return
        
    rejection_templates[key] = message
    if save_templates(rejection_templates):
        await update.message.reply_text(f"✅ Template <b>{key}</b> added successfully!\n\n<b>Preview:</b>\n{message}", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await update.message.reply_text("❌ Failed to save to database.")

async def edit_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin(update): return
    
    args = context.args
    reply = update.message.reply_to_message

    if reply:
        if not args:
            await update.message.reply_text("⚠️ Usage (Reply Mode): /edit_template <key>")
            return
        key = args[0]
        message = reply.text_html or reply.caption_html
        if not message:
             await update.message.reply_text("⚠️ The replied message has no text.")
             return
    else:
        if len(args) < 2:
            await update.message.reply_text("⚠️ Usage:\n1. Reply to a formatted message: /edit_template <key>\n2. Manual: /edit_template <key> <html_text>")
            return
        key = args[0]
        message = ' '.join(args[1:])
    
    if key not in rejection_templates:
        await update.message.reply_text(f"⚠️ Template '{key}' not found.")
        return
        
    rejection_templates[key] = message
    if save_templates(rejection_templates):
        await update.message.reply_text(f"✅ Template <b>{key}</b> updated!\n\n<b>Preview:</b>\n{message}", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await update.message.reply_text("❌ Failed to save to database.")

async def remove_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin(update): return
    
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /remove_template <key>")
        return

    key = context.args[0]
    if key not in rejection_templates:
        await update.message.reply_text(f"⚠️ Template '{key}' not found.")
        return
        
    del rejection_templates[key]
    if save_templates(rejection_templates):
        await update.message.reply_text(f"🗑️ Template <b>{key}</b> removed.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Failed to save to database.")

async def check_cooldowns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin(update): return

    cooldowns = context.bot_data.get('rejected_cooldowns', {})
    if not cooldowns:
        await update.message.reply_text("✅ <b>No active cooldowns.</b>", parse_mode=ParseMode.HTML)
        return

    msg = "<b>⏳ Active Cooldown List:</b>\n\n"
    active_count = 0
    to_remove = []
    
    now = datetime.now()
    
    for uid, data in cooldowns.items():
        # Handle Format Compatibility
        if isinstance(data, datetime):
            expiry = data
            saved_name = "Unknown"
        else:
            expiry = data.get('expiry')
            saved_name = data.get('name', 'Unknown')
            
        if now < expiry:
            active_count += 1
            date_str = expiry.strftime("%Y-%m-%d %H:%M")
            remaining = (expiry - now).days
            
            # REAL-TIME FETCH (Get latest username)
            try:
                chat = await context.bot.get_chat(uid)
                if chat.username:
                    display_name = f"@{chat.username}"
                else:
                    display_name = chat.first_name
            except Exception:
                display_name = saved_name # Fallback if fetch fails
            
            msg += f"👤 <b>{display_name}</b> (<code>{uid}</code>)\n└ 🔓 Unlocks: {date_str} ({remaining} days left)\n\n"
        else:
            to_remove.append(uid)
    
    # Auto-cleanup expired
    if to_remove:
        for uid in to_remove:
            del cooldowns[uid]
        context.bot_data['rejected_cooldowns'] = cooldowns

    if active_count == 0:
        msg = "✅ <b>No active cooldowns.</b> (Cleaned up expired entries)"
    else:
        msg += "<i>To remove a cooldown: /remove_cooldown &lt;user_id&gt;</i>"

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def remove_cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin(update): return
    
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /remove_cooldown <user_id>")
        return
        
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid User ID. Must be a number.")
        return

    cooldowns = context.bot_data.get('rejected_cooldowns', {})
    
    if target_id in cooldowns:
        del cooldowns[target_id]
        context.bot_data['rejected_cooldowns'] = cooldowns # Trigger Save
        
        # FORCE SAVE
        await context.application.persistence.flush()
        
        await update.message.reply_text(f"✅ Cooldown removed for user <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"⚠️ User ID <code>{target_id}</code> is not in the cooldown list.", parse_mode=ParseMode.HTML)

# --- ADMIN DECISION HANDLER (DYNAMIC UI) ---
async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    
    data = query.data.split(":")
    action = data[0]
    
    # --- LOGIC HANDLING ---
    
    # 1. INITIAL REJECT CLICK -> SHOW DYNAMIC TEMPLATES
    if action == "pre_reject":
        user_id = int(data[1])
        keyboard = []
        row = []
        
        # Dynamically build buttons from loaded templates
        for key in rejection_templates.keys():
            # Create a label (Capitalize key, maybe remove underscores)
            label = key.replace('_', ' ').title()
            # If standard keys, we can add emojis (optional beautification)
            if key == 'source': label = "📦 Source"
            elif key == 'ownership': label = "📱 Owner"
            elif key == 'history': label = "🕒 History"
            elif key == 'quality': label = "📉 Quality"
            elif key == 'duplicate': label = "👯 Duplicate"
            elif key == 'other': label = "🚫 Other"
            
            row.append(InlineKeyboardButton(label, callback_data=f"sel_reason:{key}:{user_id}"))
            
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row: keyboard.append(row)
        
        # Add Cancel/Back button at the bottom
        keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data=f"reset:{user_id}")])
        
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 2. TEMPLATE SELECTED -> ASK FOR COOLDOWN
    elif action == "sel_reason":
        reason_key = data[1]
        target_uid = int(data[2])
        
        context.user_data['temp_reject_reason'] = reason_key
        context.user_data['temp_reject_uid'] = target_uid

        # Show Cooldown Options
        keyboard = [
            [
                InlineKeyboardButton("🚫 No Cooldown", callback_data=f"sel_cd:0:{target_uid}"),
                InlineKeyboardButton("3 Days", callback_data=f"sel_cd:3:{target_uid}")
            ],
            [
                InlineKeyboardButton("1 Week", callback_data=f"sel_cd:7:{target_uid}"),
                InlineKeyboardButton("2 Weeks", callback_data=f"sel_cd:14:{target_uid}"),
                InlineKeyboardButton("3 Weeks", callback_data=f"sel_cd:21:{target_uid}")
            ],
            [
                InlineKeyboardButton("1 Month", callback_data=f"sel_cd:30:{target_uid}"),
                InlineKeyboardButton("2 Months", callback_data=f"sel_cd:60:{target_uid}"),
                InlineKeyboardButton("3 Months", callback_data=f"sel_cd:90:{target_uid}")
            ],
            [InlineKeyboardButton("🔙 Back", callback_data=f"pre_reject:{target_uid}")]
        ]
        
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 3. COOLDOWN SELECTED -> ASK FOR SEND/NOTE
    elif action == "sel_cd":
        days = int(data[1])
        target_uid = int(data[2])
        
        context.user_data['temp_reject_days'] = days
        
        # Display Confirmation
        reason_key = context.user_data.get('temp_reject_reason', 'other')
        display_days = f"{days} Days" if days > 0 else "None"
        
        keyboard = [
            [InlineKeyboardButton(f"✅ Send (CD: {display_days})", callback_data=f"do_reject:send:{target_uid}")],
            [InlineKeyboardButton("📝 Add Optional Note", callback_data=f"do_reject:note:{target_uid}")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"sel_reason:{reason_key}:{target_uid}")]
        ]
        
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 4. EXECUTE REJECTION OR ASK FOR NOTE
    elif action == "do_reject":
        sub_action = data[1]
        target_uid = int(data[2])
        
        reason_key = context.user_data.get('temp_reject_reason', 'other')
        cooldown_days = context.user_data.get('temp_reject_days', 0)
        base_reason = rejection_templates.get(reason_key, "Application Declined.")

        if sub_action == "send":
            await finalize_rejection(update, context, target_uid, base_reason, None, cooldown_days=cooldown_days)
            # Unpin message
            try:
                await context.bot.unpin_chat_message(chat_id=ADMIN_CHAT_ID, message_id=query.message.message_id)
            except Exception as e:
                logger.warning(f"Could not unpin message: {e}")
            return
        
        elif sub_action == "note":
            context.bot_data[f"admin_reply_{query.from_user.id}"] = {
                'target_uid': target_uid,
                'base_reason': base_reason,
                'cooldown_days': cooldown_days,
                'msg_id': query.message.message_id, # Save MSG ID for unpinning later via reply
                'original_text': query.message.text_html
            }
            
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✍️ <b>Add Rejection Note for User {target_uid}</b>\n\n"
                     f"Selected Template: <i>{reason_key}</i>\n"
                     "Reply to this message with your additional comments.",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )
            return

    # 4. PRE-ACCEPT (Existing Logic)
    elif action == "pre_accept":
        user_id = int(data[1])
        keyboard = [
            [InlineKeyboardButton("⚠️ Confirm Accept?", callback_data=f"noop:{user_id}")],
            [
                InlineKeyboardButton("✅ Yes", callback_data=f"accept:{user_id}"),
                InlineKeyboardButton("🔙 No", callback_data=f"reset:{user_id}")
            ]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 5. RESET (Back to Main Menu)
    elif action == "reset":
        user_id = int(data[1])
        keyboard = [
            [
                InlineKeyboardButton("✅ Accept", callback_data=f"pre_accept:{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"pre_reject:{user_id}")
            ]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif action == "noop":
        return

    # --- FINAL ACCEPT LOGIC (Existing) ---
    if action == "accept":
        user_id = int(data[1])
        admin_user = query.from_user
        admin_name = f"@{admin_user.username}" if admin_user.username else admin_user.first_name
        original_text = query.message.text_html

        # 1. GENERATE INVITE LINK
        invite_link_text = ""
        if MAINTAINER_GROUP_ID:
            try:
                invite = await context.bot.create_chat_invite_link(
                    chat_id=MAINTAINER_GROUP_ID,
                    member_limit=1,
                    expire_date=datetime.now() + timedelta(hours=24),
                    name=f"Invite for {user_id}"
                )
                invite_link_text = (
                    f"\n\n🔗 <b>Maintainer Group Invite:</b>\n{invite.invite_link}\n"
                    "<i>(This link is valid for 24 hours and can only be used once)</i>"
                )
            except Exception as e:
                logger.error(f"Failed to generate invite link: {e}")
                invite_link_text = "\n\n⚠️ <i>(Could not generate invite link. Ensure Bot is Admin in the group.)</i>"
        else:
             invite_link_text = "\n\n⚠️ <i>(Group ID not configured in .env)</i>"

        # 2. COMMIT TO GITHUB
        github_status = ""
        # Access safely
        current_apps = context.bot_data.get('pending_apps', {})
        
        if user_id in current_apps:
            app_data = current_apps[user_id]
            maintainer_alias = app_data.get('maintainer_alias', 'Unknown')
            success, msg = add_maintainer_to_github(maintainer_alias)
            github_status = f"\n\n🖥️ <b>GitHub Action:</b>\n{msg}"
            
            # Remove and Trigger Save
            del current_apps[user_id]
            context.bot_data['pending_apps'] = current_apps
            
            # CLEAR USER DATA (The interview answers)
            if user_id in context.application.user_data:
                context.application.user_data[user_id].clear()
            
            # FORCE SAVE
            await context.application.persistence.flush()
        else:
            github_status = "\n\n⚠️ <b>GitHub Action:</b>\nCould not find user data in memory."

        # 3. NOTIFY ADMIN & USER
        new_status = f"\n\n✅ <b>ACCEPTED by {admin_name}</b>{github_status}" 
        user_notification = (
            "🎉 <b>Congratulations!</b>\n\n"
            "Your application for AfterlifeOS Maintainer has been <b>ACCEPTED</b>!\n"
            f"{invite_link_text}\n\n"
            "Welcome to the team! 🚀"
        )

        await query.edit_message_text(
            text=original_text + new_status,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        
        # Unpin message
        try:
            await context.bot.unpin_chat_message(chat_id=ADMIN_CHAT_ID, message_id=query.message.message_id)
        except Exception as e:
            logger.warning(f"Could not unpin message: {e}")
        
        try:
            await context.bot.send_message(chat_id=user_id, text=user_notification, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Could not notify user {user_id}: {e}")

# Helper to finalize rejection (Used by Callback and MessageHandler)
async def finalize_rejection(update, context, user_id, base_reason, custom_note, origin_msg_id=None, origin_text=None, cooldown_days=0):
    # Determine who is taking the action (from callback or message)
    if update.callback_query:
        admin_user = update.callback_query.from_user
        message = update.callback_query.message
    else:
        admin_user = update.message.from_user
        message = update.message
        
    admin_name = f"@{admin_user.username}" if admin_user.username else admin_user.first_name
    
    # Construct Reason Text
    full_reason = base_reason
    if custom_note:
        full_reason += f"\n\n📝 <b>Admin Note:</b>\n<i>{custom_note}</i>"

    # Notify User
    cooldown_msg = ""
    if cooldown_days > 0:
        resume_date = (datetime.now() + timedelta(days=cooldown_days)).strftime("%Y-%m-%d")
        cooldown_msg = f"\n\n⏳ <b>Cooldown Active:</b>\nYou may apply again after <b>{resume_date}</b>."

    user_notification = (
        "⚠️ <b>Application Update</b>\n\n"
        "We appreciate your interest in AfterlifeOS.\n"
        "Unfortunately, your maintainer application has been <b>declined</b>.\n\n"
        f"{full_reason}"
        f"{cooldown_msg}\n\n"
        "You are welcome to apply again in the future after addressing these points."
    )
    
    try:
        await context.bot.send_message(chat_id=user_id, text=user_notification, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Could not notify user {user_id}: {e}")

    # Update Admin Message (If possible, we need to find the original application message)
    # Since we might be in a reply thread, this is tricky. 
    # If called from Callback, we edit the message.
    if update.callback_query:
        original_text = update.callback_query.message.text_html
        new_status = f"\n\n❌ <b>REJECTED by {admin_name}</b>\nReason: {base_reason}"
        if custom_note:
            new_status += f"\nNote: {custom_note}"
        if cooldown_days > 0:
            new_status += f"\nCooldown: {cooldown_days} Days"
            
        await update.callback_query.edit_message_text(
            text=original_text + new_status,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    elif origin_msg_id and origin_text:
        # Reply flow with known origin
        new_status = f"\n\n❌ <b>REJECTED by {admin_name}</b>\nReason: {base_reason}"
        if custom_note:
            new_status += f"\nNote: {custom_note}"
        if cooldown_days > 0:
            new_status += f"\nCooldown: {cooldown_days} Days"
            
        try:
            await context.bot.edit_message_text(
                chat_id=ADMIN_CHAT_ID,
                message_id=origin_msg_id,
                text=origin_text + new_status,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            await update.message.reply_text(f"✅ Rejection sent and status updated.")
        except Exception as e:
            logger.error(f"Failed to edit admin message: {e}")
            await update.message.reply_text(f"✅ Rejection sent, but failed to update status message: {e}")
    else:
        # If called from Reply, we just confirm to admin.
        await update.message.reply_text(f"✅ Rejection sent to user {user_id}.")

    # Clean up memory with Persistence Trigger
    current_apps = context.bot_data.get('pending_apps', {})
    saved_name = "Unknown"
    
    if user_id in current_apps:
        saved_name = current_apps[user_id].get('name', 'Unknown')
        del current_apps[user_id]
        context.bot_data['pending_apps'] = current_apps

    # CLEAR USER DATA (The interview answers)
    if user_id in context.application.user_data:
        context.application.user_data[user_id].clear()

    # SET REJECTION COOLDOWN
    if cooldown_days > 0:
        expiry_date = datetime.now() + timedelta(days=cooldown_days)
        cooldowns = context.bot_data.get('rejected_cooldowns', {})
        
        # Store structured data (Migrate from old format if needed)
        cooldowns[user_id] = {
            'expiry': expiry_date,
            'name': saved_name
        }
        context.bot_data['rejected_cooldowns'] = cooldowns # Trigger Persistence Save

    # FORCE SAVE
    await context.application.persistence.flush()

async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return

    notes_text = (
        "<b>📋 Project Notes & Guidelines</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>1. Bring-up Standards</b>\n"
        "• Ensure your trees follow the standard AfterlifeOS file structure.\n"
        "• Remove any bloatware or unnecessary proprietary apps from vendor.\n\n"
        "<b>2. Commit History</b>\n"
        "• We value clean git history. Avoid massive squashed commits unless necessary.\n"
        "• Use proper commit messages (e.g., <code>component: Description</code>).\n\n"
        "<b>3. Communication</b>\n"
        "• Join the Maintainer Group immediately after acceptance.\n"
        "• Report any critical bugs affecting core functionality to the core team.\n\n"
        "<i>For more details, refer to the pinned messages in the group.</i>"
    )
    await update.message.reply_text(notes_text, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_chat.id)
    admin_chat_id = str(ADMIN_CHAT_ID)

    if user_chat_id == admin_chat_id:
        help_text = (
            "<b>🛡️ Admin Commands</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<b>Template Management:</b>\n"
            "• <code>/show_templates</code> - List all rejection templates\n"
            "• <code>/add_template &lt;key&gt; &lt;text&gt;</code> - Add new template\n"
            "• <code>/edit_template &lt;key&gt; &lt;text&gt;</code> - Edit existing template\n"
            "• <code>/remove_template &lt;key&gt;</code> - Remove a template\n\n"
            "<b>Cooldown Management:</b>\n"
            "• <code>/check_cooldowns</code> - View active bans\n"
            "• <code>/remove_cooldown &lt;id&gt;</code> - Unban a user\n\n"
            "<i>You can also reply to a message with /add_template &lt;key&gt; to save it.</i>"
        )
    else:
        help_text = (
            "<b>🤖 Maintainer Bot Help</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "• <code>/start</code> - Apply for Maintainer position\n"
            "• <code>/cancel</code> - Cancel current application\n"
            "• <code>/notes</code> - Read project guidelines\n"
        )
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if this reply is for a pending rejection note
    admin_id = update.message.from_user.id
    reply_key = f"admin_reply_{admin_id}"
    
    if reply_key in context.bot_data:
        data = context.bot_data[reply_key]
        target_uid = data['target_uid']
        base_reason = data['base_reason']
        cooldown_days = data.get('cooldown_days', 0)
        msg_id_to_unpin = data.get('msg_id') # Get stored ID
        original_text = data.get('original_text')
        custom_note = update.message.text
        
        # Execute rejection
        await finalize_rejection(update, context, target_uid, base_reason, custom_note, msg_id_to_unpin, original_text, cooldown_days)
        
        # Unpin if ID exists
        if msg_id_to_unpin:
            try:
                await context.bot.unpin_chat_message(chat_id=ADMIN_CHAT_ID, message_id=msg_id_to_unpin)
            except Exception as e:
                logger.warning(f"Could not unpin message via reply: {e}")

        # Clean up
        del context.bot_data[reply_key]
    else:
        # Not a rejection note reply, ignore or handle elsewhere
        pass

def main():
    if not REDIS_URL:
        logger.error("❌ REDIS_URL not found in env. Cannot start.")
        sys.exit(1)

    # Persistence setup
    my_persistence = RedisPersistence(url=REDIS_URL)

    # --- ONE-TIME MIGRATION LOGIC ---
    # Check if Redis has data. If not, try to import from GitHub one last time.
    try:
        r = redis.from_url(REDIS_URL)
        if not r.exists("bot_data") and not r.exists("user_data"):
            logger.info("ℹ️ Redis empty. Attempting migration from GitHub pickle...")
            
            local_pickle = os.path.join(base_dir, 'bot_data.pickle')
            if download_file_from_github('bot_data.pickle'):
                with open(local_pickle, 'rb') as f:
                    old_blob = pickle.load(f)
                
                # PTB PicklePersistence usually stores data in a dict: 
                # {'bot_data': {}, 'user_data': {}, 'chat_data': {}, 'conversations': {}}
                
                # 1. Migrate BOT_DATA
                if 'bot_data' in old_blob:
                    r.set("bot_data", pickle.dumps(old_blob['bot_data']))
                    logger.info(f"✅ Migrated bot_data")
                
                # 2. Migrate USER_DATA (Convert to Hash Map)
                if 'user_data' in old_blob:
                    u_data = old_blob['user_data']
                    for uid, data in u_data.items():
                        r.hset("user_data", str(uid), pickle.dumps(data))
                    logger.info(f"✅ Migrated user_data ({len(u_data)} users)")

                # 3. Migrate CHAT_DATA
                if 'chat_data' in old_blob:
                    c_data = old_blob['chat_data']
                    for cid, data in c_data.items():
                        r.hset("chat_data", str(cid), pickle.dumps(data))
                    logger.info(f"✅ Migrated chat_data")
                
                # 4. Migrate CONVERSATIONS (If any)
                if 'conversations' in old_blob:
                    conv_data = old_blob['conversations']
                    for name, state in conv_data.items():
                        r.set(f"conv_{name}", pickle.dumps(state))
                    logger.info(f"✅ Migrated conversations")

                logger.info("🎉 Full Migration to Redis Complete!")
                # Cleanup local file
                os.remove(local_pickle)
    except Exception as e:
        logger.error(f"⚠️ Migration warning: {e}")

    app = Application.builder().token(API_TOKEN).persistence(my_persistence).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            RULES_AGREEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, rules_logic)],
            SOURCE_TYPE_CHECK: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_source_type)],
            PRIVATE_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_private_reason)],
            PRIVATE_ACCESS_AGREEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_private_agreement)],
            FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            MAINTAINER_ALIAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_maintainer_alias)],
            GITHUB_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_github)],
            DEVICE_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_device_info)],
            DEVICE_TREE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_dt)],
            DEVICE_COMMON: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_dt_common)],
            VENDOR_TREE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_vt)],
            VENDOR_COMMON: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_vt_common)],
            KERNEL_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_kernel)],
            SUPPORT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_support)],
            OFFICIAL_ROMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_official_roms)],
            DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_duration)],
            CONTRIBUTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_contribution)],
            WHY_JOIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why_join)],
            SUITABILITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_suitability)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CallbackQueryHandler(handle_admin_decision))
    
    # Template Management Commands
    app.add_handler(CommandHandler("show_templates", show_templates))
    app.add_handler(CommandHandler("add_template", add_template))
    app.add_handler(CommandHandler("edit_template", edit_template))
    app.add_handler(CommandHandler("remove_template", remove_template))
    
    # Cooldown Management Commands
    app.add_handler(CommandHandler("check_cooldowns", check_cooldowns))
    app.add_handler(CommandHandler("remove_cooldown", remove_cooldown))

    # Handler for Admin Replies
    app.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND, handle_admin_reply))

    # Handler for New Chat Members (Welcome Message)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    
    print(f"🤖 Bot GitHub Integrated & No Previews) is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
