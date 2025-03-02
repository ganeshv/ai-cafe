import os
import logging
import re
import base64
import requests
from io import BytesIO
from datetime import datetime, timedelta
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from anthropic import Anthropic
import json
import copy
import ast
from diskcache import Cache
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
cache = Cache(os.environ.get("AICAFE_CACHE_DIR", "/tmp/ai-cafe-cache"))
LONGRESPONSE = "Response was too long for Slack. See attached llm_response.txt"

class AttachmentHandler:
    """Handles file attachments in messages"""
    
    @staticmethod
    def download_and_encode_file(client, file_info):
        """Download file and convert to base64 if needed"""
        try:
            # Get file data
            headers = {'Authorization': f'Bearer {client.token}'}
            #logger.info(f"Processing attachment: {file_info}")
            mimetype = file_info["mimetype"]
            is_image = mimetype.startswith("image/")
            filename = file_info.get("name", "unnamed")
            if is_image:
                url = file_info.get("thumb_1024") or file_info["url_private"]
            else:
                url = file_info["url_private"]

            if url in cache:
                logger.info(f"Using cached file: {url}")
                content = cache[url]
            else:
                logger.info(f"Downloading file: {url}")
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                content = response.content
                cache[url] = content
            
            
            if is_image or mimetype == "application/pdf":
                # Convert image to base64
                encoded = base64.b64encode(content).decode('utf-8')
                return {
                    "type": "image" if is_image else "document",
                    "source": {
                        "type": "base64",
                        "media_type": file_info["mimetype"],
                        "data": encoded
                    }
                }
            elif filename == "llm_response.txt":
                return {
                    "type": "text",
                    "text": content.decode('utf-8', errors='replace')
                }
            elif mimetype.startswith("text/"):
                # Plain text files
                text_content = content.decode('utf-8', errors='replace')
                return {
                    "type": "text",
                    "text": f"[{file_info['pretty_type']} content from {filename}]:\n\n{text_content}"
                }                
            else:
                # Handle other file types as text
                return {
                    "type": "text",
                    "text": f"[Attached file: {file_info.get('name', 'unnamed')}]\n"
                }                
        except Exception as e:
            logger.error(f"Error processing file {file_info.get('name')}: {e}")
            return None

    @staticmethod
    def process_attachments(client, files):
        """Process file attachments into format suitable for Claude API"""
        if not files:
            return []
        
        file_contents = []
        for file in files:
            try:
                # Get detailed file info
                response = client.files_info(file=file["id"])
                file_info = response["file"]
                
                processed = AttachmentHandler.download_and_encode_file(client, file_info)
                if processed:
                    file_contents.append(processed)
                
            except Exception as e:
                logger.error(f"Error processing attachment: {e}")
                
        return file_contents

