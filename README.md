# AI Cafe
_Why Wasn't AI Consulted?_


Slack bots that integrate AI into your workspace.
- Let people converse with Claude and co just by posting a message on Slack - reduce signup, billing friction to usage.
- People talk to AI in a community setting. They learn from each other how to use AI, what works, what doesn't.
- Conversations are visible to all channel members, no sharing friction of screenshots and links.
- Workspace owner (or whoever deploys the bot) pays for the API tokens.

Right now, only Claude is supported.

## Features

- Integration with Claude 3.5 Sonnet through the Anthropic API
- Thread-based conversations
- Support for file attachments including images, PDFs, and text files
- Configuration options per thread including temperature and system prompts
- Public/private thread modes
- Automatic handling of long responses via file uploads
- Message deletion cascade (downstream bot responses deleted when trigger message is deleted)
- Caching support for file attachments

## Installation

### Creating the Slack app

This is never going to be published on the Slack app store, so you should install it as a custom
app for your workspace. Instructions here work as of March 2025.
1. Go to https://api.slack.com/apps
2. Click "Create an App", "From a manifest", select the workspace you want to install the bot.
3. Paste the contents of the manifest.json in this repo to the "Create app from manifest" dialog.
    (On macOS, `cat manifest.json | pbcopy` will copy the contents to the clipboard)
4. Review summary and create your app.
5. In the app's Basic Information, generate an app-level token. Call it "cb-socket1", add scope "connections:write"
    You need to copy the generated token somewhere, it won't be seen again.
6. Upload logo.png in the app icon dialog.
7. Go to "OAuth & Permissions" listed under Features.
8. Install to your workspace.
9. Copy the "Bot user OAuth Token" somewhere, you'll need it soon.
10. Create a new channel, like "ai-cafe".
11. Add the Claude app to the channel (Edit channel settings, Integrations, Add apps)


### Running the bot
1. Set up the required environment variables. Put them in a `env.txt` and source it before running the bot.
    The app and bot tokens were saved in the previous step, copy them here.
    ```bash
    SLACK_BOT_TOKEN=xoxb-your-bot-token
    SLACK_APP_TOKEN=xapp-your-app-token
    ANTHROPIC_API_KEY=your-anthropic-key
    ANTHROPIC_SYSTEM_PROMPT=path/to/system/prompt.txt (optional)
    AICAFE_CACHE_DIR=/path/to/cache/directory (optional, defaults to /tmp/ai-cafe-cache)
    ```
2. Install the required dependencies:
    ```bash
    pip install slack-bolt anthropic diskcache requests
    ```
3. Run the bot:
    ```bash
    source env.sh
    python claude-slack-bot.py
    ```
## Basic Usage

Mention the bot to start a new thread:
@Claude What is the meaning of life?

If the bot is up and running, you should immediately see a :thinking_face: emoji below your message
and Claude will reply in a threaded response. Continue the conversation in the thread.

- Every thread is a separate, independent 1:1 conversation between Claude and the original person who started the thread (OP).
- You can attach images, PDFs and text files to messages.
- Claude's context in a thread is limited to the contents of the thread - it does not know anything about
  other conversations or other channels.
- Only the first message of the thread needs to mention @Claude, subsequent messages by OP are implicitly addressed to Claude.
- Messages in a thread posted by people other than the OP are ignored.
- Within a thread, OP can prefix a message with @aside to insert comments which will be ignored by Claude.
- Deletion of a message in a thread, results in the bot deletes all subsequent responses of its own. This helps
  recovery from unpromising lines of enquiry and resume the conversation along another tack.
- Long responses going beyond ~3000 characters are converted to Slack snippets to get around Slack limits.
- Editing messages will not trigger any immediate re-evaluation, but subsequent interactions will pick up the edit as part of the context.
- Top-level messages which don't start with a @Claude mention are normal Slack threads, ignored by the bot.

## Advanced
### Configuration Options
Add configuration options at the start of your message: @claude-bot {{ "temperature": 0.8, "system": "You are a helpful assistant" }} What is the meaning of life?

Options
- temperature: Controls response randomness (0.0-1.0)
- system: Override system prompt for the thread
- claude: Set to false to disable Claude API calls (for testing)

### Public/Private Threads
- Default: Only thread creator can interact with Claude
- Public mode: Add @public flag to allow anyone to interact. But Claude sees all human messages as having a single source - no names are 
@claude-bot @public What is the meaning of life?

### Special Commands
- @aside: Within a Claude thread, every message by the user starting the thread is considered an interaction with Claude, but if the user wants to make remarks to other humans within the thread, behind Claude's back, they should start the message with @aside. These messages are not seen by Claude.

## Known Issues
- Convert to and from Slack markdown
- Long message handling is hacky, Slack's API doubly so - randomly tags text files as binary thereby preventing them from being viewed in the UI.

## Future

- Moar bots - OpenAI
- Instruct/Chat fine tuning is typically structured as 1:1 conversations - how well would it respond to multi-user chats?
- Figure out a nice protocol for bots to hand off tasks to one another

## Credits
Claude wrote most of this, Copilot the rest, the remaining 90% was done by me.

System prompts are published by Anthropic. The one we're using is the 3.5 Sonnet "text and images" version.
Without the system prompt, Claude loses its mojo. So it's better to use it even though it's a shitload of tokens.

## License
MIT License
