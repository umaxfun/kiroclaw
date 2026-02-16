"""C5: File Handler — bidirectional file transfer between Telegram and workspace."""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, Message

logger = logging.getLogger(__name__)


class FileHandler:
    """Downloads inbound files to workspace, sends outbound files to Telegram."""

    @staticmethod
    async def download_to_workspace(message: Message, workspace_path: str) -> str:
        """Download file attached to a Telegram message into the workspace directory.

        Supports: document, photo, audio, voice, video, video_note, sticker.
        Returns the absolute local file path.
        Raises ValueError if no downloadable attachment is found.
        """
        file_id: str | None = None
        filename: str | None = None

        if message.document:
            file_id = message.document.file_id
            filename = message.document.file_name or f"document_{message.document.file_unique_id}"
        elif message.photo:
            # Use the largest photo (last in the list)
            photo = message.photo[-1]
            file_id = photo.file_id
            filename = f"photo_{photo.file_unique_id}.jpg"
        elif message.audio:
            file_id = message.audio.file_id
            filename = message.audio.file_name or f"audio_{message.audio.file_unique_id}.mp3"
        elif message.voice:
            file_id = message.voice.file_id
            filename = f"voice_{message.voice.file_unique_id}.ogg"
        elif message.video:
            file_id = message.video.file_id
            filename = message.video.file_name or f"video_{message.video.file_unique_id}.mp4"
        elif message.video_note:
            file_id = message.video_note.file_id
            filename = f"videonote_{message.video_note.file_unique_id}.mp4"
        elif message.sticker:
            file_id = message.sticker.file_id
            filename = f"sticker_{message.sticker.file_unique_id}.webp"

        if file_id is None or filename is None:
            raise ValueError("No downloadable attachment")

        destination = Path(workspace_path) / filename
        bot = message.bot
        if bot is None:
            raise ValueError("Message has no bot instance")
        await bot.download(file_id, destination=destination)

        resolved = str(destination.resolve())
        logger.info("Downloaded file to %s", resolved)
        return resolved

    @staticmethod
    async def send_file(
        bot: Bot,
        chat_id: int,
        thread_id: int,
        file_path: str,
        caption: str | None = None,
    ) -> None:
        """Send a file from the workspace to the Telegram thread via sendDocument."""
        await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(file_path),
            caption=caption or None,  # convert empty string to None
            message_thread_id=thread_id,
        )

    @staticmethod
    def validate_path(file_path: str, workspace_path: str) -> bool:
        """Ensure file_path resolves to a location within workspace_path.

        Prevents path traversal attacks (e.g., ../../etc/passwd).
        Resolves symlinks and checks for path components like '..'.
        """
        try:
            # Resolve both paths to their canonical forms (following symlinks)
            resolved = Path(file_path).resolve(strict=False)
            workspace_resolved = Path(workspace_path).resolve(strict=False)
            
            # Check if resolved path is within workspace
            # This handles symlinks and .. components correctly
            return resolved.is_relative_to(workspace_resolved)
        except (ValueError, OSError):
            # Invalid path or resolution error
            return False
