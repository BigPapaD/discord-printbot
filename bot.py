import logging
import os
import re
import tempfile
import time
import traceback
import wave

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

from printer import PrinterManager

load_dotenv()

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')
IMAGE_EXTENSIONS_WITHOUT_DOT = {ext.lstrip('.') for ext in IMAGE_EXTENSIONS}
AUDIO_EXTENSIONS = ('.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac')
MAX_PRINT_LAST = 50
PRINT_COMMANDS = {'print', 'print-last', 'qr', 'barcode', 'cut'}
PRINT_COOLDOWN_SECONDS = max(0, int(os.getenv('PRINT_COOLDOWN_SECONDS', '3')))
MAX_AUDIO_SPECTROGRAM_SECONDS = max(5, int(os.getenv('MAX_AUDIO_SPECTROGRAM_SECONDS', '120')))
_last_print_command_at = {}


def _parse_allowed_role_ids(raw_value):
    role_ids = set()
    for part in raw_value.split(','):
        role_id = part.strip()
        if not role_id:
            continue
        if role_id.isdigit():
            role_ids.add(int(role_id))
    return role_ids


ALLOWED_PRINT_ROLE_IDS = _parse_allowed_role_ids(os.getenv('PRINT_ALLOWED_ROLE_IDS', ''))
COMMAND_PATTERN = re.compile(
    r'(?<!\S)#(?P<cmd>help|qr|barcode|cut|print-last|print)\b(?:\s+(?P<arg>.*))?',
    re.IGNORECASE,
)

logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
)
logger = logging.getLogger('discord-printbot')


class PrintBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http_session = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()

    async def close(self):
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()


intents = discord.Intents.default()
intents.message_content = True
bot = PrintBot(command_prefix='!', intents=intents)
printer_manager = PrinterManager()


def get_channel_label(channel):
    if isinstance(channel, discord.DMChannel):
        recipient = getattr(channel, 'recipient', None)
        if recipient is not None:
            return f'DM:{recipient.name}'
        return 'DM'
    if isinstance(channel, discord.GroupChannel):
        return f'Group DM:{channel.name or "unnamed"}'
    name = getattr(channel, 'name', None)
    if name:
        return f'#{name}'
    return str(channel)


def extract_no_cut_flag(raw_arg):
    tokens = raw_arg.split()
    filtered_tokens = []
    no_cut = False
    for token in tokens:
        if token.lower() == '-nc':
            no_cut = True
            continue
        filtered_tokens.append(token)
    return ' '.join(filtered_tokens), no_cut


def is_print_allowed(message, command):
    if command not in PRINT_COMMANDS:
        return True, None

    if ALLOWED_PRINT_ROLE_IDS and message.guild is not None:
        author_roles = getattr(message.author, 'roles', [])
        has_allowed_role = any(getattr(role, 'id', None) in ALLOWED_PRINT_ROLE_IDS for role in author_roles)
        if not has_allowed_role:
            return False, 'You are not allowed to use print commands in this server.'

    if PRINT_COOLDOWN_SECONDS > 0:
        now = time.monotonic()
        last_used_at = _last_print_command_at.get(message.author.id)
        if last_used_at is not None:
            elapsed = now - last_used_at
            if elapsed < PRINT_COOLDOWN_SECONDS:
                wait_seconds = int(PRINT_COOLDOWN_SECONDS - elapsed + 0.999)
                return False, f'Please wait {wait_seconds}s before sending another print command.'
        _last_print_command_at[message.author.id] = now

    return True, None


@bot.event
async def on_ready():
    logger.info('%s connected', bot.user)
    logger.info('Watching for #help, #print, #print-last, #qr, #barcode, #cut')
    try:
        printer_manager.check_printer_connection()
        logger.info('Printer connection verified')
    except Exception as e:
        logger.warning('Printer check failed: %s', e)


