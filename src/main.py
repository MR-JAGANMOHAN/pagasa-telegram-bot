import os
import json
import asyncio
import httpx # Using httpx for asynchronous HTTP requests
import re # For escaping markdown characters
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
PAGASA_URL = "https://www.pagasa.dost.gov.ph/regional-forecast/ncrprsd"
DATA_FILE = "weather_data.json" # This will be created in the GitHub Actions runner

DIV_IDS = {
    "rainfalls": "Rainfall Advisory",
    "thunderstorms": "Thunderstorm Advisory/Watch"
}

# --- Helper Functions ---

def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2 parse mode."""
    # Order matters for some escape sequences.
    # Escape \ first, then other characters.
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    # Escape the escape character itself
    # text = text.replace('\\', '\\\\') # Not needed for the specific list below as per Telegram docs
    # Escape other special characters
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def fetch_website_content(url: str) -> str | None:
    """Fetches the HTML content of a given URL asynchronously."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except httpx.RequestError as e:
        print(f"Error fetching website content: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during website fetch: {e}")
        return None

def parse_div_content(html_content: str, div_id: str) -> str | None:
    """Parses the HTML content to find the text of the div with a given div ID, handling <br> as newlines."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        target_div = soup.find("div", id=div_id)
        if target_div:
            # If there is a child div, use it; otherwise, use the target div itself
            first_child_div = target_div.find("div")
            content_div = first_child_div if first_child_div else target_div

            # Replace <br> with newlines
            for br in content_div.find_all("br"):
                br.replace_with("\n")
            # Get text and clean up multiple newlines
            raw_text = content_div.get_text(separator="\n", strip=True)
            cleaned_text = re.sub(r'\n\s*\n+', '\n\n', raw_text).strip()
            return cleaned_text
        else:
            print(f"Div with ID '{div_id}' not found.")
            return f"The section for {DIV_IDS.get(div_id, div_id)} could not be found on the page."
    except Exception as e:
        print(f"Error parsing HTML content for div '{div_id}': {e}")
        return f"Error parsing content for {DIV_IDS.get(div_id, div_id)}."


def load_previous_data(file_path: str) -> dict:
    """Loads previously fetched data from a JSON file."""
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Error decoding JSON from {file_path}. Treating as first run for its data.")
            return {}
        except Exception as e:
            print(f"Warning: Error loading data from {file_path}: {e}. Treating as first run for its data.")
            return {}
    print(f"Info: {file_path} not found. Assuming first run or cache miss.")
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
    """Sends a message to a Telegram channel asynchronously using MarkdownV2."""
    if not bot_token or not channel_id:
        print("Telegram Bot Token or Channel ID is not configured. Skipping notification.")
        return

    bot = Bot(token=bot_token)
    # Escape the message text for MarkdownV2
    escaped_message_text = escape_markdown_v2(message_text)
    # Construct the message. Escape the advisory_type too if it could contain special chars.
    # For simplicity, assuming advisory_type is safe here, but for robustness, it could also be escaped.
    header = escape_markdown_v2(f"📢 {advisory_type} Update 📢")
    full_message = f"{header}\n\n{escaped_message_text}"

    try:
        await bot.send_message(chat_id=channel_id, text=full_message, parse_mode=ParseMode.MARKDOWN_V2)
        print(f"Message sent to Telegram channel {channel_id} for {advisory_type}.")
    except TelegramError as e:
        print(f"Error sending message to Telegram for {advisory_type}: {e}")
        # Fallback: try sending as plain text if MarkdownV2 fails
        try:
            print(f"Attempting to send as plain text for {advisory_type} due to previous error.")
            plain_text_message = f"📢 {advisory_type} Update 📢\n\n{message_text}" # Original, unescaped text
            await bot.send_message(chat_id=channel_id, text=plain_text_message)
            print(f"Fallback plain text message sent for {advisory_type}.")
        except TelegramError as e2:
            print(f"Error sending fallback plain text message for {advisory_type}: {e2}")
    except Exception as e:
        print(f"An unexpected error occurred during Telegram send for {advisory_type}: {e}")

# --- Main Logic ---
async def main():
    print("Starting weather check...")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("CRITICAL: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID environment variables are not set. Exiting.")
        return

    html_content = await fetch_website_content(PAGASA_URL)
    if not html_content:
        print("Failed to fetch website content. Exiting.")
        return

    # Try to load previous data. If weather_data.json doesn't exist (e.g. first run, or cache miss and not committed yet)
    # this will return an empty dict.
    previous_data = load_previous_data(DATA_FILE)
    current_data = {}
    notifications_to_send = []

    is_first_overall_run = not os.path.exists(DATA_FILE) # More reliable check for the *very* first run in the Action environment
                                                         # or if the file was deleted/cache cleared.

    for div_id, advisory_name in DIV_IDS.items():
        print(f"\nProcessing {advisory_name} (div_id: {div_id})...")
        content = parse_div_content(html_content, div_id)

        if content is None: # Should now be a string like "Error parsing..." or "No specific advisory..."
            print(f"  Content for {advisory_name} could not be properly parsed or was not found. Using placeholder message.")
            content = f"Unable to retrieve or parse content for {advisory_name} at this time. Please check the PAGASA website directly."


        current_data[div_id] = content
        previous_content = previous_data.get(div_id) # previous_data might be {}

        print(f"  Previous content for {div_id}: {'Not found in loaded data' if previous_content is None else 'Found'}")
        # print(f"  Current content for {div_id}: {content[:100]}...") # Print a snippet for brevity

        # Determine if notification is needed
        # Send if:
        # 1. It's the very first time this script runs *and* creates weather_data.json (is_first_overall_run is True)
        # 2. Or, if previous content for THIS specific advisory was not found (e.g. new advisory type added)
        # 3. Or, if current content is different from previous content.
        should_notify = False
        if is_first_overall_run:
            print(f"  Overall first run detected (data file '{DATA_FILE}' did not exist).")
            should_notify = True
        elif previous_content is None:
            print(f"  No previous data found for {advisory_name} specifically.")
            should_notify = True
        elif content != previous_content:
            print(f"  Content for {advisory_name} has changed.")
            should_notify = True
        else:
            print(f"  Content for {advisory_name} has not changed.")

        if should_notify and content: # Ensure content is not empty or truly None
            print(f"  Queueing notification for {advisory_name}.")
            notifications_to_send.append({
                "token": TELEGRAM_BOT_TOKEN,
                "channel": TELEGRAM_CHANNEL_ID,
                "message": content,
                "type": advisory_name
            })

    if notifications_to_send:
        await asyncio.gather(*(
            send_telegram_message(n["token"], n["channel"], n["message"], n["type"])
            for n in notifications_to_send
        ))
    else:
        print("\nNo new changes detected that require notification.")

    save_current_data(DATA_FILE, current_data)
    print("\nWeather check finished.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"An error occurred in the main execution: {e}")

