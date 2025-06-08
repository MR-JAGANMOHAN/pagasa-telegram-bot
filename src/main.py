import asyncio
import aiohttp
import os
from bs4 import BeautifulSoup
from telegram import Bot
import json
import logging
import re
import requests
from io import BytesIO

# Get secrets from environment variables
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("TELEGRAM_CHANNEL_ID")
DATA_FILE = "previous_data.json"
ADVISORY_DATA_FILE = "previous_weather_advisory.json"
TEST_ADVISORY_CHANNEL = "@testchanneljrp"
URL = "https://www.pagasa.dost.gov.ph/regional-forecast/ncrprsd"
ADVISORY_URL = "https://www.pagasa.dost.gov.ph/weather/weather-advisory"

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


async def fetch_html(session, url):
    async with session.get(url) as response:
        return await response.text()


def parse_first_child_text(soup, div_id):
    div = soup.find("div", id=div_id)
    if not div:
        return None

    first_child = div.find("div")
    if not first_child:
        return None

    # Extract raw HTML, convert <br> to \n, and clean up
    raw_html = first_child.decode_contents()
    text_with_newlines = (
        raw_html.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    )
    return BeautifulSoup(text_with_newlines, "html.parser").get_text().strip()


def load_previous_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    else:
        with open(DATA_FILE, "w") as f:
            json.dump({}, f)
        return {}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)


async def send_to_telegram(bot, message):
    logging.info(
        f"Sending message to Telegram: {message[:60]}{'...' if len(message) > 60 else ''}"
    )
    await bot.send_message(chat_id=CHANNEL_USERNAME, text=message, parse_mode="HTML")
    logging.info("Message sent to Telegram.")


def get_previous_advisory_number():
    if not os.path.exists(ADVISORY_DATA_FILE):
        return None
    try:
        with open(ADVISORY_DATA_FILE, "r") as f:
            data = json.load(f)
            return data.get("weather_advisory_no")
    except Exception:
        return None


def set_previous_advisory_number(number):
    data = {"weather_advisory_no": number}
    with open(ADVISORY_DATA_FILE, "w") as f:
        json.dump(data, f)


async def check_weather_advisory(bot):
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, ADVISORY_URL)
    soup = BeautifulSoup(html, "html.parser")
    adv_div = soup.find("div", class_="weather-advisory")
    if not adv_div:
        logging.info("No weather-advisory div found.")
        return
    h4 = adv_div.find("h4")
    if not h4:
        logging.info("No h4 found in weather-advisory div.")
        return
    m = re.search(r"WEATHER ADVISORY NO\.?\s*(\d+)", h4.get_text())
    if not m:
        logging.info(f"No advisory number found in h4. h4 text: {h4.get_text()}")
        return
    advisory_no = m.group(1)
    prev_no = get_previous_advisory_number()
    if prev_no == advisory_no:
        logging.info("No new weather advisory.")
        return
    # Check for Metro Manila in weekly-content-adv (including inside HTML comments)
    weekly_div = soup.find("div", class_="weekly-content-adv")
    found_metro_manila = False
    if weekly_div:
        # Check visible text
        if "Metro Manila" in weekly_div.get_text():
            found_metro_manila = True
        else:
            from bs4 import Comment
            for comment in weekly_div.find_all(string=lambda text: isinstance(text, Comment)):
                if "Metro Manila" in comment:
                    found_metro_manila = True
                    break
    if not weekly_div or not found_metro_manila:
        logging.info("Metro Manila not mentioned in advisory content.")
        return
    # Find PDF link (first <a> in weather-advisory div)
    a_tag = adv_div.find("a", href=True)
    if not a_tag:
        logging.info("No PDF link found in advisory div.")
        return
    pdf_url = a_tag["href"]
    if not pdf_url.startswith("http"):
        pdf_url = "https://www.pagasa.dost.gov.ph" + pdf_url
    # Try to extract issued_at from comments in weekly-content-adv
    issued_at = None
    if weekly_div:
        from bs4 import Comment
        for comment in weekly_div.find_all(string=lambda text: isinstance(text, Comment)):
            m = re.search(r"ISSUED AT:?\s*([\w:, ]+\d{4})", comment)
            if m:
                issued_at = m.group(1).strip()
                break
    if not issued_at:
        issued_at = "(time not found)"
    # Send hyperlink message to Telegram (test channel), enable preview
    caption = f"Metro Manila is included in heavy rainfall outlooks in Weather Advisory No. {advisory_no}, issued at {issued_at}. <a href=\"{pdf_url}\">View PDF</a>"
    await bot.send_message(chat_id=TEST_ADVISORY_CHANNEL, text=caption, parse_mode="HTML", disable_web_page_preview=False)
    set_previous_advisory_number(advisory_no)
    logging.info("Sent new weather advisory link to test Telegram channel.")


