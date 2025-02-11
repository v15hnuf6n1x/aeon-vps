import contextlib
from time import time
from asyncio import Lock
from logging import ERROR, getLogger
from secrets import token_hex

from bot import (
    LOGGER,
    IS_PREMIUM_USER,
    bot,
    user,
    download_dict,
    non_queued_dl,
    queue_dict_lock,
    download_dict_lock,
)
from bot.helper.ext_utils.task_manager import (
    is_queued,
    limit_checker,
    stop_duplicate_check,
)
from bot.helper.telegram_helper.message_utils import (
    delete_links,
    send_message,
    sendStatusMessage,
)
from bot.helper.mirror_leech_utils.status_utils.queue_status import QueueStatus
from bot.helper.mirror_leech_utils.status_utils.telegram_status import TelegramStatus

global_lock = Lock()
GLOBAL_GID = set()
getLogger("pyrogram").setLevel(ERROR)


class TelegramDownloadHelper:
    def __init__(self, listener):
        self.name = ""
        self.__processed_bytes = 0
        self.__start_time = time()
        self.__listener = listener
        self.__id = ""
        self.__is_cancelled = False

    @property
    def speed(self):
        return self.__processed_bytes / (time() - self.__start_time)

    @property
    def processed_bytes(self):
        return self.__processed_bytes

    async def __on_download_start(self, name, size, file_id, from_queue):
        async with global_lock:
            GLOBAL_GID.add(file_id)
        self.name = name
        self.__id = file_id
        gid = token_hex(4)
        async with download_dict_lock:
            download_dict[self.__listener.uid] = TelegramStatus(
                self, size, self.__listener.message, gid, "dl"
            )
        async with queue_dict_lock:
            non_queued_dl.add(self.__listener.uid)
        if not from_queue:
            await self.__listener.on_download_start()
            await sendStatusMessage(self.__listener.message)
            LOGGER.info(f"Download from Telegram: {name}")
        else:
            LOGGER.info(f"Start Queued Download from Telegram: {name}")

    async def __onDownloadProgress(self, current, _):
        if self.__is_cancelled:
            if IS_PREMIUM_USER:
                user.stop_transmission()
            else:
                bot.stop_transmission()
        self.__processed_bytes = current

    async def __on_download_error(self, error):
        async with global_lock:
            with contextlib.suppress(Exception):
                GLOBAL_GID.remove(self.__id)
        await self.__listener.onDownloadError(error)

    async def __on_download_complete(self):
        await self.__listener.on_download_complete()
        async with global_lock:
            GLOBAL_GID.remove(self.__id)

    async def __download(self, message, path):
        try:
            download = await message.download(
                file_name=path, progress=self.__onDownloadProgress
            )
            if self.__is_cancelled:
                await self.__on_download_error("Cancelled by user!")
                return
        except Exception as e:
            LOGGER.error(str(e))
            await self.__on_download_error(str(e))
            return
        if download is not None:
            await self.__on_download_complete()
        elif not self.__is_cancelled:
            await self.__on_download_error("Internal error occurred")

    async def add_download(self, message, path, filename, session):
        if session == "user":
            if not self.__listener.isSuperGroup:
                await send_message(
                    message, "Use SuperGroup to download this Link with User!"
                )
                return
            message = await user.get_messages(
                chat_id=message.chat.id, message_ids=message.id
            )
            
        media = getattr(message, message.media.value) if message.media else None

        if media is not None:
            async with global_lock:
                download = media.file_unique_id not in GLOBAL_GID

            if download:
                if filename == "":
                    name = media.file_name if hasattr(media, "file_name") else "None"
                else:
                    name = filename
                    path = path + name
                size = media.file_size
                gid = media.file_unique_id

                msg, button = await stop_duplicate_check(name, self.__listener)
                if msg:
                    await send_message(self.__listener.message, msg, button)
                    await delete_links(self.__listener.message)
                    return
                if limit_exceeded := await limit_checker(size, self.__listener):
                    await self.__listener.onDownloadError(limit_exceeded)
                    await delete_links(self.__listener.message)
                    return
                added_to_queue, event = await is_queued(self.__listener.uid)
                if added_to_queue:
                    LOGGER.info(f"Added to Queue/Download: {name}")
                    async with download_dict_lock:
                        download_dict[self.__listener.uid] = QueueStatus(
                            name, size, gid, self.__listener, "dl"
                        )
                    await self.__listener.on_download_start()
                    await sendStatusMessage(self.__listener.message)
                    await event.wait()
                    async with download_dict_lock:
                        if self.__listener.uid not in download_dict:
                            return
                    from_queue = True
                else:
                    from_queue = False
                await self.__on_download_start(name, size, gid, from_queue)
                await self.__download(message, path)
            else:
                await self.__on_download_error("File already being downloaded!")
        else:
            await self.__on_download_error(
                "No valid media type in the replied message"
            )

    async def cancel_download(self):
        self.__is_cancelled = True
        LOGGER.info(
            f"Cancelling download via User: [ Name: {self.name} ID: {self.__id} ]"
        )
