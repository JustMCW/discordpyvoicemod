# Discord.py voice mod
An extension of [discord.py](https://github.com/Rapptz/discord.py)


### Adds the following features :
- logging in as user (TOS I know)
- Connecting to DM voice channels
- Recieving audio from voice channels
- Added **call_create**, **call_update**, **call_delete** events

## Usage
Install discord.py, download this directory. And import the modloader module, run the apply_all(), or the individual mods as required.

## Example
A simple script that joins a call whenever someone else calls them

```python
import discord
import discordpyvoicemod.modloader

discordpyvoicemod.modloader.apply_all()

@client.event
async def on_ready():
    print(f"Logged in as {client.user.name}")

@client.event
async def on_call_create(data):
    channel_id = int(data["channel_id"])
    DMChannel = await client.fetch_channel(channel_id)    
    await DMChannel.connect()

client.run("TOKEN", log_level=logging.INFO)
```
