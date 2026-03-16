# ModMail Bot

A full-featured, personalized modmail and server-management Discord bot built with **Python (discord.py v2)** and **MongoDB (Motor async driver)**.

---

## Features

| Module | Highlights |
|---|---|
| **ModMail** | DM-to-channel threads, staff reply, anonymous reply, internal notes, block/unblock, auto-close, thread history, transcripts |
| **Tickets** | Button/slash-command ticket panel, priority tags, claim, add/remove users, transcript on close |
| **Moderation** | Ban, unban, kick, mute/unmute (Discord timeout), warn, softban, purge, lock/unlock, slowmode, case tracking, mod-log channel |
| **AutoMod** | Anti-spam, anti-mention, anti-links, anti-invites, bad-words filter, anti-caps, anti-zalgo — each with configurable action (delete/warn/mute/kick/ban) |
| **Welcome** | Customisable welcome & farewell embeds with template variables, auto-join role, per-channel config |
| **Roles** | Reaction roles, self-assignable roles, button-based role menu, staff give/take role |
| **Snippets** | Pre-written reply templates for modmail, shorthand `!s` / `!sa` commands, usage tracking |
| **Admin** | Custom help, hot-reload, prefix change, bot info, status, log-channel override, invite link |

---

## Requirements

- Python **3.11+**
- A [Discord bot application](https://discord.com/developers/applications) with **Message Content Intent**, **Server Members Intent**, and **Presence Intent** enabled
- A running **MongoDB** instance (local or [MongoDB Atlas](https://www.mongodb.com/atlas))

---

## Setup

### 1. Clone / place the files

```
modmail_bot/
├── bot.py
├── config.py
├── requirements.txt
├── .env.example
├── cogs/
│   ├── admin.py
│   ├── automod.py
│   ├── moderation.py
│   ├── modmail.py
│   ├── roles.py
│   ├── snippets.py
│   ├── tickets.py
│   └── welcome.py
└── utils/
    ├── db.py
    └── helpers.py
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in **all required fields**:

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Your Discord bot token |
| `GUILD_ID` | Your server's ID (right-click server → Copy ID) |
| `MONGO_URI` | MongoDB connection string |
| `MODMAIL_CATEGORY_ID` | Category where modmail thread channels are created |
| `TICKET_CATEGORY_ID` | Category where ticket channels are created |
| `MOD_LOG_CHANNEL_ID` | Channel for moderation action logs |
| `MODMAIL_LOG_CHANNEL_ID` | Channel for closed modmail transcripts |
| `WELCOME_CHANNEL_ID` | Channel for welcome messages |
| `FAREWELL_CHANNEL_ID` | Channel for farewell messages |
| `STAFF_ROLE_IDS` | Comma-separated role IDs allowed to use staff commands |
| `PREFIX` | Command prefix (default `!`) |
| `ACCENT_COLOR` | Embed accent color in hex (default `5865F2`) |

### 4. Enable Discord intents

In the [Developer Portal](https://discord.com/developers/applications), enable:
- **Message Content Intent**
- **Server Members Intent**
- **Presence Intent**

### 5. Run the bot

```bash
python bot.py
```

---

## Discord Permissions Needed

When inviting the bot, grant it these permissions:

- Read Messages / View Channels
- Send Messages
- Manage Messages
- Manage Channels
- Manage Roles
- Ban Members
- Kick Members
- Moderate Members (Timeout)
- Add Reactions
- Embed Links
- Attach Files
- Read Message History

---

## Command Reference

> Run `!help` in your server for a full, categorised command list.
> Run `!help <command>` for details on a specific command.

### ModMail (staff only — inside a modmail channel)

| Command | Description |
|---|---|
| `!reply <text>` / `!r` | Reply to the user |
| `!areply <text>` / `!ar` | Reply anonymously (user sees "Staff Reply") |
| `!note <text>` / `!n` | Internal note (not sent to user) |
| `!close [reason]` | Close the thread |
| `!closeafter <minutes> [reason]` | Auto-close after N minutes |
| `!block @user [reason]` | Block user from sending modmail |
| `!unblock @user` | Unblock a user |
| `!contact @user [message]` | Open a thread with a user proactively |
| `!threads @user` | View thread history for a user |

### Tickets

| Command | Description |
|---|---|
| `!ticketpanel` | Post the interactive ticket panel |
| `/ticket` | Open a ticket via slash command |
| `!tclose [reason]` | Close the current ticket |
| `!tclaim` / `!tunclaim` | Claim/unclaim a ticket |
| `!tadd @member` | Add a user to the ticket |
| `!tremove @member` | Remove a user from the ticket |
| `!tpriority <low/medium/high/urgent>` | Set ticket priority |

### Moderation

| Command | Description |
|---|---|
| `!ban @member [days] [reason]` | Ban a member |
| `!unban <user_id> [reason]` | Unban by ID |
| `!kick @member [reason]` | Kick a member |
| `!mute @member <duration> [reason]` | Timeout (10m, 2h, 1d…) |
| `!unmute @member [reason]` | Remove timeout |
| `!warn @member <reason>` | Issue a warning |
| `!warnings @member` | View warnings |
| `!clearwarns @member` | Clear all warnings |
| `!softban @member [reason]` | Ban+unban to delete messages |
| `!purge <amount> [@member]` | Delete messages |
| `!lock [#channel] [reason]` | Lock channel |
| `!unlock [#channel]` | Unlock channel |
| `!slowmode <seconds>` | Set slowmode |
| `!case <number>` | Look up a case |
| `!modlogs @member` | Full mod-log for user |
| `!userinfo [@member]` | User info card |
| `!serverinfo` | Server info card |
| `!roleinfo @role` | Role info card |

### AutoMod

```
!automod status
!automod enable <rule>
!automod disable <rule>
!automod action <rule> <delete|warn|mute|kick|ban>
!automod badword add <word>
!automod badword remove <word>
!automod whitelist add <domain>
!automod whitelist remove <domain>
!automod spamthreshold <messages> <seconds>
!automod ignorechannel #channel
!automod ignorerole @role
```

Available rules: `anti_spam`, `anti_mention`, `anti_links`, `anti_invites`, `bad_words`, `anti_caps`, `anti_zalgo`

### Welcome / Farewell

```
!setwelcome <template>          Template vars: {user} {username} {server} {membercount} {id}
!setfarewell <template>
!setwelcomechannel [#channel]
!setfarewellchannel [#channel]
!togglewelcome
!togglefarewell
!setwelcomerole [@role]
!testwelcome
!testfarewell
```

### Roles

```
!rrsetup #channel <message_id> <emoji> @role
!rrremove <message_id> <emoji>
!rrlist
!selfrole add @role
!selfrole remove @role
!selfrole list
!iam @role
!iamnot @role
!rolemenu [title] [description]
!giverole @member @role
!takerole @member @role
```

### Snippets

```
!snippet add <name> <content>
!snippet edit <name> <content>
!snippet delete <name>
!snippet list
!snippet info <name>
!snippet use <name>
!snippet anon <name>
!s <name>                  (shorthand use)
!sa <name>                 (shorthand anonymous)
```

---

## MongoDB Collections

| Collection | Purpose |
|---|---|
| `modmail_threads` | Modmail thread records + message history |
| `tickets` | Support ticket records |
| `mod_logs` | Moderation action cases |
| `snippets` | Reply templates |
| `guild_config` | Per-guild settings (prefix, channels, self-roles, etc.) |
| `automod_config` | Per-guild automod rules and configuration |
| `reaction_roles` | Reaction-role mappings |

---

## Project Structure

```
bot.py              Entry point — bot class, extension loading, startup
config.py           Environment variable loading
utils/
  db.py             Motor MongoDB client + init
  helpers.py        Shared embed builders, permission checks
cogs/
  modmail.py        Core modmail DM-to-channel system
  tickets.py        Channel-based ticket system
  moderation.py     Moderation commands + case logging
  automod.py        Auto-moderation rules engine
  welcome.py        Welcome / farewell messages
  roles.py          Reaction roles + self-roles
  snippets.py       Pre-written modmail reply templates
  admin.py          Help, hot-reload, bot management
```

---

## License

MIT — free to use, modify, and distribute.
