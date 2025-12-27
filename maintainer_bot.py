import sys
import os
import logging
import re
import base64
import json
import requests # Need requests library
from datetime import datetime, timedelta

# --- CONFIGURATION & SETUP ---

sys.path.append(os.path.expanduser("~/pylib"))

try:
    from dotenv import load_dotenv
except ImportError:
    print("❌ Error: 'python-dotenv' library is not found.")
    sys.exit(1)

base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(base_dir, 'private.env'))

API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_ID')
MAINTAINER_GROUP_ID = os.getenv('MAINTAINER_GROUP_ID')

# GITHUB CONFIG
GH_TOKEN = os.getenv('GITHUB_TOKEN')
GH_REPO = os.getenv('GITHUB_REPO')       # e.g., AfterlifeOS/vendor_signed
GH_PATH = os.getenv('GITHUB_FILE_PATH')  # e.g., signed.mk
GH_BRANCH = os.getenv('GITHUB_BRANCH')   # e.g., 16

# WELCOME LINKS
LINK_DEVICE_LIST = os.getenv('LINK_DEVICE_LIST', 'https://google.com')
LINK_BRINGUP_GUIDE = os.getenv('LINK_BRINGUP_GUIDE', 'https://google.com')

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
        PicklePersistence
    )
except ImportError as e:
    print(f"❌ Error importing telegram library: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)

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
    user = update.message.from_user
    logger.info(f"User {user.first_name} started conversation.")
    
    # Initialize bot_data storage for pending apps if not exists
    if 'pending_apps' not in context.bot_data:
        context.bot_data['pending_apps'] = {}

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
        "Provide your <b>GitHub Profile URL</b>:\n\n"
        "💡 <i>Example: https://github.com/johndoe</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    return GITHUB_URL

async def get_github(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_valid_url(update.message.text):
        await update.message.reply_text("⚠️ Invalid URL. Please provide a valid GitHub link:", disable_web_page_preview=True)
        return GITHUB_URL
    context.user_data['github'] = update.message.text
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
    if 'pending_apps' not in context.bot_data:
        context.bot_data['pending_apps'] = {}
    
    context.bot_data['pending_apps'][user.id] = {
        'maintainer_alias': data['maintainer_alias'],
        'name': data['name']
    }

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

    admin_msg = (
        "<b>🚀 NEW MAINTAINER APPLICATION</b>\n"
        f"<i>Received: {date_str}</i>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>👤 APPLICANT DETAILS</b>\n"
        f"├ <b>Name:</b> {data['name']}\n"
        f"├ <b>Maintainer Alias:</b> {data['maintainer_alias']}\n"
        f"├ <b>User:</b> {username}\n"
        f"├ <b>ID:</b> {user.id}\n"
        f"└ <b>GitHub:</b> {format_link(data['github'], data['github'])}\n\n"
        
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
    except Exception as e:
        logger.error(f"Failed to send: {e}")
        await update.message.reply_text("❌ Error sending application.")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 <b>Operation Cancelled.</b>", parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove(), disable_web_page_preview=True)
    return ConversationHandler.END

async def get_suitability(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await finalize(update, context)

# --- TEMPLATE MANAGEMENT (JSON) ---
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
    if not os.path.exists(TEMPLATES_FILE):
        save_templates(DEFAULT_TEMPLATES)
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

# --- ADMIN DECISION HANDLER (DYNAMIC UI) ---
async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    
    data = query.data.split(":")
    action = data[0]
    user_id = int(data[1])
    
    # --- LOGIC HANDLING ---
    
    # 1. INITIAL REJECT CLICK -> SHOW DYNAMIC TEMPLATES
    if action == "pre_reject":
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

    # 2. TEMPLATE SELECTED -> ASK FOR OPTIONAL NOTE
    elif action == "sel_reason":
        reason_key = data[1]
        target_uid = int(data[2])
        
        context.user_data['temp_reject_reason'] = reason_key
        context.user_data['temp_reject_uid'] = target_uid

        # Fetch message from dynamic dict, fallback to Generic if key missing
        reason_text = rejection_templates.get(reason_key, rejection_templates.get('other', "Application Declined."))

        keyboard = [
            [InlineKeyboardButton("✅ Send Now", callback_data=f"do_reject:send:{target_uid}")],
            [InlineKeyboardButton("📝 Add Optional Note", callback_data=f"do_reject:note:{target_uid}")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"pre_reject:{target_uid}")]
        ]
        
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 3. EXECUTE REJECTION OR ASK FOR NOTE
    elif action == "do_reject":
        sub_action = data[1]
        target_uid = int(data[2])
        
        reason_key = context.user_data.get('temp_reject_reason', 'other')
        base_reason = rejection_templates.get(reason_key, "Application Declined.")

        if sub_action == "send":
            await finalize_rejection(update, context, target_uid, base_reason, None)
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
                'msg_id': query.message.message_id # Save MSG ID for unpinning later via reply
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
        if 'pending_apps' in context.bot_data and user_id in context.bot_data['pending_apps']:
            app_data = context.bot_data['pending_apps'][user_id]
            maintainer_alias = app_data.get('maintainer_alias', 'Unknown')
            success, msg = add_maintainer_to_github(maintainer_alias)
            github_status = f"\n\n🖥️ <b>GitHub Action:</b>\n{msg}"
            del context.bot_data['pending_apps'][user_id]
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
async def finalize_rejection(update, context, user_id, base_reason, custom_note):
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
    user_notification = (
        "⚠️ <b>Application Update</b>\n\n"
        "We appreciate your interest in AfterlifeOS.\n"
        "Unfortunately, your maintainer application has been <b>declined</b>.\n\n"
        f"{full_reason}\n\n"
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
            
        await update.callback_query.edit_message_text(
            text=original_text + new_status,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    else:
        # If called from Reply, we just confirm to admin.
        await update.message.reply_text(f"✅ Rejection sent to user {user_id}.")

    # Clean up memory
    if 'pending_apps' in context.bot_data and user_id in context.bot_data['pending_apps']:
        del context.bot_data['pending_apps'][user_id]

async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        msg_id_to_unpin = data.get('msg_id') # Get stored ID
        custom_note = update.message.text
        
        # Execute rejection
        await finalize_rejection(update, context, target_uid, base_reason, custom_note)
        
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
    # Persistence setup: Stores data to 'bot_data.pickle' in the same directory as the script
    data_path = os.path.join(base_dir, 'bot_data.pickle')
    my_persistence = PicklePersistence(filepath=data_path)

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

    # Handler for Admin Replies
    app.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND, handle_admin_reply))

    # Handler for New Chat Members (Welcome Message)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    
    print(f"🤖 Bot GitHub Integrated & No Previews) is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
