import os
import json
import asyncio
import httpx # Using httpx for asynchronous HTTP requests
import re # For escaping markdown characters
from bs4 import BeautifulSoup, NavigableString # Import NavigableString
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
    it uses the text of the div_id itself. Handles <br> as newlines and cleans whitespace.
    """
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        target_div = soup.find("div", id=div_id)
        
        if not target_div:
            print(f"Div with ID '{div_id}' not found.")
            advisory_name = DIV_IDS.get(div_id, div_id)
            return f"The section for {advisory_name} could not be found on the page."

        first_immediate_child_div = target_div.find("div", recursive=False)
        content_source_div = first_immediate_child_div if first_immediate_child_div else target_div

        if not content_source_div: 
             # This case should ideally not be reached if target_div was found.
             print(f"Critical error: content_source_div is None for {div_id} despite target_div existing.")
             advisory_name = DIV_IDS.get(div_id, div_id)
             return f"Critical parsing error: No content source for {advisory_name}."

        text_parts = []
        # Iterate over direct children of the content_source_div
        for element in content_source_div.children: 
            if isinstance(element, NavigableString): # If it's a text node
                text_parts.append(str(element)) 
            elif element.name == 'br': # If it's a <br> tag
                text_parts.append('\n')
            # Note: If other inline tags (e.g., <b>, <i>) could appear directly
            # within content_source_div and contain text, this logic might need
            # to be expanded to call element.get_text() for those.
            # For the provided HTML structure, this handles text nodes and <br>s.

        # Join all parts; this text will have original spacing from HTML and \n for <br>
        raw_text_from_parts = "".join(text_parts)
        
        # Process the raw text to achieve the desired formatting:
        # 1. Split into lines based on the '\n' characters (these came from <br> tags or were in original text nodes)
        lines = raw_text_from_parts.split('\n')
        
        # 2. Strip leading/trailing whitespace from each individual line
        #    This ensures that lines like "    Some Text   " become "Some Text"
        stripped_lines = [line.strip() for line in lines]
        
        # 3. Join these stripped lines back with a single newline character.
        #    At this point, multiple <br> tags (e.g., <br><br>) would have resulted in
        #    multiple empty strings in stripped_lines, which then become multiple
        #    consecutive '\n' characters here.
        text_with_single_nl_and_stripped_lines = "\n".join(stripped_lines)
        
        # 4. Normalize multiple newlines to create paragraph breaks.
        #    Replace sequences of one or more newlines (which might have only whitespace
        #    between them if original lines were just spaces) with exactly two newlines.
        #    Finally, strip any leading/trailing newlines from the entire resulting block.
        final_text = re.sub(r'\n\s*\n+', '\n\n', text_with_single_nl_and_stripped_lines).strip()
        
        return final_text

    except Exception as e:
        print(f"Error parsing HTML content for div '{div_id}': {e}")
        advisory_name = DIV_IDS.get(div_id, div_id)
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
    header = escape_markdown_v2("🇵🇭 PAGASA Update: ") + escaped_advisory_type + escape_markdown_v2(" 🇵🇭")
    
    if not message_text: # Also check if message_text is only whitespace
        print(f"Message text for {advisory_type} is empty or whitespace. Skipping notification.")
        return
        
    escaped_message_text = escape_markdown_v2(message_text)
    full_message = f"{header}\n\n{escaped_message_text}"

    try:
        await bot.send_message(chat_id=channel_id, text=full_message, parse_mode=ParseMode.MARKDOWN_V2)
        print(f"Message sent to Telegram channel {channel_id} for {advisory_type}.")
    except TelegramError as e:
        print(f"Error sending MarkdownV2 message to Telegram for {advisory_type}: {e}")
        try:
            print(f"Attempting to send as plain text for {advisory_type} due to previous error.")
            plain_text_header = f"🇵🇭 PAGASA Update: {advisory_type} 🇵🇭"
            # Use the original, unescaped message_text for plain text fallback
            plain_text_message = f"{plain_text_header}\n\n{message_text}"
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
        error_message = "Failed to fetch the latest weather data from PAGASA website. Please check the site directly."
        # Ensure error messages are also sent if possible
        await send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, error_message, "Website Fetch Error")
        return

    previous_data = load_previous_data(DATA_FILE)
    current_data = {}
    notifications_to_send = []

    is_first_overall_run = not os.path.exists(DATA_FILE)

    for div_id, advisory_name in DIV_IDS.items():
        print(f"\nProcessing {advisory_name} (div_id: {div_id})...")
        content = parse_div_content(html_content, div_id)

        # Check if parsing returned an error message or valid (even if empty after strip) content
        if content is None or "could not be found on the page" in content or "Error parsing content" in content or "Critical parsing error" in content:
            print(f"  Problem parsing content for {advisory_name}: {content}")
            # If content is an error message from parsing, it will be used.
            # If parse_div_content returned None for some other reason (shouldn't with current logic), craft a message.
            if content is None: 
                content = f"Unable to retrieve or parse content for {advisory_name} at this time. Please check the PAGASA website directly."
        
        current_data[div_id] = content # Store even if it's an error message or empty
        previous_content = previous_data.get(div_id)

        print(f"  Previous content for {div_id}: {'Not found or empty' if not previous_content else 'Found'}")
        # print(f"  Current content for {div_id}: '{content[:100] if content else 'Empty'}'...")


        should_notify = False
        # Determine if a notification is needed
        if is_first_overall_run:
            print(f"  Overall first run detected. Will notify if new content is valid.")
            # Only notify on first run if content is not an error and not empty
            if content and not any(err_msg in content for err_msg in ["could not be found", "Error parsing", "Critical parsing"]):
                should_notify = True
        elif previous_content is None: # No previous data for this specific advisory
            print(f"  No previous data found for {advisory_name}. Will notify if new content is valid.")
            if content and not any(err_msg in content for err_msg in ["could not be found", "Error parsing", "Critical parsing"]):
                should_notify = True
        elif content != previous_content:
            print(f"  Content for {advisory_name} has changed.")
            # Notify on change, even if new content is an error message (to inform about the parsing issue)
            # or if new content is empty (to inform that advisory was removed/emptied)
            should_notify = True 
        else:
            print(f"  Content for {advisory_name} has not changed.")

        if should_notify and content: # Ensure content is not None (it could be an empty string if advisory is empty)
            print(f"  Queueing notification for {advisory_name}.")
            notifications_to_send.append({
                "token": TELEGRAM_BOT_TOKEN,
                "channel": TELEGRAM_CHANNEL_ID,
                "message": content, # This might be an error message, actual advisory, or empty string
                "type": advisory_name
            })
        elif should_notify and content is None: # Should not happen due to error message crafting above
             print(f"  Change detected for {advisory_name}, but new content is None. Not queueing.")


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
    # To run this test:
    # 1. Make sure DIV_IDS is defined if your parse_div_content uses it for error messages.
    # 2. Call the function directly.
    """
    # Local test setup:
    DIV_IDS_test = { "thunderstorms": "Thunderstorm Advisory/Watch" }
    def get_div_ids_for_test(): global DIV_IDS; DIV_IDS = DIV_IDS_test
    
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
    # get_div_ids_for_test() # To make DIV_IDS available to parse_div_content if it's out of main
    # print("--- Testing with user's example HTML ---")
    # result_flat = parse_div_content(test_html_thunderstorm_flat, "thunderstorms")
    # print(f"Result:\n'{result_flat}'")
    # 
    # expected_output = '''Thunderstorm Watch #NCR_PRSD
# Issued at: 10:00 PM, 05 June 2025
# 
# Thunderstorm is MORE LIKELY to develop over Greater Metro Manila Area(Metro Manila, Bulacan, Rizal, Laguna and Cavite) within 12 hours.
# 
# All are advised to continue monitoring for updates.'''.strip().replace("# ", "")
    # if result_flat == expected_output:
    #    print("\\nTest PASSED!")
    # else:
    #    print("\\nTest FAILED!")
    #    print(f"Expected:\n'{expected_output}'")

    """
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"An error occurred in the main execution: {e}")

