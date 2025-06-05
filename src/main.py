import os
import json
import asyncio
import httpx # Using httpx for asynchronous HTTP requests
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

# --- Configuration ---
# These will be set as environment variables in GitHub Actions
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID") # e.g., "@yourchannelname" or "-1001234567890" for private channels

# URL of the PAGASA regional forecast page
PAGASA_URL = "https://www.pagasa.dost.gov.ph/regional-forecast/ncrprsd"

# File to store the last fetched data
DATA_FILE = "weather_data.json"

# IDs of the divs to monitor
DIV_IDS = {
    "rainfalls": "Rainfall Advisory",
    "thunderstorms": "Thunderstorm Advisory/Watch"
}

# --- Helper Functions ---

async def fetch_website_content(url: str) -> str | None:
    """Fetches the HTML content of a given URL asynchronously."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client: # Increased timeout
            response = await client.get(url)
            response.raise_for_status()  # Raises an exception for 4XX/5XX errors
            return response.text
    except httpx.RequestError as e:
        print(f"Error fetching website content: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during website fetch: {e}")
        return None

def parse_div_content(html_content: str, div_id: str) -> str | None:
    """Parses the HTML content to find the text of the first child div of a given div ID."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        target_div = soup.find("div", id=div_id)
        if target_div:
            first_child_div = target_div.find("div") # Find the first direct child div
            if first_child_div:
                # Extract text, replace <br> with newlines, and strip leading/trailing whitespace
                # Convert <br> tags to newlines for better readability
                for br in first_child_div.find_all("br"):
                    br.replace_with("\n")
                return first_child_div.get_text(separator="\n", strip=True)
            else:
                print(f"No first child div found within div with ID '{div_id}'.")
                return f"Content for {DIV_IDS.get(div_id, div_id)} is currently not available or the structure has changed."
        else:
            print(f"Div with ID '{div_id}' not found.")
            return f"The section for {DIV_IDS.get(div_id, div_id)} could not be found on the page."
    except Exception as e:
        print(f"Error parsing HTML content for div '{div_id}': {e}")
        return None

def load_previous_data(file_path: str) -> dict:
    """Loads previously fetched data from a JSON file."""
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Error decoding JSON from {file_path}. Starting fresh.")
            return {}
        except Exception as e:
            print(f"Error loading data from {file_path}: {e}")
            return {}
    return {}

def save_current_data(file_path: str, data: dict):
    """Saves the current fetched data to a JSON file."""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        print(f"Data saved to {file_path}")
    except Exception as e:
        print(f"Error saving data to {file_path}: {e}")

async def send_telegram_message(bot_token: str, channel_id: str, message_text: str, advisory_type: str):
    """Sends a message to a Telegram channel asynchronously."""
    if not bot_token or not channel_id:
        print("Telegram Bot Token or Channel ID is not configured. Skipping notification.")
        return

    bot = Bot(token=bot_token)
    full_message = f"📢 **{advisory_type} Update** 📢\n\n{message_text}"
    try:
        await bot.send_message(chat_id=channel_id, text=full_message, parse_mode=ParseMode.MARKDOWN)
        print(f"Message sent to Telegram channel {channel_id} for {advisory_type}.")
    except TelegramError as e:
        print(f"Error sending message to Telegram: {e}")
    except Exception as e:
        print(f"An unexpected error occurred during Telegram send: {e}")

# --- Main Logic ---
async def main():
    """Main function to orchestrate the weather checking and notification."""
    print("Starting weather check...")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("CRITICAL: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID environment variables are not set. Exiting.")
        return

    html_content = await fetch_website_content(PAGASA_URL)
    if not html_content:
        print("Failed to fetch website content. Exiting.")
        return

    previous_data = load_previous_data(DATA_FILE)
    current_data = {}
    notifications_to_send = []

    is_first_run = not bool(previous_data) # Check if previous_data is empty

    for div_id, advisory_name in DIV_IDS.items():
        print(f"\nProcessing {advisory_name} (div_id: {div_id})...")
        content = parse_div_content(html_content, div_id)

        if content is None:
            print(f"Could not parse content for {advisory_name}. Using placeholder or skipping.")
            # Use a placeholder if content couldn't be parsed, to avoid breaking comparison logic
            # or to notify about the parsing issue.
            content = f"Unable to retrieve content for {advisory_name} at this time."


        current_data[div_id] = content
        previous_content = previous_data.get(div_id)

        print(f"  Previous content for {div_id}: {'Not found' if previous_content is None else 'Found'}")
        print(f"  Current content for {div_id}: {'Not found' if content is None else 'Found'}")


        if content != previous_content:
            print(f"  Content for {advisory_name} has changed or is new.")
            if is_first_run:
                print(f"  First run: Sending initial {advisory_name} to Telegram.")
                notifications_to_send.append({
                    "token": TELEGRAM_BOT_TOKEN,
                    "channel": TELEGRAM_CHANNEL_ID,
                    "message": content,
                    "type": advisory_name
                })
            else: # Not the first run, and content has changed
                 notifications_to_send.append({
                    "token": TELEGRAM_BOT_TOKEN,
                    "channel": TELEGRAM_CHANNEL_ID,
                    "message": content,
                    "type": advisory_name
                })
        else:
            print(f"  Content for {advisory_name} has not changed.")

    if notifications_to_send:
        # Send all notifications concurrently
        await asyncio.gather(*(
            send_telegram_message(n["token"], n["channel"], n["message"], n["type"])
            for n in notifications_to_send
        ))
    else:
        print("\nNo changes detected that require notification.")

    save_current_data(DATA_FILE, current_data)
    print("\nWeather check finished.")

if __name__ == "__main__":
    # Ensure event loop is handled correctly, especially for GitHub Actions
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"An error occurred in the main execution: {e}")
