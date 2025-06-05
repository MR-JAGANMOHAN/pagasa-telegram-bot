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
# These would typically be set as environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_FALLBACK_TOKEN_IF_ANY")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "YOUR_FALLBACK_CHANNEL_ID_IF_ANY")
PAGASA_URL = "https://www.pagasa.dost.gov.ph/regional-forecast/ncrprsd"
DATA_FILE = "weather_data.json"

DIV_IDS = {
    "rainfalls": "Rainfall Advisory",
    "thunderstorms": "Thunderstorm Advisory/Watch"
}

# --- Helper Functions ---

def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2 parse mode."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
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
    """
    Parses the HTML content to find the text of the first immediate child div
    of the div with a given div_id. If no immediate child div is found,
    it uses the text of the div_id itself. Handles <br> as newlines.
    """
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        target_div = soup.find("div", id=div_id)
        
        if not target_div:
            print(f"Div with ID '{div_id}' not found.")
            # Use a more descriptive message based on DIV_IDS if available
            advisory_name = DIV_IDS.get(div_id, div_id) # Fallback to div_id if not in DIV_IDS
            return f"The section for {advisory_name} could not be found on the page."

        # Find the first *immediate* child div of the target_div.
        # If no immediate child div exists, use the target_div itself as the source.
        first_immediate_child_div = target_div.find("div", recursive=False)
        
        content_source_div = first_immediate_child_div if first_immediate_child_div else target_div

        # Replace <br> tags with newline characters within the content_source_div.
        # This searches for all <br> tags anywhere *within* content_source_div.
        for br_tag in content_source_div.find_all("br"):
            br_tag.replace_with("\n")
        
        # Get text from the content_source_div.
        # separator="\n" helps to preserve newlines between different text blocks
        # that might arise from other HTML tags within the content_source_div.
        # strip=True removes leading/trailing whitespace from the entire extracted text.
        raw_text = content_source_div.get_text(separator="\n", strip=True)
        
        # Clean up multiple consecutive newlines (possibly mixed with spaces)
        # and reduce them to a maximum of two newlines (to represent a paragraph break).
        # Also, ensure single newlines are preserved.
        cleaned_text = re.sub(r'\n\s*\n+', '\n\n', raw_text).strip()
        
        return cleaned_text

    except Exception as e:
        print(f"Error parsing HTML content for div '{div_id}': {e}")
        # Use a more descriptive message based on DIV_IDS if available
        advisory_name = DIV_IDS.get(div_id, div_id) # Fallback to div_id if not in DIV_IDS
        return f"Error parsing content for {advisory_name}."


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
    if not bot_token or not channel_id or bot_token == "YOUR_FALLBACK_TOKEN_IF_ANY":
        print("Telegram Bot Token or Channel ID is not configured correctly. Skipping notification.")
        return

    bot = Bot(token=bot_token)
    escaped_advisory_type = escape_markdown_v2(advisory_type)
    header = escape_markdown_v2("🇵🇭 PAGASA Update: ") + escaped_advisory_type + escape_markdown_v2(" 🇵🇭") # Adding some emojis
    
    # Ensure message_text itself is not None or empty before escaping
    if not message_text:
        print(f"Message text for {advisory_type} is empty. Skipping notification.")
        return
        
    escaped_message_text = escape_markdown_v2(message_text)
    full_message = f"{header}\n\n{escaped_message_text}"

    try:
        await bot.send_message(chat_id=channel_id, text=full_message, parse_mode=ParseMode.MARKDOWN_V2)
        print(f"Message sent to Telegram channel {channel_id} for {advisory_type}.")
    except TelegramError as e:
        print(f"Error sending MarkdownV2 message to Telegram for {advisory_type}: {e}")
        # Fallback: try sending as plain text if MarkdownV2 fails
        try:
            print(f"Attempting to send as plain text for {advisory_type} due to previous error.")
            plain_text_header = f"🇵🇭 PAGASA Update: {advisory_type} 🇵🇭"
            plain_text_message = f"{plain_text_header}\n\n{message_text}" # Original, unescaped text
            await bot.send_message(chat_id=channel_id, text=plain_text_message)
            print(f"Fallback plain text message sent for {advisory_type}.")
        except TelegramError as e2:
            print(f"Error sending fallback plain text message for {advisory_type}: {e2}")
    except Exception as e:
        print(f"An unexpected error occurred during Telegram send for {advisory_type}: {e}")

