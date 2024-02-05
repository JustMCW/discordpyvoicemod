"""
Recieving audio data from connected voice channel
"""

import nacl
import struct
import time

import discord


class RawData:
    """Handles raw data from Discord so that it can be decrypted and decoded to be used.

    .. versionadded:: 2.0
    """

    def __init__(self, data, client):
        self.data = bytearray(data)
        self.client = client

        self.header = data[:12]
        self.data = self.data[12:]
        import struct
        unpacker = struct.Struct(">xxHII")
        self.sequence, self.timestamp, self.ssrc = unpacker.unpack_from(self.header)
        self.decrypted_data = getattr(self.client, f"_decrypt_{self.client.mode}")(
            self.header, self.data
        )
        self.decoded_data = None

        self.user_id = None

def unpack_audio(vc, data):
    """Takes an audio packet received from Discord and decodes it into pcm audio data.
    If there are no users talking in the channel, `None` will be returned.

    You must be connected to receive audio.

    .. versionadded:: 2.0

    Parameters
    ----------
    data: :class:`bytes`
        Bytes received by Discord via the UDP connection used for sending and receiving voice data.
    """
    if 200 <= data[1] <= 204:
        # RTCP received.
        # RTCP provides information about the connection
        # as opposed to actual audio data, so it's not
        # important at the moment.
        return

    data = RawData(data, vc)

    if data.decrypted_data == b"\xf8\xff\xfe":  # Frame of silence
        return
    return data.decrypted_data


def strip_header_ext(data):
    if data[0] == 0xBE and data[1] == 0xDE and len(data) > 4:
        _, length = struct.unpack_from(">HH", data)
        offset = 4 + length * 4
        data = data[offset:]
    return data

# listen to content of voice channel
class IOVoiceClient(discord.VoiceClient):

    def _decrypt_xsalsa20_poly1305_lite(self, header, data):
        box = nacl.secret.SecretBox(bytes(self.secret_key)) # type: ignore

        nonce = bytearray(24)
        nonce[:4] = data[-4:]
        data = data[:-4]

        return strip_header_ext(box.decrypt(bytes(data), bytes(nonce)))

    async def listen(self):
        import select
        self.socket.listen
        BUFFER_SIZE = 4096

        # UDP socket : 
        # Datagram socket (doesn't ) 
        # data = self.socket.recv(BUFFER_SIZE)
        def data_handler(data : bytes):
            print(len(data))
        
        while True:
            try:
                ready, _, err = select.select([self.socket],[], [self.socket], 0.01)
            except (OSError,ValueError):
                print("wait error")
                time.sleep(1)
                continue
                
            if not ready:
                if err:
                    print(err)
                continue
            
            # Collect the data
            try:
                data = self.socket.recv(4096)
            except (OSError):
                continue

            # Decryption & Handling
            data = unpack_audio(self,data)
            if data is None: 
                continue
            data_handler(data)


def apply():
    discord.VoiceClient._decrypt_xsalsa20_poly1305_lite = IOVoiceClient._decrypt_xsalsa20_poly1305_lite #type: ignore
    discord.VoiceClient.listen = IOVoiceClient.listen #type: ignore
