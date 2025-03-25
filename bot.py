import asyncio
import email
import email.header
import email.utils
import sys
import logging
from datetime import datetime
from email.utils import getaddresses
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from imapclient import IMAPClient
import os
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import pytz  # Добавляем для работы с часовыми поясами

# Настроим logging для корректного вывода сообщений в UTF-8
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', handlers=[
    logging.StreamHandler(sys.stdout)
])

# Загружаем переменные окружения из .env
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID"))
EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER")
FILTER_EMAIL = os.getenv("FILTER_EMAIL")  # Ожидается "robot@another-world.com"
SECRET_PASSWORD = os.getenv("SECRET_PASSWORD")

approved_users = set()

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="/status"), KeyboardButton(text="/help")]],
    resize_keyboard=True
)


def decode_header_value(header_value):
    """Декодирует заголовок письма, если он закодирован."""
    decoded_fragments = email.header.decode_header(header_value)
    return ''.join([fragment.decode(encoding) if isinstance(fragment, bytes) and encoding else fragment
                    for fragment, encoding in decoded_fragments])


def format_date(date_str):
    """Преобразует дату из заголовка письма в формат 'ДД Месяц ГГГГ, ЧЧ:ММ' на русском с учётом МСК."""
    russian_months = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
        7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
    }
    try:
        dt = email.utils.parsedate_to_datetime(date_str)

        # Преобразуем в московское время (UTC+3)
        msk_tz = pytz.timezone("Europe/Moscow")
        dt = dt.astimezone(msk_tz)

        day = dt.day
        month = russian_months.get(dt.month, str(dt.month))
        year = dt.year
        hour = dt.hour
        minute = dt.minute
        return f"{day:02d} {month} {year}, {hour:02d}:{minute:02d}"
    except Exception as e:
        logging.error(f"Ошибка форматирования даты: {e}")
        return date_str


async def process_folder(mail, folder_name):
    """Проверяет указанную папку и пересылает письма от robot@another-world.com."""
    try:
        mail.select_folder(folder_name)
    except Exception as e:
        logging.error(f"Ошибка при выборе папки {folder_name}: {e}")
        return

    logging.info(f"Проверка новых писем в папке '{folder_name}'...")
    try:
        messages = mail.search(['UNSEEN'])
        logging.info(f"Результат поиска в '{folder_name}': {messages}")
        if messages:
            logging.info(f"Найдено новых сообщений в {folder_name}: {len(messages)}")
            for msg_id in messages:
                msg_data = mail.fetch(msg_id, "RFC822")
                for response_part in msg_data.values():
                    msg = email.message_from_bytes(response_part[b"RFC822"])
                    # Проверяем отправителя
                    addresses = getaddresses([msg.get("From", "")])
                    if not any("robot@another-world.com" in addr.lower() for name, addr in addresses):
                        logging.info(f"Пропущено письмо от: {addresses}")
                        continue

                    # Извлекаем тело письма (HTML-часть)
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/html":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                    logging.info(f"Исходное тело письма: {body}")

                    # Заменяем теги <br> на переводы строк
                    body = body.replace("<br>", "\n").replace("<br/>", "\n")
                    soup = BeautifulSoup(body, "html.parser")
                    body_text = soup.get_text(separator="\n")
                    logging.info(f"Текст из HTML: {body_text}")

                    # Разбиваем текст на строки и удаляем пустые
                    body_lines = [line.strip() for line in body_text.splitlines() if line.strip()]
                    logging.info(f"Количество строк в письме: {len(body_lines)}")

                    # Фильтруем строки, оставляя только те, которые начинаются с ожидаемых полей
                    expected_fields = [
                        "Имя:", "Телефон:", "Эл. почта:", "Дата:", "Время:",
                        "Время окончания бронирования:", "Игра:", "Количество игроков:",
                        "Сумма заказа:", "Промокод:", "Нужно доплатить на арене:", "Заявка на день рождения",
                        "Новое бронирование на сайте ВАШ_ГОРОД.another-world.com"
                    ]
                    details_lines = [line for line in body_lines if
                                     any(line.startswith(field) for field in expected_fields)]

                    # Ищем фразу о заявке на день рождения или бронировании на сайте
                    additional_info = ""
                    if "Заявка на день рождения" in body_text:
                        additional_info = "Заявка на день рождения с сайта ВАШ_ГОРОД.another-word.com"
                    elif "Новое бронирование на сайте ВАШ_ГОРОД.another-word.com" in body_text:
                        additional_info = "Новое бронирование на сайте ВАШ_ГОРОД.another-word.com"

                    if details_lines:
                        # Формируем уведомление с данными из письма
                        date_formatted = format_date(msg.get("Date", "неизвестно"))
                        text = f"📩 *Новое уведомление от робота за {date_formatted}!*\n\n" \
                               f"{additional_info}\n\n" + "\n".join(details_lines) + "\n\n" \
                                                                                     f"💡 Пожалуйста, свяжитесь с клиентом!"
                        logging.info(f"Отправка уведомления из '{folder_name}': {text[:30]}...")
                        await bot.send_message(TELEGRAM_GROUP_ID, text, parse_mode="Markdown")
                        mail.set_flags(msg_id, ["\\Seen"])
                    else:
                        logging.warning(f"Не удалось извлечь данные из письма. Полных строк: {body_lines}")
        else:
            logging.info(f"Новых писем в {folder_name} не найдено.")
    except Exception as e:
        logging.error(f"Ошибка при проверке писем в {folder_name}: {e}")


async def check_mail_loop():
    while True:
        try:
            logging.info("Подключение к IMAP серверу...")
            with IMAPClient(IMAP_SERVER) as mail:
                mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
                logging.info("Подключение установлено.")
                await process_folder(mail, "INBOX")
                await process_folder(mail, "[Gmail]/Спам")
                await asyncio.sleep(10)
        except Exception as e:
            logging.error(f"Ошибка: {e}")
        await asyncio.sleep(10)


# Команды для работы бота в Telegram
@dp.message(Command("start"))
async def start_command(message: types.Message):
    if message.chat.type == "private":
        await message.answer("\U0001F44B Привет! Введите пароль для доступа:")
    else:
        await message.answer("Бот работает в группе и пересылает бронирования.")


@dp.message(Command("status"))
async def status_command(message: types.Message):
    if message.chat.type == "private":
        if message.from_user.id in approved_users:
            await message.answer("\U00002705 Бот работает и следит за новыми бронированиями!", reply_markup=menu)
        else:
            await message.answer("\U0001F512 Введите пароль для доступа.")
    else:
        await message.answer("\U00002705 Бот работает в группе.")


@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "ℹ️ Этот бот пересылает бронирования в группу.\n\nКоманды:\n/status – Проверить статус\n/help – Помощь")


@dp.message()
async def check_password(message: types.Message):
    if message.chat.type == "private":
        if message.text == SECRET_PASSWORD:
            approved_users.add(message.from_user.id)
            await message.answer("\U00002705 Доступ подтверждён! Теперь вы можете использовать команды.",
                                 reply_markup=menu)
        else:
            await message.answer("\U0000274C Неверный пароль. Попробуйте снова.")


async def main():
    logging.info("Запуск бота...")
    asyncio.create_task(check_mail_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
