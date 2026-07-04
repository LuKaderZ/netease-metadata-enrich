# -*- coding: utf-8 -*-
"""
从网易云音乐元数据源匹配并写入 album/track/date 标签。

支持两种元数据源：
  1. 自有歌单（推荐）：python enrich_from_netease.py <dir> --playlist-id <歌单ID>
  2. API 搜索（补充）：python enrich_from_netease.py <dir> --search

需要：pip install mutagen NetEaseMusicApi requests
"""
import json, os, re, shutil, sys, time, unicodedata
from datetime import datetime, timedelta
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.easyid3 import EasyID3
import mutagen
import requests

# 确保 stdout 能输出 Unicode，避免 Windows GBK 终端崩溃。
# 优先切换到 UTF-8，失败则保留原编码 + errors='replace'
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, OSError):
    try:
        sys.stdout.reconfigure(encoding=sys.stdout.encoding or 'utf-8', errors='replace')
    except Exception:
        pass

HEADERS = {
    'Cookie': 'appver=1.5.0.75771',
    'Referer': 'http://music.163.com',
}

# 半角/全角对照表 — CJK 标点符号和假名
_HALFWIDTH_MAP = {
    ord('、'): ',', ord('。'): '.', ord('，'): ',', ord('：'): ':',
    ord('；'): ';', ord('！'): '!', ord('？'): '?', ord('～'): '~',
    ord('（'): '(', ord('）'): ')', ord('［'): '[', ord('］'): ']',
    ord('＜'): '<', ord('＞'): '>', ord('「'): '"', ord('」'): '"',
    ord('『'): '"', ord('』'): '"', ord('／'): '/',
    ord('Ａ'): 'A', ord('Ｂ'): 'B', ord('Ｃ'): 'C', ord('Ｄ'): 'D',
    ord('Ｅ'): 'E', ord('Ｆ'): 'F', ord('Ｇ'): 'G', ord('Ｈ'): 'H',
    ord('Ｉ'): 'I', ord('Ｊ'): 'J', ord('Ｋ'): 'K', ord('Ｌ'): 'L',
    ord('Ｍ'): 'M', ord('Ｎ'): 'N', ord('Ｏ'): 'O', ord('Ｐ'): 'P',
    ord('Ｑ'): 'Q', ord('Ｒ'): 'R', ord('Ｓ'): 'S', ord('Ｔ'): 'T',
    ord('Ｕ'): 'U', ord('Ｖ'): 'V', ord('Ｗ'): 'W', ord('Ｘ'): 'X',
    ord('Ｙ'): 'Y', ord('Ｚ'): 'Z',
    ord('ａ'): 'a', ord('ｂ'): 'b', ord('ｃ'): 'c', ord('ｄ'): 'd',
    ord('ｅ'): 'e', ord('ｆ'): 'f', ord('ｇ'): 'g', ord('ｈ'): 'h',
    ord('ｉ'): 'i', ord('ｊ'): 'j', ord('ｋ'): 'k', ord('ｌ'): 'l',
    ord('ｍ'): 'm', ord('ｎ'): 'n', ord('ｏ'): 'o', ord('ｐ'): 'p',
    ord('ｑ'): 'q', ord('ｒ'): 'r', ord('ｓ'): 's', ord('ｔ'): 't',
    ord('ｕ'): 'u', ord('ｖ'): 'v', ord('ｗ'): 'w', ord('ｘ'): 'x',
    ord('ｙ'): 'y', ord('ｚ'): 'z',
    ord('０'): '0', ord('１'): '1', ord('２'): '2', ord('３'): '3',
    ord('４'): '4', ord('５'): '5', ord('６'): '6', ord('７'): '7',
    ord('８'): '8', ord('９'): '9',
}


