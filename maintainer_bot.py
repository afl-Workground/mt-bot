import sys
import os
import logging
import re
import base64
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

if not API_TOKEN or not ADMIN_CHAT_ID:
    print("❌ Error: Configuration missing in .env!")
    sys.exit(1)

try:
    from telegram import (
        Update, 
        ReplyKeyboardMarkup, 
        ReplyKeyboardRemove, 
        InlineKeyboardMarkup, 
        InlineKeyboardButton
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
(RULES_AGREEMENT, FULL_NAME, MAINTAINER_ALIAS, GITHUB_URL, DEVICE_INFO, 
 DEVICE_TREE, DEVICE_COMMON, VENDOR_TREE, VENDOR_COMMON, 
 KERNEL_SOURCE, SUPPORT_LINK, OFFICIAL_ROMS, DURATION, 
 CONTRIBUTION, WHY_JOIN, SUITABILITY) = range(16)

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
        await update.message.reply_text(
            "<b>Step 1/11: Identity</b>\n"
            "Please enter your <b>Real Name</b>:\n\n"
            "💡 <i>Example: John Doe</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
            disable_web_page_preview=True
        )
        return FULL_NAME
    
    await update.message.reply_text("❌ <b>Application Declined.</b>", reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
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
        
        "<b>📂 SOURCE CODE</b>\n"
        f"├ <b>Device Tree:</b> {format_link(data['dt'])}\n"
        f"├ <b>DT Common:</b> {format_link(data['dt_c'])}\n"
        f"├ <b>Vendor Tree:</b> {format_link(data['vt'])}\n"
        f"├ <b>VT Common:</b> {format_link(data['vt_c'])}\n"
        f"└ <b>Kernel:</b> {format_link(data['kernel'])}\n\n"
        
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
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, 
            text=admin_msg, 
            parse_mode=ParseMode.HTML, 
            disable_web_page_preview=True,
            reply_markup=reply_markup
        )
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

# UPDATED: Admin Decision Handler with Invite Link & GitHub Commit
async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    
    data = query.data.split(":")
    action = data[0]
    user_id = int(data[1])
    
    # --- CONFIRMATION LOGIC ---
    if action == "pre_accept":
        keyboard = [
            [InlineKeyboardButton("⚠️ Confirm Accept?", callback_data=f"noop:{user_id}")],
            [
                InlineKeyboardButton("✅ Yes", callback_data=f"accept:{user_id}"),
                InlineKeyboardButton("🔙 No", callback_data=f"reset:{user_id}")
            ]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return
    elif action == "pre_reject":
        keyboard = [
            [InlineKeyboardButton("⚠️ Confirm Reject?", callback_data=f"noop:{user_id}")],
            [
                InlineKeyboardButton("❌ Yes", callback_data=f"reject:{user_id}"),
                InlineKeyboardButton("🔙 No", callback_data=f"reset:{user_id}")
            ]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return
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
    # --------------------------

    admin_user = query.from_user
    admin_name = f"@{admin_user.username}" if admin_user.username else admin_user.first_name

    original_text = query.message.text_html
    
    if action == "accept":
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
        # Retrieve stored user data
        if 'pending_apps' in context.bot_data and user_id in context.bot_data['pending_apps']:
            app_data = context.bot_data['pending_apps'][user_id]
            maintainer_alias = app_data.get('maintainer_alias', 'Unknown')
            
            # Execute Commit
            success, msg = add_maintainer_to_github(maintainer_alias)
            github_status = f"\n\n🖥️ <b>GitHub Action:</b>\n{msg}"
            
            # Clean up memory
            del context.bot_data['pending_apps'][user_id]
        else:
            github_status = "\n\n⚠️ <b>GitHub Action:</b>\nCould not find user data in memory (Bot might have restarted). Please add maintainer manually."

        # 3. NOTIFY ADMIN & USER
        new_status = f"\n\n✅ <b>ACCEPTED by {admin_name}</b>{github_status}" 
        
        user_notification = (
            "🎉 <b>Congratulations!</b>\n\n"
            "Your application for AfterlifeOS Maintainer has been <b>ACCEPTED</b>!\n"
            f"{invite_link_text}\n\n"
            "Welcome to the team! 🚀"
        )
        
    else:
        new_status = f"\n\n❌ <b>REJECTED by {admin_name}</b>"
        user_notification = (
            "⚠️ <b>Update on your Application</b>\n\n"
            "We appreciate your interest in AfterlifeOS.\n"
            "Unfortunately, your maintainer application has been <b>declined</b> at this time.\n"
            "You may improve your skills and apply again in the future."
        )
        # Clean up memory if rejected
        if 'pending_apps' in context.bot_data and user_id in context.bot_data['pending_apps']:
            del context.bot_data['pending_apps'][user_id]

    await query.edit_message_text(
        text=original_text + new_status,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    
    try:
        await context.bot.send_message(chat_id=user_id, text=user_notification, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Could not notify user {user_id}: {e}")
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"⚠️ Failed to DM user {user_id}. Link was not sent.", disable_web_page_preview=True)

def main():
    # Persistence setup: Stores data to 'bot_data.pickle' in the same directory as the script
    data_path = os.path.join(base_dir, 'bot_data.pickle')
    my_persistence = PicklePersistence(filepath=data_path)

    app = Application.builder().token(API_TOKEN).persistence(my_persistence).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            RULES_AGREEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, rules_logic)],
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
    app.add_handler(CallbackQueryHandler(handle_admin_decision))
    
    print(f"🤖 Bot GitHub Integrated & No Previews) is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
