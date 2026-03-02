import discord
from discord.ext import commands

# Define the bot intents
intents = discord.Intents.default()
intents.messages = True

# Create a bot instance
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
def on_ready():
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    print('------')

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Respond to a specific command without a command decorator
    if message.content.startswith('!hello'):
        await message.channel.send('Hello! I am your Discord bot.')

@bot.command()
async def ping(ctx):
    await ctx.send('Pong!')

# Run the bot with your token
TOKEN = 'YOUR_TOKEN_HERE'
bot.run(TOKEN)