class ClaudeBot:
    def __init__(self, slack_token, app_token, anthropic_key, system_prompt):
        """Initialize the Claude bot with necessary tokens and clients"""
        self.app = App(token=slack_token)
        system_prompt = system_prompt.replace("{{currentDateTime}}", datetime.now().strftime("%A, %B %d, %Y"))
        self.system_prompt = [
            {
                "type": "text",
                "text": system_prompt
            }
        ] if system_prompt else ""

        self.anthropic = Anthropic(api_key=anthropic_key,
            default_headers={"anthropic-version": "2023-06-01", "anthropic-beta": "pdfs-2024-09-25,prompt-caching-2024-07-31"})
        
        # Get bot's own ID during initialization
        try:
            auth_response = self.app.client.auth_test()
            self.bot_user_id = auth_response["user_id"]
            logger.info(f"Bot initialized with ID: {self.bot_user_id}")
        except Exception as e:
            logger.error(f"Failed to get bot ID: {e}")
            raise

        # Register event listeners
        self.app.event("message")(self.handle_message)
        
        # Initialize SocketModeHandler
        self.handler = SocketModeHandler(self.app, app_token)
        self.last_api_call = datetime.now() - timedelta(minutes=10)

    def is_bot_mention(self, text):
        """Check if message starts with @claude-bot"""
        return text.strip().startswith(f"<@{self.bot_user_id}>")
                                       
    def is_aside(self, text):
        """Check if message starts with @aside"""
        return text.strip().lower().startswith("@aside")
    
    def log_event(self, event):
        """Log event details, but don't overwhelm. Event type, user, ts, type, subtype"""

        logger.info(f"Event: subtype: {event.get('subtype')}, user: {event.get('user', event.get('message', {}).get('user'))}, ts: {event.get('ts')}")
    
        
    def handle_message(self, event, say, client):
        """Handle new messages in the #ai-claude channel"""
        try:
            self.log_event(event)

            subtype = event.get("subtype")
            if subtype == "message_deleted" or \
                (subtype == "message_changed" and event.get("message", {}).get("subtype") == "tombstone"):
                return self.handle_message_deleted(event, say, client)

            if subtype == "message_changed":
                # compare with previous message to see if any real change
                if event.get("previous_message", {}).get("text", 0) == event.get("message", {}).get("text"):
                    return
            
            msg = event if subtype != "message_changed" else event.get("message", {})
            
            # We thread our responses in all cases, whether this is a new thread or a reply
            thread_ts = msg.get("thread_ts", msg.get("ts"))

            is_thread_message = "thread_ts" in msg
            text = msg.get("text", "")
            logger.info(f"Got message: {text}")
            # Handle new thread creation
            if not is_thread_message:
                text, config = self.parse_config_block(text)
                if not config.get("is_bot_mention"):
                    return  # Ignore non-bot threads
                thread_initiator = msg["user"]
                
                # Process attachments only for new thread
                initial_content = [{"type": "text", "text": text}]
                if msg.get("files"):
                    attachments = AttachmentHandler.process_attachments(
                        client,
                        msg.get("files", [])
                    )
                    initial_content.extend(attachments)
                
                formatted_messages = [{"role": "user", "content": initial_content}]
            else:
                # Check if message is from thread initiator and had mentioned the bot in the first message
                thread = client.conversations_replies(channel=event["channel"], ts=thread_ts)
                first_msg = thread["messages"][0]
                first_msg_txt, config = self.parse_config_block(first_msg.get("text", ""))

                if not config.get("is_bot_mention"):
                    return

                thread_initiator = first_msg.get("user")
                if not config.get("is_public") and msg["user"] != thread_initiator:
                    return
                
                first_msg["text"] = first_msg_txt
                formatted_messages = self.format_thread(thread, client, config)
                # if the last message was from the assistant, don't go further, we probably got a message_changed event
                if formatted_messages[-1]["role"] == "assistant":
                    return
            
            # Call Claude API
            msgs, sysprompt = self.apply_cache_headers(formatted_messages)
            if "system" in config:
                sysprompt = config["system"]
            self.dump_claude_request(msgs)
            if config.get("claude") == False:
                logger.info("skipping Claude")
                return
            
            self.show_typing(client, event["channel"], msg["ts"])

            response = self.anthropic.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=8192,
                system=sysprompt,
                messages=msgs,
                temperature=config.get("temperature", 0.7)
            )
            self.last_api_call = datetime.now()

            self.remove_typing(client, event["channel"], msg["ts"])

            # Process response text and convert artifacts to blocks
            # We don't do artifacts right now
            response_text = response.content[0].text

            cleaned_text, file = handle_large_response(response_text, client, event["channel"], thread_ts)
            logger.info(f"usage: {response.usage}")
            logger.info(f"response: {cleaned_text[:200]}")
            if file:
                return # we uploaded a file, no need to post text
            blocks = convert_to_blocks(cleaned_text)
            # Post response
            say(
                text=cleaned_text[:3000],  # Fallback text
                blocks=blocks,
                thread_ts=thread_ts,
                #files=files
            )
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            filename, lineno, func, text = tb[-1]
            logger.error(f"Exception in file {filename}, line {lineno}, in {func}")
            logger.error(f"  Code: {text}")
            say(
                text=f"I encountered an error processing your message: {str(e)}",
                thread_ts=thread_ts
            )

    def parse_config_block(self, text):
        """
        Parse a message containing a configuration block marked by double braces.
        Returns the cleaned message and extracted config dict.
        
        Example:
            >>> msg = '{{ "system": "hello", "keepalive": 5 }}\nActual message here'
            >>> parse_config_block(msg)
            ('Actual message here', {'system': 'hello', 'keepalive': 5})
        """

        config = {}        
        # Define the possible prefix patterns
        bot_mention = f"<@{self.bot_user_id}>"
        public_flag = "@public"
        
        # Check for and remove prefixes
        while True:
            text = text.strip()
            if text.startswith(bot_mention):
                config["is_bot_mention"] = True
                text = text[len(bot_mention):].strip()
            elif text.startswith(public_flag):
                config["is_public"] = True
                text = text[len(public_flag):].strip()
            else:
                break
        
        # Match text within double braces at start of string
        pattern = r'^\s*{{(.+?)}}\s*(.*)'
        match = re.match(pattern, text, re.DOTALL)
        if not match:
            return text.strip(), config

        try:
            config_str, remaining_text = match.groups()
            config2 = ast.literal_eval('{' + config_str + '}')
            
            if not isinstance(config, dict):
                logger.error(f"Config block {config} must evaluate to a dictionary")
                config2 = {}
            config.update(config2)
            return remaining_text.strip(), config
            
        except (SyntaxError, ValueError) as e:
            logger.error(f"Invalid config block: {e}")
            return text.strip(), config
        
    def format_thread(self, thread, client, config):
        """Format thread messages for Claude API"""
        formatted_messages = []
        first_msg = thread["messages"][0]
        initiator_id = first_msg.get("user")
        for msg in thread["messages"]:
            # Skip if not from thread initiator and not from our bot
            user = msg.get("user")
            if user != self.bot_user_id:
                if user != initiator_id and not config.get("is_public"):
                    continue
            
            # Skip @aside messages
            if self.is_aside(msg.get("text", "")):
                continue
            
            formatted_content = []
            
            # Handle bot messages with blocks (artifacts)
            if user == self.bot_user_id and msg.get("blocks"):
                blocktxt = reconstruct_from_slackmsg(msg["blocks"])
                if blocktxt:
                    msg["text"] = blocktxt
            
            # Add main message text
            if msg.get("text") and not msg["text"].startswith(LONGRESPONSE):
                formatted_content.append({
                    "type": "text",
                    "text": msg["text"]
                })
            
            # Process attachments
            if msg.get("files"):
                file_contents = AttachmentHandler.process_attachments(client, msg["files"])
                formatted_content.extend(file_contents)
            
            if len(formatted_content) == 0:
                #print("No content", json.dumps(msg, indent=4))
                continue

            formatted_messages.append({
                "role": "assistant" if user == self.bot_user_id else "user",
                "content": formatted_content
            })
        return formatted_messages
    
    def handle_message_deleted(self, event, say, client):
        """Handle message deletions and cascade delete bot responses"""
        try:
            # Get thread messages
            thread_ts = event.get("previous_message", {}).get("thread_ts")
            logger.info(f"GOT DELETE: thread: {thread_ts} previous_msg: {event.get('previous_message', {}).get('ts')}")
            if not thread_ts:
                return
            if event.get("previous_message", {}).get("subtype") == "tombstone":
                return
            
            messages = client.conversations_replies(
                channel=event["channel"],
                ts=thread_ts
            )["messages"]

            # Find deleted message index
            deleted_ts = float(event["previous_message"]["ts"])
            after_deleted_idx = next(
                (i for i, msg in enumerate(messages) if float(msg["ts"]) >= deleted_ts),
                -1
            )

            if after_deleted_idx == -1:
                return
            
            # Delete all subsequent bot messages (only our bot's messages)
            for msg in messages[after_deleted_idx:]:
                if msg.get("user") == self.bot_user_id:
                    try:
                        client.chat_delete(
                            channel=event["channel"],
                            ts=msg["ts"]
                        )
                    except Exception as e:
                        logger.error(f"Error deleting message: {e}")
                        
        except Exception as e:
            logger.error(f"Error handling message deletion: {e}")

    def apply_cache_headers(self, messages):
        """Apply cache headers to system prompt and first message with attachment if caching is enabled"""

        # Add cache control to system prompt if one exists
        system_prompt = copy.deepcopy(self.system_prompt)
        if system_prompt and isinstance(system_prompt, list):
            system_prompt[-1]["cache_control"] = {"type": "ephemeral"}
        
        # Find last message with an attachment and add cache to its last content element
        for message in reversed(messages):
            if message["role"] == "user" and len(message["content"]) > 1:
                message["content"][-1]["cache_control"] = {"type": "ephemeral"}
                break
        
        return messages, system_prompt
    
    def dump_claude_request(self, messages):
        """Dump Claude request for debugging"""
        # print request, except long messages like attachments which should be trimmed to 1k characters
        # images and pdfs should be trimmed completely
        messages_copy = copy.deepcopy(messages)
        for message in messages_copy:
            for content in message.get("content", []):
                if content.get("type") == "image" or content.get("type") == "document":
                    content["source"]["data"] = ""
                elif content.get("type") == "text":
                    content["text"] = content["text"][:200]
        logger.info(f"Claude Request: {json.dumps(messages_copy, indent=4)}")

    def show_typing(self, client, channel, ts):
        """Show typing indicator in the channel"""
        try:
            client.reactions_add(
                name="thinking_face",
                channel=channel,
                timestamp=ts
            )
        except Exception as e:
            logger.error(f"Error showing typing indicator: {e}")

    def remove_typing(self, client, channel, ts):
        """Remove typing indicator from the channel"""
        try:
            client.reactions_remove(
                name="thinking_face",
                channel=channel,
                timestamp=ts
            )
        except Exception as e:
            logger.error(f"Error removing typing indicator: {e}")

    def start(self):
        """Start the bot"""
        logger.info("Starting Claude Slack bot...")
        self.handler.start()