# --- Main Logic ---
async def main():
    print("Starting weather check...")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID or \
       TELEGRAM_BOT_TOKEN == "YOUR_FALLBACK_TOKEN_IF_ANY" or \
       TELEGRAM_CHANNEL_ID == "YOUR_FALLBACK_CHANNEL_ID_IF_ANY":
        print("CRITICAL: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID environment variables are not set or are placeholders. Exiting.")
        return

    html_content = await fetch_website_content(PAGASA_URL)
    if not html_content:
        print("Failed to fetch website content. Exiting.")
        # Attempt to send a generic error message if fetching fails
        error_message = "Failed to fetch the latest weather data from PAGASA website. Please check the site directly."
        await send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, error_message, "Website Fetch Error")
        return

    previous_data = load_previous_data(DATA_FILE)
    current_data = {}
    notifications_to_send = []

    is_first_overall_run = not os.path.exists(DATA_FILE)

    for div_id, advisory_name in DIV_IDS.items():
        print(f"\nProcessing {advisory_name} (div_id: {div_id})...")
        content = parse_div_content(html_content, div_id)

        # Check if parsing returned an error message or valid content
        if content is None or "could not be found" in content or "Error parsing content" in content:
            print(f"  Content for {advisory_name} problem: {content}")
            # If content is an error message from parsing, use it directly.
            # If it's None for some other reason, craft a message.
            if content is None:
                content = f"Unable to retrieve content for {advisory_name} at this time. Please check the PAGASA website directly."
        
        current_data[div_id] = content
        previous_content = previous_data.get(div_id)

        print(f"  Previous content for {div_id}: {'Not found or empty' if not previous_content else 'Found'}")
        # print(f"  Current content for {div_id}: {content[:100] if content else 'Empty'}...")


        should_notify = False
        if is_first_overall_run:
            print(f"  Overall first run detected. Will notify if content exists.")
            if content and not ("could not be found" in content or "Error parsing content" in content): # Only notify if valid content
                should_notify = True
        elif previous_content is None: # No previous data for this specific advisory
            print(f"  No previous data found for {advisory_name}. Will notify if content exists.")
            if content and not ("could not be found" in content or "Error parsing content" in content):
                should_notify = True
        elif content != previous_content:
            print(f"  Content for {advisory_name} has changed.")
            # We notify even if the new content is an error message, to inform about the change/problem
            should_notify = True 
        else:
            print(f"  Content for {advisory_name} has not changed.")

        if should_notify and content: # Ensure content is not empty
            print(f"  Queueing notification for {advisory_name}.")
            notifications_to_send.append({
                "token": TELEGRAM_BOT_TOKEN,
                "channel": TELEGRAM_CHANNEL_ID,
                "message": content, # This might be an error message or actual advisory
                "type": advisory_name
            })
        elif should_notify and not content:
             print(f"  Change detected for {advisory_name}, but new content is empty. Not sending notification.")


    if notifications_to_send:
        await asyncio.gather(*(
            send_telegram_message(n["token"], n["channel"], n["message"], n["type"])
            for n in notifications_to_send
        ))
    else:
        print("\nNo new changes or valid new content detected that require notification.")

    save_current_data(DATA_FILE, current_data)
    print("\nWeather check finished.")

if __name__ == "__main__":
    # Example HTML for testing parse_div_content locally
    # To run this test, uncomment it and comment out asyncio.run(main())
    # You'd also need to make parse_div_content non-async or wrap its call.
    """
    test_html_thunderstorm_nested = '''
    <div id="thunderstorms" class="tab-pane fade in active">
        <div> <!-- This is the first immediate child div -->
            <div>Thunderstorm Watch #NCR_PRSD</div> <!-- This is a grandchild -->
            <br/>
            Issued at: 10:00 PM, 05 June 2025<br/>
            <br/>
            Thunderstorm is MORE LIKELY to develop over Greater Metro Manila Area(Metro Manila, Bulacan, Rizal, Laguna and Cavite) within 12 hours. <br/>
            <br/>
            All are advised to continue monitoring for updates.
        </div>
        <div>Another immediate child div</div>
    </div>
    '''
    test_html_thunderstorm_flat = '''
    <div id="thunderstorms" class="tab-pane fade in active">
        <div><!-- First immediate child -->
            Thunderstorm Watch #NCR_PRSD<br>
            Issued at: 10:00 PM, 05 June 2025<br>
            <br>
            Thunderstorm is MORE LIKELY to develop over Greater Metro Manila Area(Metro Manila, Bulacan, Rizal, Laguna and Cavite) within 12 hours. <br>
            <br>
            All are advised to continue monitoring for updates.
        </div>
        <div><!-- Second immediate child -->
            Thunderstorm Advisory No. 6 #NCR_PRSD<br>
            Issued at: 9:30 PM, 05 June 2025(Thursday)<br>
        </div>
    </div>
    '''
    test_html_no_child_div = '''
    <div id="rainfalls">
        Rainfall Advisory #1<br>
        Issued at: 1:00 PM<br>
        Light to moderate rains over Metro Manila.
    </div>
    '''
    
    # print("--- Testing with FLAT structure (user's example) ---")
    # result_flat = parse_div_content(test_html_thunderstorm_flat, "thunderstorms")
    # print(result_flat)
    # print("\\n--- Testing with NESTED structure (potential issue) ---")
    # result_nested = parse_div_content(test_html_thunderstorm_nested, "thunderstorms") # Original code would fail here
    # print(result_nested) # Fixed code should work
    # print("\\n--- Testing with NO CHILD DIV structure ---")
    # result_no_child = parse_div_content(test_html_no_child_div, "rainfalls")
    # print(result_no_child)
    # print("\\n--- Testing with non-existent DIV ---")
    # result_not_found = parse_div_content(test_html_thunderstorm_flat, "nonexistent")
    # print(result_not_found)
    """
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"An error occurred in the main execution: {e}")

