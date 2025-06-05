import asyncio
import aiohttp
import os
from bs4 import BeautifulSoup
from telegram import Bot
import json

# Get secrets from environment variables
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("TELEGRAM_CHANNEL_ID")
DATA_FILE = "previous_data.json"
URL = "https://www.pagasa.dost.gov.ph/regional-forecast/ncrprsd"

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

    # Convert <br> to newline and extract full text
    for br in first_child.find_all("br"):
        br.replace_with("\n")
    return first_child.get_text(strip=True, separator="\n")

def load_previous_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

async def send_to_telegram(bot, message):
    await bot.send_message(chat_id=CHANNEL_USERNAME, text=message)

async def main():
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, URL)

    soup = BeautifulSoup(html, "html.parser")

    rain_text = parse_first_child_text(soup, "rainfalls")
    storm_text = parse_first_child_text(soup, "thunderstorms")

    new_data = {
        "rainfalls": rain_text,
        "thunderstorms": storm_text
    }

    old_data = load_previous_data()
    bot = Bot(token=BOT_TOKEN)

    tasks = []

    for category in ["rainfalls", "thunderstorms"]:
        old = old_data.get(category)
        new = new_data.get(category)

        if new and new != old:
            # Only send the descriptive part (remove the first 2 lines)
            lines = new.splitlines()
            if len(lines) > 2:
                message = "\n".join(lines[2:]).strip()
            else:
                message = new.strip()

            tasks.append(send_to_telegram(bot, message))

    if tasks:
        await asyncio.gather(*tasks)

    save_data(new_data)

if __name__ == "__main__":
    asyncio.run(main())
