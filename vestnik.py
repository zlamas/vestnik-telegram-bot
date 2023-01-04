#!/usr/bin/env python3
import json
import logging
import random
import datetime
import configparser
import warnings

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember, LabeledPrice
from telegram.ext import Application, Defaults, filters, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, PreCheckoutQueryHandler
from telegram.error import Forbidden, Conflict, NetworkError


async def is_member(update, context):
	user_id = update.effective_user.id
	result = await context.bot.get_chat_member(keys['channel'], user_id)
	return result.status in ['member', 'administrator']


async def start(update, context):
	if await is_member(update, context):
		with open(paths['welcome']) as f:
			welcome_message = f.read().strip()

		markup = get_start_ik(update.effective_user.id in daily_ids)
		await update.effective_message.reply_text(welcome_message, reply_markup=markup)
	else:
		await stranger_reply(update)


async def stranger_reply(update):
	with open(paths['stranger']) as f:
		stranger_message = f.read().strip()

	markup = InlineKeyboardMarkup([[
		InlineKeyboardButton("Подписаться", f"https://t.me/{keys['invite']}")
	]])
	await update.effective_message.reply_text(stranger_message, reply_markup=markup)


def get_start_ik(is_subscribed):
	if is_subscribed:
		sub_button_text = "Отписаться"
		action = 'unsub_daily'
	else:
		sub_button_text = "Подписаться"
		action = 'sub_daily'

	return InlineKeyboardMarkup([[
		InlineKeyboardButton(sub_button_text, callback_data=action)
	]])


async def button(update, context):
	query = update.callback_query
	await query.answer()

	if query.data == 'sub_daily':
		if await is_member(update, context):
			markup = get_start_ik(True)
			await query.edit_message_reply_markup(reply_markup=markup)
			await subscribe_daily(update)
		else:
			await stranger_reply(update)
	elif query.data == 'unsub_daily':
		markup = get_start_ik(False)
		await query.edit_message_reply_markup(reply_markup=markup)
		await unsubscribe_daily(update)


def extract_status_change(chat_member_update):
	"""Takes a ChatMemberUpdated instance and extracts whether the 'old_chat_member' was a member
	of the chat and whether the 'new_chat_member' is a member of the chat. Returns None, if
	the status didn't change.
	"""
	status_change = chat_member_update.difference().get('status')
	old_is_member, new_is_member = chat_member_update.difference().get('is_member', (None, None))

	if status_change is None:
		return None

	old_status, new_status = status_change
	was_member = old_status in [
		ChatMember.MEMBER,
		ChatMember.CREATOR,
		ChatMember.ADMINISTRATOR,
	] or (old_status == ChatMember.RESTRICTED and old_is_member is True)
	is_member = new_status in [
		ChatMember.MEMBER,
		ChatMember.CREATOR,
		ChatMember.ADMINISTRATOR,
	] or (new_status == ChatMember.RESTRICTED and new_is_member is True)

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


async def subscribe_daily(update):
	chat = update.effective_chat

	if chat.id in daily_ids:
		response = "Вы уже подписаны!"
	else:
		daily_ids.append(chat.id)
		save_daily_list()

		logger.info(f"{chat.full_name} ({chat.id}) subscribed to bot")
		response = "Подписка оформлена!"

	await update.effective_message.reply_text(response)


async def unsubscribe_daily(update, left_channel=False):
	chat = update.effective_chat

	if chat.id in daily_ids:
		remove_user(chat.id)
		logger.info(f"{chat.full_name} ({chat.id}) unsubscribed from bot")
		response = "Вы отписались."
	else:
		response = "Вы не подписаны!"

	await update.effective_message.reply_text(response)


def remove_user(user_id, user_name=None):
	daily_ids.pop(user_id)
	save_daily_list()
	if user_name is not None:
		logger.info(f"Removing user {user_name} ({user_id}) from messaging list")


def save_daily_list():
	with open(paths['sub_list'], 'w') as f:
		json.dump(daily_ids, f)


async def send_message(context):
	for chat_id in daily_ids:
		await send_daily_card(context, chat_id)


async def send_daily_card(context, chat_id):
	with open(paths['data']) as f:
		data = json.load(f)

	card_id = random.randrange(78)
	decks = list(data['decks'].items())
	deck_id, deck_name = random.choice(decks)
	image_path = f"{paths['image_dir']}/{deck_id}/{card_id}.jpg"

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

	try:
		with open(image_path, 'rb') as img:
			await context.bot.send_photo(chat_id, img, caption)
	except Forbidden:
		await remove_blocked_user(context, chat_id)


async def remove_blocked_user(context, chat_id):
	user_name = (await context.bot.get_chat(chat_id)).full_name
	logger.error(f"Failed to send message to {user_name} ({chat_id}): user blocked the bot")
	remove_user(chat_id, user_name)


async def send_test_card(_, context):
	await send_daily_card(context, daily_ids[0])


async def list_subscriber_names(update, context):
	message = ""
	for user_id in daily_ids:
		chat = await context.bot.get_chat(user_id)
		message += f"{user_id} — <b>{chat.full_name}</b>"
		if chat.username:
			message += f" (@{chat.username})"
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
	global logger, daily_ids, keys, paths

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

	admin_filter = filters.User(daily_ids[0])

	# ignore the dumb warning about the `days` parameter for jobs
	warnings.filterwarnings("ignore", "Prior to v20.0 the `days` parameter")

	application.add_handler(CommandHandler(['start', 'help'], start))
	application.add_handler(CommandHandler('sendtestcard', send_test_card, admin_filter))
	application.add_handler(CommandHandler('listsubnames', list_subscriber_names, admin_filter))

	application.add_handler(CallbackQueryHandler(button))

	application.add_handler(ChatMemberHandler(track_channel_members, ChatMemberHandler.CHAT_MEMBER))

	application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.COMMAND, unknown))
	application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, start))

	application.add_error_handler(error_callback)

	application.job_queue.run_daily(send_message, datetime.time(hour=job_hour))

	application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
	main()
