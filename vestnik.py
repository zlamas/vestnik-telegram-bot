#!/usr/bin/env python3
import json
import logging
import random
import datetime
import argparse
import configparser

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat, ChatMember, ChatMemberUpdated
from telegram.ext import Updater, CallbackContext, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, Filters, Defaults
from telegram.error import Unauthorized, NetworkError

logger = logging.getLogger(__name__)


def is_member(update: Update, context: CallbackContext):
	user_id = update.effective_user.id
	result = context.bot.get_chat_member(keys['channel'], user_id)
	return result.status in ['member', 'administrator']


def start(update: Update, context: CallbackContext):
	if is_member(update, context):
		with open(paths['welcome']) as f:
			welcome_message = f.read().strip()

		markup = get_start_ik(str(update.effective_user.id) in daily_ids)
		update.effective_message.reply_text(welcome_message, reply_markup=markup)
	else:
		stranger_reply(update)


def stranger_reply(update: Update):
	with open(paths['stranger']) as f:
		stranger_message = f.read().strip()

	markup = InlineKeyboardMarkup([[
		InlineKeyboardButton("Подписаться", f"https://t.me/{keys['invite']}")
	]])
	update.effective_message.reply_text(stranger_message, reply_markup=markup)


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


def button(update: Update, context: CallbackContext):
	query = update.callback_query
	query.answer()

	if query.data == 'sub_daily':
		if is_member(update, context):
			markup = get_start_ik(True)
			query.edit_message_reply_markup(reply_markup=markup)
			subscribe_daily(update)
		else:
			stranger_reply(update)
	elif query.data == 'unsub_daily':
		markup = get_start_ik(False)
		query.edit_message_reply_markup(reply_markup=markup)
		unsubscribe_daily(update)


def extract_status_change(chat_member_update: ChatMemberUpdated):
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


def track_channel_members(update: Update, context: CallbackContext):
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
		if str(user.id) in daily_ids:
			with open(paths['left_channel']) as f:
				left_channel_message = f.read().strip()

			context.bot.send_message(user.id, left_channel_message)
			remove_user(user.id, user.full_name)


def subscribe_daily(update: Update):
	chat = update.effective_chat

	if str(chat.id) in daily_ids:
		response = "Вы уже подписаны!"
	else:
		daily_ids[chat.id] = 0
		save_daily_list()

		logger.info(f"{chat.full_name} ({chat.id}) subscribed to bot")
		response = "Подписка оформлена!"

	update.effective_message.reply_text(response)


def unsubscribe_daily(update: Update, left_channel=False):
	chat = update.effective_chat

	if str(chat.id) in daily_ids:
		remove_user(chat.id)
		logger.info(f"{chat.full_name} ({chat.id}) unsubscribed from bot")
		response = "Вы отписались."
	else:
		response = "Вы не подписаны!"

	update.effective_message.reply_text(response)


def remove_user(user_id, user_name=None):
	daily_ids.pop(user_id)
	save_daily_list()
	if user_name is not None:
		logger.info(f"Removing user {user_name} ({user_id}) from messaging list")


def save_daily_list():
	with open(paths['sub_list'], 'w') as f:
		json.dump(daily_ids, f)


def send_message(context: CallbackContext):
	for chat_id, days_left in daily_ids.items():
		# if days_left == 0:
			# send_reminder(context, chat_id)
		# else:
		send_daily_card(context, chat_id)
			# daily_ids[chat_id] -= 1
			# save_daily_list()


def send_reminder(context: CallbackContext, chat_id):
	try:
		with open(paths['sub_message']) as f:
			context.bot.send_message(chat_id, f.read().strip())
	except Unauthorized:
		remove_blocked_user(context, chat_id)


def send_daily_card(context: CallbackContext, chat_id):
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
			context.bot.send_photo(chat_id, img, caption)
	except Unauthorized:
		remove_blocked_user(context, chat_id)


def send_test_card(update: Update, context: CallbackContext):
	admin_id = next(iter(daily_ids))
	if update.effective_user.id == int(admin_id):
		send_daily_card(context, admin_id)


def remove_blocked_user(context: CallbackContext, chat_id):
	user_name = context.bot.get_chat(chat_id).full_name
	logger.error(f"Failed to send message to {user_name} ({chat_id}): user blocked the bot")
	remove_user(chat_id, user_name)


def unknown(update: Update, _: CallbackContext):
	update.message.reply_text("Такой команды ещё не придумали!")


def error_callback(_: Update, context: CallbackContext):
	if not isinstance(context.error, NetworkError):
		raise context.error


def main():
	global daily_ids, keys, paths

	arg_parser = argparse.ArgumentParser()
	arg_parser.add_argument('-l', '--list', action='store_true', help='list subscribers')
	args = arg_parser.parse_args()

	config_file = 'vestnik.conf'
	config = configparser.ConfigParser()
	config.read(config_file)
	keys = config['keys']
	paths = config['paths']
	job_hour = config.getint('time', 'hour')

	updater = Updater(keys['token'], defaults=Defaults(parse_mode='HTML'))

	if args.list:
		with open(paths['sub_list']) as f:
			for user_id in json.load(f).keys():
				print(user_id, updater.bot.get_chat(user_id).full_name)
		return

	logging.basicConfig(
		format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
		level=logging.INFO)
	logging.getLogger("telegram.vendor.ptb_urllib3.urllib3").setLevel(logging.ERROR)

	try:
		with open(paths['sub_list']) as f:
			daily_ids = json.load(f)
	except ValueError:
		logger.error(f"Unable to decode JSON in {paths['sub_list']}, terminating")
		return
	except OSError:
		logger.warning(f"File {paths['sub_list']} doesn't exist, creating")
		open(paths['sub_list'], 'a').close()

	dispatcher = updater.dispatcher

	dispatcher.add_handler(CommandHandler(['start', 'help'], start))
	dispatcher.add_handler(CommandHandler('send_test_card', send_test_card))

	dispatcher.add_handler(CallbackQueryHandler(button))

	dispatcher.add_handler(ChatMemberHandler(track_channel_members, ChatMemberHandler.CHAT_MEMBER))

	dispatcher.add_handler(MessageHandler(Filters.chat_type.private & Filters.command, unknown))
	dispatcher.add_handler(MessageHandler(Filters.chat_type.private & Filters.text, start))

	dispatcher.add_error_handler(error_callback)

	updater.start_polling(allowed_updates=Update.ALL_TYPES)
	logger.info("Bot started")

	updater.job_queue.run_daily(send_message, datetime.time(hour=job_hour))

	updater.idle()


if __name__ == '__main__':
	main()
