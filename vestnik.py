#!/usr/bin/env python3
import json
import logging
import random
import datetime
import configparser
import warnings

from telegram import (
	ChatMember,
	InlineKeyboardButton,
	InlineKeyboardMarkup,
	Update
)
from telegram.ext import (
	filters,
	Application,
	CallbackQueryHandler,
	ChatJoinRequestHandler,
	ChatMemberHandler,
	CommandHandler,
	Defaults,
	MessageHandler
)
from telegram.error import Conflict, Forbidden, NetworkError
from telegram.constants import ChatMemberStatus

logger = logging.getLogger(__name__)
daily_list = []


def save_daily_list():
	with open(PATHS['sub_list'], 'w') as f:
		json.dump(daily_list, f)


def add_user(user_id, user_name):
	daily_list.append(user_id)
	save_daily_list()
	logger.info("Adding user %s (%s) to messaging list", user_name, user_id)


def remove_user(user_id, user_name):
	daily_list.remove(user_id)
	save_daily_list()
	logger.info("Removing user %s (%s) from messaging list", user_name, user_id)


def remove_blocked_user(chat):
	logger.info("%s (%s) blocked the bot", chat.full_name, chat.id)
	if chat.id in daily_list:
		remove_user(chat.id, chat.full_name)


def extract_status_change(chat_member_update):
	status_change = chat_member_update.difference().get('status')

	if status_change is None:
		return None

	old_status, new_status = status_change
	was_member = old_status in [
		ChatMember.MEMBER,
		ChatMember.OWNER,
		ChatMember.ADMINISTRATOR
	]
	is_member = new_status in [
		ChatMember.MEMBER,
		ChatMember.OWNER,
		ChatMember.ADMINISTRATOR
	]

	return was_member, is_member


async def is_channel_member(update, context):
	user_id = update.effective_user.id
	member = await context.bot.get_chat_member(KEYS['channel'], user_id)
	return member.status in [
		ChatMember.MEMBER,
		ChatMember.OWNER,
		ChatMember.ADMINISTRATOR
	]


async def stranger_reply(update):
	# markup = InlineKeyboardMarkup([[
	# 	InlineKeyboardButton("Подписаться", f"https://t.me/{KEYS['invite']}")
	# ]])

	with open(PATHS['stranger']) as f:
		message = f.read().strip()

	await update.effective_user.send_message(message)


async def subscribe_daily(update):
	user = update.effective_user

	if user.id in daily_list:
		response = "Вы уже подписаны!"
	else:
		logger.info("%s (%s) subscribed to bot", user.full_name, user.id)
		add_user(user.id, user.full_name)
		response = "Подписка оформлена!"

	await update.effective_message.reply_text(response)


async def send_daily_card(context, user_id):
	with open(PATHS['data']) as f:
		data = json.load(f)

	card_id = random.randrange(78)
	decks = list(data['decks'].items())
	deck_id, deck_name = random.choice(decks)
	card_path = f"{PATHS['cards']}/{deck_id}/{card_id}.jpg"

	if card_id > 21:
		rank = (card_id - 22) % 14
		suit = (card_id - 22) // 14

		ranks = data['ranks']
		if deck_id in data['altRanks']:
			ranks[10:] = data['altRanks'][deck_id]
		suits = (data['altSuits'][deck_id]
			if deck_id in data['altSuits']
			else data['suits'])

		name = f"{ranks[rank]} {suits[suit]}"
	else:
		name = f"{data['roman'][card_id]} {data['major'][card_id]}"

	meanings = data['meanings']
	meanings = (meanings[deck_id]
		if deck_id in meanings
		else meanings['normal'])

	with open(PATHS['card_caption']) as f:
		caption = f.read().strip().format(name, deck_name, meanings[card_id])

	try:
		await context.bot.send_photo(user_id, card_path, caption)
	except Forbidden:
		remove_blocked_user(await context.bot.get_chat(user_id))


async def start(update, context):
	if not await is_channel_member(update, context):
		await stranger_reply(update)
		return

	if update.chat_member:
		user = update.chat_member.new_chat_member.user
	else:
		user = update.effective_user

	markup = (InlineKeyboardMarkup([[
		InlineKeyboardButton("Подписаться", callback_data='sub_daily')
	]]) if user.id not in daily_list else None)

	animation_path = f"{PATHS['images']}/promo.mp4"

	with open(PATHS['welcome']) as f:
		welcome_message = f.read().strip()
	with open(PATHS['info']) as f:
		info_message = f.read().strip()

	await user.send_animation(
		animation_path,
		caption=welcome_message,
		reply_markup=markup)
	await user.send_message(info_message)