@bot.event
async def on_error(event, *args, **kwargs):
    logger.error('Unhandled exception in %s', event)
    logger.error(traceback.format_exc())


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    match = COMMAND_PATTERN.search(message.content)
    if not match:
        await bot.process_commands(message)
        return

    command = match.group('cmd').lower()
    arg, no_cut = extract_no_cut_flag((match.group('arg') or '').strip())

    is_allowed, deny_reason = is_print_allowed(message, command)
    if not is_allowed:
        await message.reply(deny_reason)
        await message.add_reaction('❌')
        await bot.process_commands(message)
        return

    if command == 'help':
        await message.reply(
            "```\n"
            "DISCORD PRINT BOT\n"
            "=================\n"
            "Commands\n"
            "#print           Print this message or replied message\n"
            "#print-last N    Print last N messages (max 50)\n"
            "#qr TEXT         Print a QR code\n"
            "#barcode TEXT    Print a Code128 barcode\n"
            "#cut             Cut paper\n"
            "#help            Show this screen\n"
            "(attach audio to print spectrogram)\n"
            "\n"
            "Flag\n"
            "-nc              Disable auto-cut for command\n"
            "```"
        )
        await message.add_reaction('✅')
    elif command == 'qr':
        await handle_qr(message, arg, cut_paper=not no_cut)
    elif command == 'barcode':
        await handle_barcode(message, arg, cut_paper=not no_cut)
    elif command == 'cut':
        if no_cut:
            await message.reply('`-nc` cannot be used with `#cut`.')
            await message.add_reaction('❌')
            await bot.process_commands(message)
            return
        await handle_cut(message)
    elif command == 'print-last':
        await handle_print_last(message, arg, cut_paper=not no_cut)
    elif command == 'print':
        message_to_print = await get_message_to_print(message)
        await handle_print_message(message, message_to_print, cut_paper=not no_cut)

    await bot.process_commands(message)


async def handle_qr(message, qr_data, cut_paper=True):
    try:
        if qr_data:
            printer_manager.print_qr_code(qr_data, cut_paper=cut_paper)
            await message.add_reaction('✅')
        else:
            await message.reply('Please provide data after #qr (e.g., `#qr https://example.com`)')
            await message.add_reaction('❌')
    except Exception as e:
        logger.error('Error printing QR code: %s', e)
        await message.add_reaction('❌')


async def handle_barcode(message, barcode_data, cut_paper=True):
    try:
        if barcode_data:
            printer_manager.print_barcode(barcode_data, cut_paper=cut_paper)
            await message.add_reaction('✅')
        else:
            await message.reply('Please provide data after #barcode (e.g., `#barcode 123456789`)')
            await message.add_reaction('❌')
    except Exception as e:
        logger.error('Error printing barcode: %s', e)
        await message.add_reaction('❌')


async def handle_cut(message):
    try:
        printer_manager.cut_paper()
        await message.add_reaction('✅')
    except Exception as e:
        logger.error('Error cutting paper: %s', e)
        await message.add_reaction('❌')


async def handle_print_last(message, arg, cut_paper=True):
    try:
        if not arg:
            await message.reply('Please specify number of messages (e.g., `#print-last 5`)')
            await message.add_reaction('❌')
            return

        num_messages = int(arg.split()[0])
        if num_messages < 1:
            await message.reply('Please specify a positive number of messages.')
            await message.add_reaction('❌')
            return
        if num_messages > MAX_PRINT_LAST:
            await message.reply(f'Maximum {MAX_PRINT_LAST} messages can be printed at once.')
            await message.add_reaction('❌')
            return

        messages = []
        async for msg in message.channel.history(limit=num_messages + 1, before=message):
            messages.append(msg)
        messages.reverse()

        if not messages:
            await message.reply('No messages to print.')
            await message.add_reaction('❌')
            return

        formatted_output = format_multiple_messages(messages, get_channel_label(message.channel))
        printer_manager.print_message(formatted_output, cut_paper=cut_paper)
        await message.add_reaction('✅')
    except ValueError:
        await message.reply('Please provide a valid number (e.g., `#print-last 5`)')
        await message.add_reaction('❌')
    except Exception as e:
        logger.error('Error printing last messages: %s', e)
        await message.add_reaction('❌')

