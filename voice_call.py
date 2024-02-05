"""
Enable joining of voice call in DMs, only works for non-bot users of course.
"""

import discord
import asyncio
import logging

from typing import *
from discord.state import logging_coroutine
import discord.utils as utils
from discord.utils import MISSING
import socket

_log = logging.getLogger(__name__)


if TYPE_CHECKING:
    from discord.types.gateway import VoiceStateUpdateEvent, VoiceServerUpdateEvent


def get_vc_id(data : Any) -> int:
    """Helper function for obtaining a unique id for different voice clients, BOTH VOICE CALL AND GUILD VOICE CHANNELS
    this function is mainly for the _get_voice_client fuction, which identifies if the bot (?) is in a certain vc, by check a hashmap of joined vc, where the key is determined by this."""
    return int(data["guild_id"] if data["guild_id"] is not None else data["channel_id"])


class ModdedConnectionState(discord.state.ConnectionState):
    """We wanna also listen to voice call stuff."""
    def parse_voice_server_update(self, data: 'VoiceServerUpdateEvent') -> None:
        key_id = get_vc_id(data)

        vc = self._get_voice_client(key_id)
        if vc is not None:
            coro = vc.on_voice_server_update(data)
            asyncio.create_task(logging_coroutine(coro, info='Voice Protocol voice server update handler'))

    def parse_voice_state_update(self, data: 'VoiceStateUpdateEvent') -> None:
        guild = self._get_guild(utils._get_as_snowflake(data, 'guild_id'))
        channel_id = utils._get_as_snowflake(data, 'channel_id')
        flags = self.member_cache_flags
        # self.user is *always* cached when this is called
        self_id = self.user.id  # type: ignore

        if guild is not None:
            if int(data['user_id']) == self_id:
                voice = self._get_voice_client(guild.id)
                if voice is not None:
                    coro = voice.on_voice_state_update(data)
                    asyncio.create_task(logging_coroutine(coro, info='Voice Protocol voice state update handler'))

            member, before, after = guild._update_voice_state(data, channel_id)  # type: ignore
            if member is not None:
                if flags.voice:
                    if channel_id is None and flags._voice_only and member.id != self_id:
                        # Only remove from cache if we only have the voice flag enabled
                        guild._remove_member(member)
                    elif channel_id is not None:
                        guild._add_member(member)

                self.dispatch('voice_state_update', member, before, after)
            else:
                _log.debug('VOICE_STATE_UPDATE referencing an unknown member ID: %s. Discarding.', data['user_id'])
        # Here, add our response to non-guild vc
        else:
            if int(data['user_id']) == self_id:
                voice = self._get_voice_client(channel_id)
                if voice is not None:
                    coro = voice.on_voice_state_update(data)
                    asyncio.create_task(logging_coroutine(coro, info='Voice Protocol voice state update handler'))
            
            # trigger our event ( with a different name )
            self.dispatch('voice_call_state_update', data)

    # Calling !!!!!!
    def parse_call_create(self, data) -> None:
        self.dispatch('call_create', data)

    def parse_call_update(self, data) -> None:
        self.dispatch('call_update', data)

    def parse_call_delete(self, data) -> None:
        self.dispatch('call_delete', data)