async def main():
    logging.info("Starting PAGASA monitor script.")
    async with aiohttp.ClientSession() as session:
        try:
            html = await fetch_html(session, URL)
            logging.info("Fetched PAGASA forecast page successfully.")
        except Exception as e:
            logging.error(f"Failed to fetch PAGASA forecast page: {e}")
            return

    soup = BeautifulSoup(html, "html.parser")

    rain_text = parse_first_child_text(soup, "rainfalls")
    storm_text = parse_first_child_text(soup, "thunderstorms")

    new_data = {"rainfalls": rain_text, "thunderstorms": storm_text}

    old_data = load_previous_data()
    bot = Bot(token=BOT_TOKEN)

    tasks = []
    found_new = False

    for category in ["rainfalls", "thunderstorms"]:
        old = old_data.get(category)
        new = new_data.get(category)

        if new and new != old:
            if "Metro Manila" in new:
                found_new = True
                logging.info(
                    f"New {category} warning found and contains 'Metro Manila'. Sending to Telegram."
                )
                # Preserve paragraph breaks
                message = "\n\n".join(
                    [para.strip() for para in new.split("\n\n") if para.strip()]
                )
                message = re.sub(
                    r"(?<!Greater )Metro Manila", "<b><u>Metro Manila</u></b>", message
                )
                message = message.replace(
                    "Thunderstorm Advisory", "⛈️ <b>Thunderstorm Advisory</b>"
                )
                message = message.replace(
                    "Thunderstorm Watch", "🕑 <b>Thunderstorm Watch</b>"
                )
                message = message.replace(
                    "Moderate to heavy rainshowers with lightning and strong winds are expected over",
                    "🕑 Moderate to heavy rainshowers with lightning and strong winds are expected over",
                )
                message = message.replace(
                    "Heavy to intense rainshowers with lightning and strong winds are being experienced",
                    "☔ Heavy to intense rainshowers with lightning and strong winds are being experienced",
                )
                message = message.replace(
                    "Intense to torrential rainshowers with lightning and strong winds are being experienced in",
                    "☔ Intense to torrential rainshowers with lightning and strong winds are being experienced in",
                )
                message = message.replace(
                    "The above conditions are being experienced in",
                    "☔ The above conditions are being experienced in",
                )
                message = message.replace(
                    "Heavy Rainfall Warning", "⚠️ <b>Heavy Rainfall Warning</b>"
                )
                message = message.replace(
                    "all RAINFALL WARNING in these areas are now terminated", "<b>all RAINFALL WARNING in these areas are now terminated</b>"
                )
                message = message.replace("YELLOW WARNING LEVEL", "🟡 <b>YELLOW WARNING LEVEL</b>")
                message = message.replace("ORANGE WARNING LEVEL", "🟠 <b>ORANGE WARNING LEVEL</b>")
                message = message.replace("RED WARNING LEVEL", "🔴 <b>RED WARNING LEVEL</b>")
                tasks.append(send_to_telegram(bot, message))
            else:
                logging.info(
                    f"New {category} warning found but does NOT contain 'Metro Manila'. Not sending to Telegram."
                )
        elif new:
            logging.info(f"No new {category} warning. Same as previous.")
        else:
            logging.warning(f"No data found for {category}.")

    if tasks:
        try:
            await asyncio.gather(*tasks)
            logging.info("All new warnings sent to Telegram successfully.")
        except Exception as e:
            logging.error(f"Failed to send one or more messages to Telegram: {e}")
    else:
        logging.info("No new warnings to send.")

    try:
        save_data(new_data)
        logging.info("Saved new data to previous_data.json.")
    except Exception as e:
        logging.error(f"Failed to save data: {e}")

    await check_weather_advisory(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
        logging.info("PAGASA monitor script finished.")
    except Exception as e:
        logging.error(f"Script failed: {e}")