def upload_snippet(client, channel, content, filename, thread_ts):
    """Upload content as a snippet file"""
    try:
        response = client.files_upload_v2(
            channel=channel,
            content=content,
            title=filename,
            filename=filename,
            thread_ts=thread_ts,
            initial_comment=LONGRESPONSE
        )
        return response["file"]
    except Exception as e:
        logger.error(f"Error uploading snippet: {e}")
        return None
    
def handle_large_response(text, client, channel, thread_ts):
    """Handle responses exceeding Slack's 3000 character limit"""
    if len(text) <= 3000:
        return text, None

    # Uploading entire response as snippet
    logger.info(f"Uploading full snippet: {text[:200]}")
    file = upload_snippet(client, channel, text, "llm_response.txt", thread_ts)
    if file:
        return LONGRESPONSE, file

    logger.error(f"Upload snippet failed")

    return text[:2950] + "\n... [Response truncated]", None

def reconstruct_from_slackmsg(blocks):
    """Reconstruct bot message from Slack blocks for context preservation"""
    messages = []
    logger.info(f"reconstructing from slack blocks {len(blocks)}")
    for block in blocks:
        if block["type"] == "section":
            text = block["text"]["text"]
            if text.startswith(LONGRESPONSE):
                continue
            logger.info(f"section block: {text[:200]}")
            messages.append(text)
                
    return "\n".join(messages)

def convert_to_blocks(text):
    blocks = []
    if text:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text}
        })
    return blocks

def main():
    """Main function to start the Slack bot"""
    try:
        # read system prompt if it exists
        system_prompt = ""
        try:
            with open(os.environ["ANTHROPIC_SYSTEM_PROMPT"], "r") as f:
                system_prompt = f.read()
        except:
            pass
        bot = ClaudeBot(
            slack_token=os.environ["SLACK_BOT_TOKEN"],
            app_token=os.environ["SLACK_APP_TOKEN"],
            anthropic_key=os.environ["ANTHROPIC_API_KEY"],
            system_prompt=system_prompt)
        bot.start()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")

if __name__ == "__main__":
    main()
