"""
Simple module for applying the mods as you need.
"""


from .user_client import apply as using_user_client_mod
from .recieve_audio import apply as using_recieve_audio_mod
from .voice_call import apply as using_voice_call_mod

def apply_all():
    """Applying all avaiable mods to discord.py"""
    using_user_client_mod()
    using_recieve_audio_mod()
    using_voice_call_mod()