def format_message_for_print(message):
    output = []
    output.append("=" * 40)
    output.append(f"Channel: {get_channel_label(message.channel)}")
    output.append(f"User: {message.author.display_name}")
    output.append(f"Time: {message.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
    output.append("-" * 40)
    output.extend(wrap_text(message.content, width=40))
    if message.attachments:
        output.append("-" * 40)
        output.append(f"Attachments: {len(message.attachments)}")
        for attachment in message.attachments:
            output.append(f"  - {attachment.filename}")
    if message.embeds:
        embed_count = sum(1 for embed in message.embeds if embed.image or embed.thumbnail)
        if embed_count > 0:
            output.append("-" * 40)
            output.append(f"Embedded Images/GIFs: {embed_count}")
    output.append("=" * 40)
    output.append("")
    return "\n".join(output)

def format_multiple_messages(messages, channel_name):
    output = []
    output.append("=" * 40)
    output.append(f"    LAST {len(messages)} MESSAGES")
    output.append(f"Channel: {channel_name}")
    output.append("=" * 40)
    output.append("")
    
    for i, message in enumerate(messages, 1):
        author = message.author.display_name
        time_str = message.created_at.strftime('%H:%M:%S')
        
        output.append(f"[{i}] {author} @ {time_str}")
        output.append("-" * 40)
        if message.content:
            content = '[command]' if message.content.startswith('#') else message.content
            output.extend(wrap_text(content, width=40))
        else:
            output.append("[no text]")
        if message.attachments:
            output.append(f"  {len(message.attachments)} attachment(s)")
        if message.embeds:
            embed_count = sum(1 for embed in message.embeds if embed.image or embed.thumbnail)
            if embed_count > 0:
                output.append(f"  {embed_count} embedded image(s)")
        output.append("")
    output.append("=" * 40)
    output.append(f"End of {len(messages)} messages")
    output.append("=" * 40)
    output.append("")
    return "\n".join(output)

def wrap_text(text, width=40):
    words = text.split()
    lines = []
    current_line = []
    for word in words:
        if len(" ".join(current_line + [word])) <= width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))
    return lines

def is_image_attachment(attachment):
    return attachment.filename.lower().endswith(IMAGE_EXTENSIONS)


def is_audio_attachment(attachment):
    return attachment.filename.lower().endswith(AUDIO_EXTENSIONS)


