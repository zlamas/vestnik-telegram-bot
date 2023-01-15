#!/usr/bin/env python3
import json
import logging
import random
import datetime
import configparser
import warnings

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember, LabeledPrice
from telegram.ext import filters, Application, Defaults, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, ChatJoinRequestHandler, PreCheckoutQueryHandler
from telegram.error import Conflict, NetworkError
from telegram.constants import ChatMemberStatus


async def is_member(update, context):
	user_id = update.effective_user.id
	chat_member = await context.bot.get_chat_member(keys['channel'], user_id)
	return chat_member.status in [
		ChatMember.MEMBER,
		ChatMember.OWNER,
		ChatMember.ADMINISTRATOR
	]


async def start(update, context):
	if await is_member(update, context):
		if update.effective_user.id in daily_ids:
			markup = None
		else:
			markup = start_inline_keyboard()

		animation_path = f"{paths['images']}/promo.mp4"

		with open(paths['welcome']) as f1, open(paths['info']) as f2:
			await update.effective_user.send_animation(animation_path, caption=f1.read().strip(), reply_markup=markup)
			await update.effective_user.send_message(f2.read().strip())
	else:
		await stranger_reply(update)


async def stranger_reply(update):
	with open(paths['stranger']) as f:
		stranger_message = f.read().strip()

	markup = InlineKeyboardMarkup([[
		InlineKeyboardButton("Подписаться", f"https://t.me/{keys['invite']}")
	]])
	await update.effective_message.reply_text(stranger_message, reply_markup=markup)


def start_inline_keyboard():
	return InlineKeyboardMarkup([[
		InlineKeyboardButton("Подписаться", callback_data='sub_daily')
	]])


async def button_handler(update, context):
	query = update.callback_query
	await query.answer()

	if query.data == 'sub_daily':
		if await is_member(update, context):
			markup = start_inline_keyboard()
			# await query.edit_message_reply_markup(reply_markup=markup)
			await subscribe_daily(update)
		else:
			await stranger_reply(update)


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


async def track_channel_members(update, context):
	chat_member = update.chat_member
	result = extract_status_change(chat_member)
	if result is None:
		return

	was_member, is_member = result
	user = chat_member.new_chat_member.user

	if not was_member and is_member:
		logger.info(f"{user.full_name} ({user.id}) joined the channel")
	elif was_member and not is_member:
		logger.info(f"{user.full_name} ({user.id}) left the channel")
		if user.id in daily_ids:
			with open(paths['left_channel']) as f:
				left_channel_message = f.read().strip()

			await context.bot.send_message(user.id, left_channel_message)
			remove_user(user.id, user.full_name)


async def blocked_handler(update, context):
	bot = update.my_chat_member.new_chat_member
	if bot.status == ChatMemberStatus.BANNED:
		user = update.effective_user
		logger.info(f"{user.full_name} ({user.id}) blocked the bot")
		if user.id in daily_ids:
			remove_user(user.id, user.full_name)


async def request_greet(update, context):
	join_request = update.chat_join_request
	user = join_request.from_user

	logger.info(f"{user.full_name} ({user.id}) sent a join request")
	await join_request.approve()
	daily_ids.append(user.id)
	save_daily_list()

	animation_path = f"{paths['images']}/promo.mp4"

	with open(paths['welcome']) as f1, open(paths['info']) as f2:
		await user.send_animation(animation_path, caption=f1.read().strip())
		await user.send_message(f2.read().strip())


async def subscribe_daily(update):
	user = update.effective_user

	if user.id in daily_ids:
		response = "Вы уже подписаны!"
	else:
		logger.info(f"{user.full_name} ({user.id}) subscribed to bot")
		response = "Подписка оформлена!"
		daily_ids.append(user.id)
		save_daily_list()

	await update.effective_message.reply_text(response)


def remove_user(user_id, user_name):
	daily_ids.remove(user_id)
	save_daily_list()
	logger.info(f"Removing user {user_name} ({user_id}) from messaging list")


def save_daily_list():
	with open(paths['sub_list'], 'w') as f:
		json.dump(daily_ids, f)


async def send_daily_message(context):
	for chat_id in daily_ids:
		await send_daily_card(context, chat_id)


async def send_daily_card(context, chat_id):
	with open(paths['data']) as f:
		data = json.load(f)

	card_id = random.randrange(78)
	decks = list(data['decks'].items())
	deck_id, deck_name = random.choice(decks)
	card_path = f"{paths['cards']}/{deck_id}/{card_id}.jpg"

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

	with open(paths['card_caption']) as f:
		caption = f.read().strip().format(name, deck_name, meanings[card_id])
		await context.bot.send_photo(chat_id, card_path, caption)


async def send_test_card(_, context):
	await send_daily_card(context, admin_id)


async def list_subscriber_names(update, context):
	message = ""
	for user_id in daily_ids:
		user = await context.bot.get_chat(user_id)
		message += f"{user_id} — <b>{user.full_name}</b>"
		if user.username:
			message += f" (@{user.username})"
		message += "\n"
	await update.message.reply_text(message)


async def unknown(update, _):
	await update.message.reply_text("Такой команды ещё не придумали!")


async def error_callback(_, context):
	if isinstance(context.error, Conflict):
		logger.error(context.error)
	elif not isinstance(context.error, NetworkError):
		raise context.error


def main():
	global logger, admin_id, daily_ids, keys, paths

	logger = logging.getLogger(__name__)
	logging.basicConfig(
		format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
		level=logging.INFO)

	config_file = 'vestnik.conf'
	config = configparser.ConfigParser()
	config.read(config_file)
	keys = config['keys']
	paths = config['paths']
	job_hour = config.getint('time', 'hour')
	admin_id = int(keys['admin'])

	defaults = Defaults(parse_mode='HTML')
	application = Application.builder().token(keys['token']).defaults(defaults).build()

	try:
		with open(paths['sub_list']) as f:
			daily_ids = json.load(f)
	except ValueError:
		logger.error(f"Unable to decode JSON in {paths['sub_list']}, terminating")
		return
	except OSError:
		logger.warning(f"File {paths['sub_list']} doesn't exist, creating")
		open(paths['sub_list'], 'a').close()

	admin_filter = filters.User(admin_id)

	# ignore the dumb warning about the `days` parameter for jobs
	warnings.filterwarnings('ignore', "Prior to v20.0 the `days` parameter")

	application.add_handler(CommandHandler(['start', 'help'], start))
	application.add_handler(CommandHandler('sendtestcard', send_test_card, admin_filter))
	application.add_handler(CommandHandler('listsubnames', list_subscriber_names, admin_filter))

	application.add_handler(CallbackQueryHandler(button_handler))

	application.add_handler(ChatMemberHandler(track_channel_members, ChatMemberHandler.CHAT_MEMBER))
	application.add_handler(ChatMemberHandler(blocked_handler, ChatMemberHandler.MY_CHAT_MEMBER))
	application.add_handler(ChatJoinRequestHandler(request_greet))

	application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.COMMAND, unknown))
	application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, start))

	application.add_error_handler(error_callback)

	application.job_queue.run_daily(send_daily_message, datetime.time(hour=job_hour))

	application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
	main()
