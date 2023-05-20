#!/usr/bin/env python3
import configparser
import datetime
import json
import logging
import random

from telegram import (
	ChatMember,
	InlineKeyboardButton,
	InlineKeyboardMarkup,
	Update
)
from telegram.ext import (
	Application,
	CallbackQueryHandler,
	ChatJoinRequestHandler,
	ChatMemberHandler,
	CommandHandler,
	Defaults,
	filters,
	MessageHandler
)
from telegram.error import (
	Conflict,
	Forbidden,
	NetworkError,
	TimedOut
)

MEMBER_STATUSES = [
	ChatMember.MEMBER,
	ChatMember.OWNER,
	ChatMember.ADMINISTRATOR
]
daily_list = []

logging.basicConfig(
	format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
	level=logging.INFO
)
logger = logging.getLogger(__name__)

config = configparser.ConfigParser()
config.read('vestnik.conf')


def load_daily_list():
	global daily_list
	with open(config.get('paths', 'sub_list')) as f:
		daily_list = json.load(f)


def save_daily_list():
	with open(config.get('paths', 'sub_list'), 'w') as f:
		json.dump(daily_list, f)


def add_user(user):
	daily_list.append(user.id)
	save_daily_list()
	logger.info("Adding user %s (%s) to messaging list", user.full_name, user.id)


def remove_user(user):
	if user.id not in daily_list:
		return

	daily_list.remove(user.id)
	save_daily_list()
	logger.info("Removing user %s (%s) from messaging list", user.full_name, user.id)


def remove_blocked_user(user):
	logger.info("%s (%s) blocked the bot", user.full_name, user.id)
	remove_user(user)


def extract_status_change(chat_member_update):
	status_change = chat_member_update.difference().get('status')

	if status_change is None:
		return None

	old_status, new_status = status_change
	was_member = old_status in MEMBER_STATUSES
	is_member = new_status in MEMBER_STATUSES
	return was_member, is_member


def retry_on_network_error(func):
	async def _wrapper(*args, **kwargs):
		retry, max_retries = 0, 5
		while True:
			try:
				return await func(*args, **kwargs)
			except NetworkError:
				if retry == max_retries:
					logger.error("Max retries exceeded")
					return
				retry += 1
				logger.error("Network error, retrying... (%s/%s)", retry, max_retries)
	return _wrapper


async def is_channel_member(update, context):
	user_id = update.effective_user.id
	member = await context.bot.get_chat_member(config.get('keys', 'channel'), user_id)
	return member.status in MEMBER_STATUSES


async def stranger_reply(update):
	with open(config.get('paths', 'stranger')) as f:
		message = f.read()

	await update.effective_user.send_message(message)


async def subscribe_daily(update):
	user = update.effective_user

	if user.id in daily_list:
		response = "Вы уже подписаны!"
	else:
		logger.info("%s (%s) subscribed to bot", user.full_name, user.id)
		add_user(user)
		response = "Подписка оформлена!"

	await update.effective_message.reply_text(response)


async def send_daily_card(context, user_id):
	with open(config.get('paths', 'data')) as f:
		data = json.load(f)

	with open(config.get('paths', 'card_caption')) as f:
		caption = f.read()

	card_id = random.randrange(78)
	decks = data['decks']
	deck_id = random.choice(list(decks))
	meanings = data['meanings'].get(deck_id) or data['meanings']['normal']

	if card_id > 21:
		rank = (card_id - 22) % 14
		suit = (card_id - 22) // 14

		rank_names = data['ranks']
		if deck_id in data['altRanks']:
			rank_names[10:] = data['altRanks'][deck_id]
		suit_names = data['altSuits'].get(deck_id) or data['suits']

		card_name = f"{rank_names[rank]} {suit_names[suit]}"
	else:
		card_name = f"{data['roman'][card_id]} {data['major'][card_id]}"

	@retry_on_network_error
	async def send_message():
		await context.bot.send_photo(
			user_id,
			f"{config.get('paths', 'cards')}/{deck_id}/{card_id}.jpg",
			caption.format(card_name, decks[deck_id], meanings[card_id])
		)

	try:
		await send_message()
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

	markup = InlineKeyboardMarkup([[
		InlineKeyboardButton("Подписаться", callback_data='sub_daily')
	]]) if user.id not in daily_list else None

	with open(config.get('paths', 'welcome')) as f:
		welcome_message = f.read()

	await user.send_photo(
		config.get('paths', 'welcome_image'),
		welcome_message,
		reply_markup=markup
	)


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
		add_user(user)
		await start(update, context)
	elif was_member and not is_member:
		logger.info("%s (%s) left the channel", user.full_name, user.id)
		remove_user(user)

		with open(config.get('paths', 'left_channel')) as f:
			message = f.read()

		try:
			await context.bot.send_message(user.id, message)
		except Forbidden:
			pass


async def block_unblock_handler(update, _):
	new_status = update.my_chat_member.new_chat_member.status
	user = update.effective_user
	if new_status == ChatMember.BANNED:
		remove_blocked_user(user)
	else:
		logger.info("%s (%s) unblocked the bot", user.full_name, user.id)


async def request_greet(update, _):
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
	message = "<b>Список подписчиков:</b>\n"
	for user_id in daily_list:
		user = await context.bot.get_chat(user_id)
		message += f"{user_id} — <b>{user.full_name}</b>"
		if user.username:
			message += f" (@{user.username})"
		message += "\n"
	await update.message.reply_text(message)


async def error_callback(_, context):
	if (not isinstance(context.error, (Conflict, NetworkError))
	or	isinstance(context.error, TimedOut)):
		logger.error(context.error, exc_info=True)


def main():
	try:
		load_daily_list()
	except FileNotFoundError as e:
		logger.warning(
			"File '%s' doesn't exist, continuing with empty subscriber list",
			e.filename
		)

	admin_id = config.getint('keys', 'admin')
	admin_filter = filters.User(admin_id)
	job_time = {k: int(v) for k, v in config.items('time')}

	application = (
		Application.builder()
		.token(config.get('keys', 'token'))
		.defaults(Defaults(parse_mode='HTML'))
		.connect_timeout(30)
		.read_timeout(30)
		.write_timeout(30)
		.build()
	)

	application.add_handler(CommandHandler(['start', 'help'], start))
	application.add_handler(CommandHandler(
		'sendtestcard',
		send_test_card,
		admin_filter
	))
	application.add_handler(CommandHandler(
		'listsubnames',
		list_subscriber_names,
		admin_filter
	))

	application.add_handler(CallbackQueryHandler(button_handler))

	application.add_handler(ChatMemberHandler(
		track_channel_members,
		ChatMemberHandler.CHAT_MEMBER
	))
	application.add_handler(ChatMemberHandler(
		block_unblock_handler,
		ChatMemberHandler.MY_CHAT_MEMBER
	))

	application.add_handler(ChatJoinRequestHandler(request_greet))

	application.add_handler(MessageHandler(
		filters.ChatType.PRIVATE & filters.COMMAND,
		unknown_command_handler
	))
	application.add_handler(MessageHandler(
		filters.ChatType.PRIVATE & filters.TEXT,
		start
	))

	application.add_error_handler(error_callback)

	application.job_queue.run_daily(
		send_daily_message,
		datetime.time(**job_time),
		job_kwargs={'misfire_grace_time': None}
	)

	application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
	main()
