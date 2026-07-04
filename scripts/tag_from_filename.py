# -*- coding: utf-8 -*-
"""
从文件名提取 artist/title，写入标签。
只处理缺少 artist 或 title 的文件，已有标签的跳过。

支持格式：FLAC (VorbisComment)、MP3 (ID3)、MP4/M4A、OGG
"""
import os
import shutil
import sys
import time

from mutagen.flac import FLAC
from mutagen.easyid3 import EasyID3
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
import mutagen

# 确保 stdout 能输出 Unicode，避免 Windows GBK 终端崩溃。
# 优先切换到 UTF-8，失败则保留原编码 + errors='replace'
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, OSError):
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or 'utf-8', errors='replace')
    except Exception:
        pass

# Windows 中文系统 os.listdir 使用 GBK 编码，
# 文件名含超 GBK 范围字符时会被替换为 ?（surrogate 字符）。
# 用 surrogateescape 可保留原始字节信息。
def safe_listdir(directory):
    """用 surrogateescape 解码文件名，避免 Windows GBK 替换 Unicode 字符。

    检测字符串中是否包含孤立的 surrogate 码点（surrogateescape 的标记），
    如果有则用 bytes 模式重新读取并以 surrogateescape 解码。
    """
    entries = os.listdir(directory)
    for name in entries:
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in name):
            bytenames = os.listdir(os.fsencode(directory))
            return [os.fsdecode(b, errors='surrogateescape') for b in bytenames]
    return entries


SUPPORTED_EXT = {'.flac', '.mp3', '.m4a', '.mp4', '.ogg'}

MP3_ARTIST_KEYS = ('TPE1', 'TPE2')
MP3_TITLE_KEYS = ('TIT2',)
FLAC_ARTIST_KEYS = ('artist', 'ARTIST')
FLAC_TITLE_KEYS = ('title', 'TITLE')
MP4_ARTIST_KEYS = ('\xa9ART', 'aART')
MP4_TITLE_KEYS = ('\xa9nam',)


def _has_key(tags, keys):
    return any(k in tags for k in keys)


def _set_tag(audio, key, value):
    """安全写入标签值，覆盖 VorbisComment / ID3 / MP4 类型差异。"""
    if isinstance(audio, (FLAC, OggVorbis)):
        audio[key] = value
    elif isinstance(audio, EasyID3):
        audio[key] = value
    elif isinstance(audio, MP4):
        audio[key] = [value]
    else:
        audio[key] = value


def _safe_save(audio, path):
    """备份后写入标签（最多 3 次重试）。path → path.bak → 写入 → 成功则删 bak。"""
    bak = path + '.bak'
    try:
        shutil.copy2(path, bak)
    except OSError:
        pass
    for attempt in range(3):
        try:
            audio.save()
            break
        except (OSError, PermissionError):
            if attempt < 2:
                time.sleep(1)
            else:
                raise
    try:
        os.remove(bak)
    except OSError:
        pass


def process(music_dir):
    updated = 0
    skipped = 0
    failed = []

    for fname in safe_listdir(music_dir):
        name, ext = os.path.splitext(fname)
        if ext.lower() not in SUPPORTED_EXT:
            continue

        path = os.path.join(music_dir, fname)
        ext_lower = ext.lower()

        # 读取现有标签
        try:
            audio = mutagen.File(path)
        except Exception as e:
            failed.append((fname, f'load error: {e}'))
            continue

        if audio is None:
            failed.append((fname, 'unsupported format'))
            continue

        tags = getattr(audio, 'tags', None) or {}

        has_artist = _has_key(tags, (FLAC_ARTIST_KEYS if ext_lower in ('.flac', '.ogg') else
                                     MP4_ARTIST_KEYS if ext_lower in ('.m4a', '.mp4') else
                                     MP3_ARTIST_KEYS))
        has_title = _has_key(tags, (FLAC_TITLE_KEYS if ext_lower in ('.flac', '.ogg') else
                                    MP4_TITLE_KEYS if ext_lower in ('.m4a', '.mp4') else
                                    MP3_TITLE_KEYS))

        if has_artist and has_title:
            skipped += 1
            continue

        # 解析文件名: "艺术家 - 曲名"
        if ' - ' not in name:
            failed.append((fname, 'no " - " separator'))
            continue

        artist, title = name.split(' - ', 1)
        artist = artist.strip()
        title = title.strip()

        if not artist or not title:
            failed.append((fname, 'empty artist or title after split'))
            continue

        # 清理 surrogate 字符（Windows GBK 编码残留）
        artist = artist.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')
        title = title.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')

        try:
            if ext_lower == '.flac':
                flac = FLAC(path)
                if not has_artist:
                    _set_tag(flac, 'artist', artist)
                if not has_title:
                    _set_tag(flac, 'title', title)
                _safe_save(flac, path)
            elif ext_lower == '.ogg':
                ogg = OggVorbis(path)
                if not has_artist:
                    _set_tag(ogg, 'artist', artist)
                if not has_title:
                    _set_tag(ogg, 'title', title)
                _safe_save(ogg, path)
            elif ext_lower in ('.m4a', '.mp4'):
                mp4 = MP4(path)
                if not has_artist:
                    _set_tag(mp4, '\xa9ART', artist)
                if not has_title:
                    _set_tag(mp4, '\xa9nam', title)
                _safe_save(mp4, path)
            else:
                mp3 = EasyID3(path)
                if not has_artist:
                    _set_tag(mp3, 'artist', artist)
                if not has_title:
                    _set_tag(mp3, 'title', title)
                _safe_save(mp3, path)
            updated += 1
        except Exception as e:
            failed.append((fname, str(e)[:200]))

    print(f"Updated: {updated}, Skipped: {skipped}, Failed: {len(failed)}")
    for name, reason in failed[:20]:
        # 安全输出，replace 掉终端不支持的字符
        safe_name = name[:80].encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8', errors='replace')
        print(f"  {safe_name}: {reason}")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        process(sys.argv[1])
    else:
        print("Usage: python tag_from_filename.py <music_directory>")