async def button_handler(update, context):
	query = update.callback_query
	await query.answer()

	if query.data == 'sub_daily':
		if await is_channel_member(update, context):
			await subscribe_daily(update)
		else:
			await stranger_reply(update)


async def track_channel_members(update, context):
	chat_member = update.chat_member
	result = extract_status_change(chat_member)
	if result is None:
		return

	was_member, is_member = result
	user = chat_member.new_chat_member.user

	if not was_member and is_member:
		logger.info("%s (%s) joined the channel", user.full_name, user.id)
		add_user(user.id, user.full_name)
		await start(update, context)
	elif was_member and not is_member:
		logger.info("%s (%s) left the channel", user.full_name, user.id)
		if user.id in daily_list:
			remove_user(user.id, user.full_name)

		with open(PATHS['left_channel']) as f:
			message = f.read().strip()
		try:
			await context.bot.send_message(user.id, message)
		except Forbidden:
			pass


async def blocked_handler(update, _):
	bot = update.my_chat_member.new_chat_member
	if bot.status == ChatMemberStatus.BANNED:
		remove_blocked_user(update.effective_chat)


async def request_greet(update, context):
	user = update.effective_user
	logger.info("%s (%s) sent a join request", user.full_name, user.id)
	await stranger_reply(update)


async def unknown_command_handler(update, _):
	await update.message.reply_text("Такой команды ещё не придумали!")


async def send_daily_message(context):
	for user_id in daily_list:
		await send_daily_card(context, user_id)


async def send_test_card(update, context):
	await send_daily_card(context, update.effective_user.id)


async def list_subscriber_names(update, context):
	message = ""
	for user_id in daily_list:
		user = await context.bot.get_chat(user_id)
		message += f"{user_id} — <b>{user.full_name}</b>"
		if user.username:
			message += f" (@{user.username})"
		message += "\n"
	await update.message.reply_text(message)


async def error_callback(_, context):
	if isinstance(context.error, Conflict):
		logger.error(context.error)
	elif not isinstance(context.error, NetworkError):
		raise context.error


def main():
	global KEYS, PATHS, daily_list

	logging.basicConfig(
		format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
		level=logging.INFO)

	config = configparser.ConfigParser()
	config_file = 'vestnik.conf'

	if not config.read(config_file):
		logger.error("Config file %s doesn't exist, terminating", config_file)
		return

	KEYS = config['keys']
	PATHS = config['paths']
	job_time = {k: int(v) for k, v in config.items('time')}

	try:
		with open(PATHS['sub_list']) as f:
			daily_list = json.load(f)
	except ValueError:
		logger.error("Failed to parse JSON file %s, terminating", PATHS['sub_list'])
		return
	except OSError:
		logger.warning("File %s doesn't exist, creating", PATHS['sub_list'])
		open(PATHS['sub_list'], 'a').close()

	defaults = Defaults(parse_mode='HTML')
	application = (Application
			.builder()
			.token(KEYS['token'])
			.defaults(defaults)
			.build())

	admin_id = int(KEYS['admin'])
	admin_filter = filters.User(admin_id)

	# ignore the dumb warning about the `days` parameter for jobs
	warnings.filterwarnings('ignore', "Prior to v20.0 the `days` parameter")

	application.add_handler(CommandHandler(['start', 'help'], start))
	application.add_handler(CommandHandler(
		'sendtestcard',
		send_test_card,
		admin_filter))
	application.add_handler(CommandHandler(
		'listsubnames',
		list_subscriber_names,
		admin_filter))

	application.add_handler(CallbackQueryHandler(button_handler))

	application.add_handler(ChatMemberHandler(
		track_channel_members,
		ChatMemberHandler.CHAT_MEMBER))
	application.add_handler(ChatMemberHandler(
		blocked_handler,
		ChatMemberHandler.MY_CHAT_MEMBER))

	application.add_handler(ChatJoinRequestHandler(request_greet))

	application.add_handler(MessageHandler(
		filters.ChatType.PRIVATE & filters.COMMAND,
		unknown_command_handler))
	application.add_handler(MessageHandler(
		filters.ChatType.PRIVATE & filters.TEXT,
		start))

	application.add_error_handler(error_callback)

	application.job_queue.run_daily(
		send_daily_message,
		datetime.time(**job_time))

	application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
	main()