class DMVoiceClient(discord.VoiceClient):
    """
    Instead of sending though guild ws, we use client's ws
    basically changing all guild id to none.
    """
    channel : 'DMVoiceChannel'

    async def on_voice_server_update(self, data: dict) -> None:
        """Whole thing just to set the server id correctly"""
        # await super().on_voice_server_update(data)
        # self.server_id = get_vc_id(data) 
        # return

        if self._voice_server_complete.is_set():
            _log.warning('Ignoring extraneous voice server update.')
            return

        self.token = data['token']
        
        # server id lol
        # it has to be changed to channel id if it is dm call since
        # the API protocol take "server_id" to be that when sending IDENTIFY 
        # it's not used in any other places so its fine ig.
        self.server_id = get_vc_id(data) 

        endpoint = data.get('endpoint')

        if endpoint is None or self.token is None:
            _log.warning(
                'Awaiting endpoint... This requires waiting. '
                'If timeout occurred considering raising the timeout and reconnecting.'
            )
            return

        self.endpoint, _, _ = endpoint.rpartition(':')
        if self.endpoint.startswith('wss://'):
            # Just in case, strip it off since we're going to add it later
            self.endpoint: str = self.endpoint[6:]

        # This gets set later
        self.endpoint_ip = MISSING

        self.socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)

        if not self._handshaking:
            # If we're not handshaking then we need to terminate our previous connection in the websocket
            await self.ws.close(4000)
            return

        self._voice_server_complete.set()


    async def change_voice_state(
        self, *, channel :  Optional['DMVoiceChannel'], self_mute: bool = False, self_deaf: bool = False
    ) -> None:
        """Instead of calling change_voice_state from guild's ws. We do it with the client's"""

        ws =  self.client.ws
        payload = {
            'op': ws.VOICE_STATE,
            'd': {
                'guild_id': None,
                'channel_id': self.channel.id if channel else None,
                'self_mute': self_mute,
                'self_deaf': self_deaf,
            },
        }
        _log.debug('Updating our DM voice state to %s.', payload)
        await ws.send_as_json(payload)
    
    async def voice_connect(self, self_deaf: bool = False, self_mute: bool = False) -> None:
        await self.change_voice_state(channel=self.channel, self_deaf=self_deaf, self_mute=self_mute)

    async def voice_disconnect(self) -> None:
        await self.change_voice_state(channel=None)

    

class DMVoiceChannel(discord.channel.DMChannel, discord.abc.Connectable):
    """Simple changes to use the DMCallProtocol instead when connecting, And also a different voice_client_key (guild_id -> channel_id)"""

    def _get_voice_client_key(self) -> Tuple[int, str]:
        # the second key was never actually used anywhere
        # the first key is just for identifying whether u are already in it
        # so it just has to be unique, thats it
        return self.id, "dm_channel?"
    
    # i didn't find any code calling this function
    def _get_voice_state_pair(self) -> Tuple[int, int]:
        raise RuntimeError("Wait, you were ever called !???")
        return  self.id, self.id
    
    async def connect(self, *, timeout: float = 60, reconnect: bool = True, cls = None, self_deaf: bool = False, self_mute: bool = False):
        vc = await super().connect(timeout=timeout, reconnect=reconnect, cls=DMVoiceClient, self_deaf=self_deaf, self_mute=self_mute)
        self.vc = vc
        return vc
    

class GroupChatVoiceChannel(discord.channel.GroupChannel, discord.abc.Connectable):
    """Simple changes to use the DMCallProtocol instead when connecting, And also a different voice_client_key (guild_id -> channel_id)"""

    def _get_voice_client_key(self) -> Tuple[int, str]:
        # the second key was never actually used anywhere
        # the first key is just for identifying whether u are already in it
        # so it just has to be unique, thats it
        return self.id, "dm_group_channel?"
    
    # i didn't find any code calling this function
    def _get_voice_state_pair(self) -> Tuple[int, int]:
        raise RuntimeError("Wait, you were ever called !???")
        return  self.id, self.id
    
    async def connect(self, *, timeout: float = 60, reconnect: bool = True, cls = None, self_deaf: bool = False, self_mute: bool = False):
        vc = await super().connect(timeout=timeout, reconnect=reconnect, cls=DMVoiceClient, self_deaf=self_deaf, self_mute=self_mute)
        self.vc = vc
        return vc
    

def apply():
    discord.channel.DMChannel = DMVoiceChannel
    discord.channel.GroupChannel = GroupChatVoiceChannel

    discord.state.ConnectionState.parse_voice_server_update = ModdedConnectionState.parse_voice_server_update  # type: ignore
    discord.state.ConnectionState.parse_voice_state_update = ModdedConnectionState.parse_voice_state_update # type: ignore
    discord.state.ConnectionState.parse_call_create = ModdedConnectionState.parse_call_create # type: ignore
    discord.state.ConnectionState.parse_call_update = ModdedConnectionState.parse_call_update # type: ignore
    discord.state.ConnectionState.parse_call_delete = ModdedConnectionState.parse_call_delete # type: ignore