def _strip_diacritics(s):
    """去除拉丁字符的变音符号，保留 CJK 字符不受影响。"""
    # NFKD 将组合字符分解（é → e + ́），再过滤掉组合标记
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize(s):
    """归一化字符串用于模糊比较。

    NFKC 处理半角假名→全角、全角 ASCII→半角，然后去重音、剥离连接词和符号。
    """
    s = unicodedata.normalize('NFKC', s)
    s = s.lower().strip()
    s = _strip_diacritics(s)
    s = s.translate(_HALFWIDTH_MAP)
    # 移除 featuring / vs / with 连接词
    s = re.sub(r'\s*(feat\.?|ft\.?|featuring|vs\.?|versus|with|pres\.?|presented by)\s+', ' ', s)
    # 移除括号及内容
    s = re.sub(r'\([^)]*\)', '', s)
    s = re.sub(r'\［[^］]*\］', '', s)
    s = re.sub(r'\[[^\]]*\]', '', s)
    s = re.sub(r'<[^>]*>', '', s)
    # Python 3 的 \w 已涵盖 Unicode 全字母集（含 CJK/Hangul/Kana 等），
    # 无需显式列出字符范围
    s = re.sub(r'[^\w\s\-\.]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# 合理音乐发行年份范围 (Unix ms)
_MIN_MS = -2208988800000   # 1900-01-01
_MAX_MS = 4102444800000    # 2100-01-01

def safe_ts(ms):
    """将毫秒级时间戳转换为 YYYY-MM-DD，过滤脏数据。"""
    if not ms:
        return ''
    try:
        if ms < _MIN_MS or ms > _MAX_MS:
            return ''
        if ms < 0:
            return (datetime(1970, 1, 1) + timedelta(seconds=ms / 1000)).strftime('%Y-%m-%d')
        return datetime.fromtimestamp(ms / 1000).strftime('%Y-%m-%d')
    except (OSError, ValueError, OverflowError):
        return ''

def fetch_playlist(playlist_id):
    """Fetch all tracks from a NetEase playlist.

    网络错误会打印诊断信息并返回空列表，不会让整个流程崩溃。
    """
    url = f'http://music.163.com/api/v6/playlist/detail?id={playlist_id}&n=10000'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        j = resp.json()
    except requests.exceptions.Timeout:
        print(f'Error: playlist detail request timed out ({url})')
        return []
    except requests.exceptions.ConnectionError:
        print(f'Error: cannot connect to music.163.com — check network')
        return []
    except requests.exceptions.HTTPError as e:
        print(f'Error: playlist detail HTTP {e.response.status_code if e.response else "??"}')
        return []
    except (ValueError, requests.exceptions.JSONDecodeError):
        print('Error: playlist detail returned non-JSON response')
        return []

    pl = j.get('playlist', j.get('result', {}))
    track_ids = [t['id'] for t in pl.get('trackIds', [])]
    total = len(track_ids)
    if not total:
        print(f'Playlist {playlist_id} is empty or not found')
        return []
    print(f'Playlist has {total} trackIds, fetching details...')

    songs = []
    retries = 3
    for i in range(0, total, 100):
        batch = track_ids[i:i+100]
        ids_str = '%5B' + '%2C'.join(str(x) for x in batch) + '%5D'
        url_d = f'http://music.163.com/api/song/detail?ids={ids_str}'

        for attempt in range(retries):
            try:
                resp = requests.get(url_d, headers=HEADERS, timeout=15)
                resp.raise_for_status()
                j = resp.json()
                songs.extend(j.get('songs', []))
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError, ValueError):
                if attempt < retries - 1:
                    wait = (attempt + 1) * 2
                    print(f'  retry {attempt+1}/{retries} batch {i} after {wait}s...')
                    time.sleep(wait)
                else:
                    print(f'  warning: batch {i} failed after {retries} retries, skipping')

        if (i // 100) % 5 == 0:
            print(f'  {i}/{total}')
        time.sleep(0.15)
    print(f'Fetched {len(songs)} songs')
    return songs

def build_lookup(songs):
    """Build multi-strategy lookup tables from song list.

    检测重复 key 并打印告警，避免同名歌曲静默覆盖。
    """
    lookup = {}
    title_lookup = {}
    alb_track_lookup = {}
    dup_artist_title = 0
    dup_title = 0
    dup_alb_track = 0

    for song in songs:
        t = normalize(song.get('name', ''))
        artists = [normalize(a.get('name', '')) for a in song.get('artists', [])]
        composite = normalize(' '.join(a.get('name', '') for a in song.get('artists', [])))

        for artist in artists:
            key = f"{artist}|{t}"
            if key in lookup:
                dup_artist_title += 1
            lookup[key] = song
        if len(artists) > 1:
            key = f"{composite}|{t}"
            if key not in lookup:
                lookup[key] = song

        if t:
            if t in title_lookup:
                dup_title += 1
            title_lookup[t] = song

        alb = normalize(song.get('album', {}).get('name', ''))
        trk = str(song.get('no', ''))
        if alb and trk:
            key = f"{alb}|{trk}"
            if key in alb_track_lookup:
                dup_alb_track += 1
            alb_track_lookup[key] = song

    if dup_artist_title or dup_title or dup_alb_track:
        print(f'  dup keys: artist|title={dup_artist_title}, title={dup_title}, album|track={dup_alb_track}')

    return lookup, title_lookup, alb_track_lookup

def match_song(artist, title, album, lookup, title_lookup, alb_track_lookup, existing_track=''):
    """Try multiple matching strategies."""
    n_artist = normalize(artist)
    n_title = normalize(title)

    # 1. Exact match: artist + title 双归一化完全一致
    key = f"{n_artist}|{n_title}"
    if key in lookup:
        return lookup[key]

    # 2. Album+track fallback: 同一张专辑的同轨号
    if album and existing_track:
        tk = str(existing_track).split('/')[0]
        k = f"{normalize(album)}|{tk}"
        if k in alb_track_lookup:
            return alb_track_lookup[k]

    # 3. Artist subset match: 歌名一致 + 本地艺术家是歌单艺术家的子集
    a_tokens = set(n_artist.split())
    if a_tokens:
        for k, v in lookup.items():
            ka, sep, kt = k.partition('|')
            if not sep:
                continue
            if kt == n_title and a_tokens.issubset(set(ka.split())):
                return v

    # 4. Title only: 歌名匹配，艺术家不要求（兜底策略，可能误配常见歌名）
    if n_title in title_lookup:
        return title_lookup[n_title]

    # 5. Title prefix match: 最宽松，标题前缀 + 一个艺术家词重叠
    tw = n_title.split()
    if len(tw) >= 3:
        prefix = ' '.join(tw[:4])
        for k, v in lookup.items():
            ka, sep, kt = k.partition('|')
            if not sep:
                continue
            if a_tokens & set(ka.split()) and kt.startswith(prefix):
                return v

    return None

def search_netease(artist, title, existing_album=''):
    """Fallback: search NetEase API for a single track."""
    try:
        from NetEaseMusicApi import api
    except ImportError:
        return None
    try:
        results = api.search.songs(f"{artist} {title}", limit=5)
    except Exception:
        return None
    if not results:
        return None
    # Prefer album match
    best = results[0]
    n_alb = existing_album.lower().strip()[:10] if existing_album else ''
    for r in results:
        if n_alb and n_alb in r.get('album', {}).get('name', '').lower():
            return r
    return best

def _safe_listdir(directory):
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


def _get_mp3_tag_value(tags, key):
    """安全读取 MP3 ID3 标签值，兼容 Frame 对象和 EasyID3 列表格式。"""
    val = tags.get(key)
    if val is None:
        return ''
    if isinstance(val, str):
        return val
    if isinstance(val, (list, tuple)) and len(val) > 0:
        return str(val[0])
    # mutagen ID3 Frame 对象 — 取 text 属性
    if hasattr(val, 'text') and val.text:
        return str(val.text[0]) if isinstance(val.text, (list, tuple)) else str(val.text)
    return str(val) if val else ''


def _set_tag_value(tags_dict, key, value):
    """向标签字典写入值，处理 VorbisComments / ID3 列表差异。"""
    if value:
        tags_dict[key] = value


def _safe_save(audio, path):
    """备份后写入标签，防止写入中途崩溃导致文件损坏。

    流程：path → path.bak → 写入 path（最多 3 次重试）→ 成功则删除 path.bak。
    若 path.bak 已存在（上次失败遗留），保留它并直接覆盖写入 path。
    """
    bak = path + '.bak'
    try:
        shutil.copy2(path, bak)
    except OSError:
        pass

    for attempt in range(3):
        try:
            audio.save()
            break
        except (OSError, PermissionError) as e:
            if attempt < 2:
                time.sleep(1)
            else:
                raise

    # 写入成功，清理备份
    try:
        os.remove(bak)
    except OSError:
        pass


def _enrich_tags(audio, tags, is_mp3, alb_name, date_str, tr_num, path):
    """将匹配到的元数据写入音频标签，已有字段跳过。备份后安全写入。"""
    updated = False
    if is_mp3:
        if alb_name and 'TALB' not in tags:
            audio['album'] = alb_name
            updated = True
        if date_str and 'TDRC' not in tags:
            audio['date'] = date_str
            updated = True
        if tr_num and 'TRCK' not in tags:
            audio['tracknumber'] = tr_num
            updated = True
    else:
        if alb_name and 'album' not in tags:
            audio['album'] = alb_name
            updated = True
        if date_str and 'date' not in tags:
            audio['date'] = date_str
            updated = True
        if tr_num and 'tracknumber' not in tags:
            audio['tracknumber'] = tr_num
            updated = True
    if updated:
        _safe_save(audio, path)
    return updated


def enrich(music_dir, playlist_id=None, use_search=False):
    """Main enrichment routine."""
    # Build metadata source
    songs = []
    if playlist_id:
        songs = fetch_playlist(playlist_id)
    lookup, title_lookup, alb_track_lookup = build_lookup(songs)

    # Find files needing enrichment
    SUPPORTED_EXT = {'.flac', '.mp3', '.m4a', '.mp4', '.ogg'}
    music_files = [f for f in _safe_listdir(music_dir)
                   if os.path.splitext(f)[1].lower() in SUPPORTED_EXT]
    updated = skipped = no_match = 0

    for fname in music_files:
        path = os.path.join(music_dir, fname)
        try:
            audio = mutagen.File(path)
        except Exception:
            continue

        if audio is None:
            continue

        tags = getattr(audio, 'tags', None) or {}
        is_mp3 = fname.lower().endswith('.mp3')

        # Read existing tags — MP3 ID3 uses Frame objects, not lists
        if is_mp3:
            if all(k in tags for k in ('TALB', 'TRCK', 'TDRC')):
                skipped += 1
                continue
            artist = _get_mp3_tag_value(tags, 'TPE1')
            if not artist:
                artist = _get_mp3_tag_value(tags, 'TPE2')
            title = _get_mp3_tag_value(tags, 'TIT2')
            album = _get_mp3_tag_value(tags, 'TALB')
            existing_track = _get_mp3_tag_value(tags, 'TRCK')
        else:
            if all(k in tags for k in ('album', 'tracknumber', 'date')):
                skipped += 1
                continue
            artist = (tags.get('artist') or tags.get('ARTIST') or [''])[0]
            title = (tags.get('title') or tags.get('TITLE') or [''])[0]
            album = (tags.get('album') or tags.get('ALBUM') or [''])[0]
            existing_track = (tags.get('tracknumber') or tags.get('TRACKNUMBER') or [''])[0]

        if not artist or not title:
            continue

        # Clean surrogate characters from Windows GBK encoding
        artist = artist.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')
        title = title.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')

        # Match
        match = match_song(artist, title, album, lookup, title_lookup, alb_track_lookup, existing_track)

        if not match and use_search:
            match = search_netease(artist, title, album)

        if not match:
            no_match += 1
            continue

        alb_name = match.get('album', {}).get('name', '')
        pt = match.get('album', {}).get('publishTime', 0)
        tr_num = str(match.get('no', ''))
        date_str = safe_ts(pt)

        try:
            if _enrich_tags(audio, tags, is_mp3, alb_name, date_str, tr_num, path):
                updated += 1
        except Exception as e:
            safe_name = fname[:50].encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8', errors='replace')
            print(f'  Write error [{safe_name}]: {e}')

    print(f'\nUpdated: {updated}, Skipped: {skipped}, No match: {no_match}')

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('dir', help='Music directory')
    ap.add_argument('--playlist-id', help='NetEase playlist ID as metadata source')
    ap.add_argument('--search', action='store_true', help='Fallback to API search for unmatched')
    args = ap.parse_args()
    enrich(args.dir, playlist_id=args.playlist_id, use_search=args.search)