def format_audio_caption(filename, duration_seconds):
    safe_name = filename or 'audio'
    minutes = int(duration_seconds // 60)
    seconds = int(duration_seconds % 60)
    return (
        "=" * 40 + "\n"
        "AUDIO SPECTROGRAM\n"
        + "=" * 40 + "\n"
        + f"File: {safe_name}\n"
        + f"Duration: {minutes:02d}:{seconds:02d}\n"
        + "-" * 40 + "\n"
    )

def get_embed_image_url(embed):
    """Extract image URL from embed if present"""
    if embed.image:
        return embed.image.url
    elif embed.thumbnail:
        return embed.thumbnail.url
    return None

async def process_and_print_images(message_to_print):
    printed_any_image = False
    if message_to_print.attachments:
        for attachment in message_to_print.attachments:
            if is_image_attachment(attachment):
                image_path = await download_image(attachment)
                if image_path:
                    try:
                        printer_manager.print_image(image_path)
                        printed_any_image = True
                    except Exception as e:
                        logger.error('Error printing image: %s', e)
                    finally:
                        try:
                            os.remove(image_path)
                        except OSError:
                            pass
            elif is_audio_attachment(attachment):
                audio_path = await download_audio(attachment)
                if audio_path:
                    spectrogram_path = None
                    duration_seconds = 0.0
                    try:
                        spectrogram_path, duration_seconds = generate_spectrogram_from_audio(audio_path, attachment.filename)
                        if spectrogram_path:
                            caption_text = format_audio_caption(attachment.filename, duration_seconds)
                            printer_manager.print_message(caption_text, cut_paper=False)
                            printer_manager.print_image(spectrogram_path)
                            printed_any_image = True
                    except Exception as e:
                        logger.error('Error printing spectrogram for %s: %s', attachment.filename, e)
                    finally:
                        if spectrogram_path:
                            try:
                                os.remove(spectrogram_path)
                            except OSError:
                                pass
                        try:
                            os.remove(audio_path)
                        except OSError:
                            pass
    if message_to_print.embeds:
        for embed in message_to_print.embeds:
            image_url = get_embed_image_url(embed)
            if image_url:
                image_path = await download_image_from_url(image_url)
                if image_path:
                    try:
                        printer_manager.print_image(image_path)
                        printed_any_image = True
                    except Exception as e:
                        logger.error('Error printing embedded image: %s', e)
                    finally:
                        try:
                            os.remove(image_path)
                        except OSError:
                            pass
    return printed_any_image

async def handle_print_message(message, message_to_print, cut_paper):
    try:
        formatted_message = format_message_for_print(message_to_print)
        if cut_paper:
            has_images = any(is_image_attachment(att) for att in message_to_print.attachments)
            has_embeds = any(get_embed_image_url(embed) for embed in message_to_print.embeds)
            should_cut_after_text = not (has_images or has_embeds)
        else:
            should_cut_after_text = False
        printer_manager.print_message(formatted_message, cut_paper=should_cut_after_text)
        printed_any_image = await process_and_print_images(message_to_print)
        if cut_paper and printed_any_image:
            printer_manager.cut_paper()
        await message.add_reaction('✅')
    except Exception as e:
        logger.error('Error printing message: %s', e)
        await message.add_reaction('❌')

async def get_message_to_print(message):
    if message.reference and message.reference.message_id:
        try:
            return await message.channel.fetch_message(message.reference.message_id)
        except Exception as e:
            logger.warning('Could not fetch replied message: %s', e)
    return message

async def download_image(attachment):
    try:
        if not bot.http_session or bot.http_session.closed:
            logger.error('HTTP session is not available for image download')
            return None
        async with bot.http_session.get(attachment.url) as resp:
            if resp.status == 200:
                temp_fd, temp_path = tempfile.mkstemp(suffix=os.path.splitext(attachment.filename)[1])
                with os.fdopen(temp_fd, 'wb') as f:
                    f.write(await resp.read())
                return temp_path
            logger.warning('Failed to download attachment image %s: HTTP %s', attachment.url, resp.status)
    except Exception as e:
        logger.error('Error downloading image: %s', e)
    return None


async def download_audio(attachment):
    try:
        if not bot.http_session or bot.http_session.closed:
            logger.error('HTTP session is not available for audio download')
            return None
        suffix = os.path.splitext(attachment.filename)[1] or '.bin'
        async with bot.http_session.get(attachment.url) as resp:
            if resp.status == 200:
                temp_fd, temp_path = tempfile.mkstemp(suffix=suffix)
                with os.fdopen(temp_fd, 'wb') as f:
                    f.write(await resp.read())
                return temp_path
            logger.warning('Failed to download audio %s: HTTP %s', attachment.url, resp.status)
    except Exception as e:
        logger.error('Error downloading audio: %s', e)
    return None


def generate_spectrogram_from_audio(audio_path, source_name):
    temp_fd, temp_path = tempfile.mkstemp(suffix='.png')
    os.close(temp_fd)

    def _figure_height_for_duration(duration_seconds):
        # Use the printer's unlimited feed direction (paper length) for time detail.
        return min(24.0, max(8.0, duration_seconds / 6.0))

    # Primary path: librosa supports many formats but may need extra system codecs.
    try:
        import numpy as np
        import librosa
        import librosa.display
        import matplotlib

        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        signal, sample_rate = librosa.load(audio_path, sr=None, mono=True)
        if signal.size == 0:
            logger.warning('Audio file %s has no samples; skipping spectrogram', source_name)
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return None, 0.0

        max_samples = sample_rate * MAX_AUDIO_SPECTROGRAM_SECONDS
        original_duration_seconds = float(signal.shape[0]) / float(sample_rate)
        if signal.shape[0] > max_samples:
            signal = signal[:max_samples]

        mel = librosa.feature.melspectrogram(y=signal, sr=sample_rate, n_fft=2048, hop_length=512, n_mels=128)
        mel_db = librosa.power_to_db(mel, ref=np.max)

        fig_height = _figure_height_for_duration(original_duration_seconds)
        fig, ax = plt.subplots(figsize=(4.5, fig_height), dpi=150)
        # Transpose so time is vertical and uses paper length instead of width.
        image = ax.imshow(mel_db.T, origin='lower', aspect='auto', cmap='magma')
        ax.set_title(f'Spectrogram: {source_name[:40]}')
        ax.set_xlabel('Mel bins')
        ax.set_ylabel('Time')
        fig.colorbar(image, ax=ax, format='%+2.0f dB')
        fig.tight_layout()
        fig.savefig(temp_path, format='png')
        plt.close(fig)
        logger.info('Generated spectrogram via librosa for %s', source_name)
        return temp_path, original_duration_seconds
    except Exception as e:
        logger.warning('Librosa spectrogram path failed for %s: %s', source_name, e)

    # Fallback path: WAV-only using stdlib wave + matplotlib.
    try:
        import numpy as np
        import matplotlib

        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        with wave.open(audio_path, 'rb') as wav_file:
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            total_frames = wav_file.getnframes()
            raw_bytes = wav_file.readframes(total_frames)

        dtype_map = {1: np.uint8, 2: np.int16, 4: np.int32}
        if sample_width not in dtype_map:
            raise ValueError(f'Unsupported WAV sample width: {sample_width}')

        signal = np.frombuffer(raw_bytes, dtype=dtype_map[sample_width])
        if channels > 1:
            signal = signal.reshape(-1, channels).mean(axis=1)

        if sample_width == 1:
            signal = (signal.astype(np.float32) - 128.0) / 128.0
        elif sample_width == 2:
            signal = signal.astype(np.float32) / 32768.0
        else:
            signal = signal.astype(np.float32) / 2147483648.0

        if signal.size == 0:
            raise ValueError('WAV file has no samples')

        original_duration_seconds = float(signal.shape[0]) / float(sample_rate)
        max_samples = sample_rate * MAX_AUDIO_SPECTROGRAM_SECONDS
        if signal.shape[0] > max_samples:
            signal = signal[:max_samples]

        # Fallback vertical spectrogram using matplotlib's mlab implementation.
        from matplotlib.mlab import specgram as mlab_specgram

        pxx, _, _ = mlab_specgram(signal, NFFT=1024, Fs=sample_rate, noverlap=512)
        pxx_db = 10.0 * np.log10(np.maximum(pxx, 1e-12))

        fig_height = _figure_height_for_duration(original_duration_seconds)
        fig, ax = plt.subplots(figsize=(4.5, fig_height), dpi=150)
        image = ax.imshow(pxx_db.T, origin='lower', aspect='auto', cmap='magma')
        ax.set_title(f'Spectrogram: {source_name[:40]}')
        ax.set_xlabel('Frequency bins')
        ax.set_ylabel('Time')
        fig.colorbar(image, ax=ax, format='%+2.0f dB')
        fig.tight_layout()
        fig.savefig(temp_path, format='png')
        plt.close(fig)
        logger.info('Generated spectrogram via WAV fallback for %s', source_name)
        return temp_path, original_duration_seconds
    except Exception as e:
        logger.error('Error generating spectrogram for %s: %s', source_name, e)
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return None, 0.0

async def download_image_from_url(url):
    try:
        if not bot.http_session or bot.http_session.closed:
            logger.error('HTTP session is not available for embed image download')
            return None
        ext = '.png'
        if '.' in url:
            url_ext = url.split('.')[-1].split('?')[0].lower()
            if url_ext in IMAGE_EXTENSIONS_WITHOUT_DOT:
                ext = f'.{url_ext}'
        async with bot.http_session.get(url) as resp:
            if resp.status == 200:
                temp_fd, temp_path = tempfile.mkstemp(suffix=ext)
                with os.fdopen(temp_fd, 'wb') as f:
                    f.write(await resp.read())
                return temp_path
            logger.warning('Failed to download embed image %s: HTTP %s', url, resp.status)
    except Exception as e:
        logger.error('Error downloading image from URL: %s', e)
    return None

if __name__ == "__main__":
    token = (os.getenv('DISCORD_TOKEN') or '').strip()
    logger.info('Starting Discord Printer Bot...')
    logger.info(
        'Printer target USB %s:%s',
        os.getenv('PRINTER_USB_VID', '0x04B8'),
        os.getenv('PRINTER_USB_PID', '0x0202'),
    )
    if not token:
        raise ValueError('DISCORD_TOKEN not found in environment variables')
    if token.lower() == 'your_bot_token_here':
        raise ValueError('DISCORD_TOKEN is still placeholder text')

    try:
        bot.run(token, log_handler=None)
    except Exception:
        logger.error('Fatal error while running bot')
        logger.error(traceback.format_exc())
        raise
