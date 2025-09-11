import re
import base64
import json
from struct import pack
from pyrogram.file_id import FileId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
from info import (
    CAPTION_LANGUAGES, DATABASE_URI, DATABASE_URI2, DATABASE_NAME,
    COLLECTION_NAME, USE_CAPTION_FILTER, MAX_B_TN, MOVIE_UPDATE_CHANNEL,
    OWNERID, MULTIPLE_DATABASE
)
from utils import get_settings, save_group_settings, temp, get_status
from .Imdbposter import get_movie_details, fetch_image
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# First Database For File Saving 
client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
col = db.get_collection(COLLECTION_NAME)

# Second Database For File Saving (if enabled)
client2 = AsyncIOMotorClient(DATABASE_URI2) if MULTIPLE_DATABASE else None
db2 = client2[DATABASE_NAME] if MULTIPLE_DATABASE else None
sec_col = db2.get_collection(COLLECTION_NAME) if MULTIPLE_DATABASE else None

def clean_file_name(file_name):
    """Clean and format the file name."""
    file_name = re.sub(r"(_|-|\.|\+)", " ", str(file_name)) 
    unwanted_chars = ['[', ']', '(', ')', '{', '}']
    for char in unwanted_chars:
        file_name = file_name.replace(char, '')
    return ' '.join(
        filter(
            lambda x: not x.startswith('@')
                      and not x.startswith('http')
                      and not x.startswith('www.')
                      and not x.startswith('t.me'),
            file_name.split()
        )
    )

def is_file_already_saved(file_id, file_name):
    """Check if the file is already saved in either collection."""
    found1 = {'file_name': file_name}
    found = {'file_id': file_id}
    for collection in [col] + ([sec_col] if MULTIPLE_DATABASE else []):
        if collection.find_one(found1) or collection.find_one(found):
            print(f"{file_name} is already saved.")
            return True
    return False

async def save_file(media):
    """Save file in the database."""
    file_id = unpack_new_file_id(media.file_id)
    file_name = clean_file_name(media.file_name)
    file = {
        'file_id': file_id,
        'file_name': file_name,
        'file_size': media.file_size,
        'caption': media.caption.html if media.caption else None
    }
    if is_file_already_saved(file_id, file_name):
        return False, 0
    try:
        await col.insert_one(file)
        print(f"{file_name} is successfully saved.")
        return True, 1
    except DuplicateKeyError:
        print(f"{file_name} is already saved.")
        return False, 0
    except Exception:
        if MULTIPLE_DATABASE and sec_col:
            try:
                await sec_col.insert_one(file)
                print(f"{file_name} is successfully saved.")
                return True, 1
            except DuplicateKeyError:
                print(f"{file_name} is already saved.")
                return False, 0
        print("Your Current File Database Is Full, Turn On Multiple Database Feature And Add Second File Mongodb To Save File.")
        return False, 0

async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False):
    """For given query return (results, next_offset, total_results)"""
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-_])' + re.escape(query) + r'(\b|[\.\+\-_])'
    else:
        raw_pattern = re.sub(r'\s+', r'.*[\s\.\+\-_]', query)
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except Exception:
        regex = query
    filter_query = {'file_name': regex}
    files = []
    if MULTIPLE_DATABASE and sec_col:
        cursor1 = col.find(filter_query).sort('$natural', -1).skip(offset).limit(max_results)
        cursor2 = sec_col.find(filter_query).sort('$natural', -1).skip(offset).limit(max_results)
        files += await cursor1.to_list(length=max_results)
        files += await cursor2.to_list(length=max_results)
        total_results = await col.count_documents(filter_query) + await sec_col.count_documents(filter_query)
    else:
        cursor = col.find(filter_query).sort('$natural', -1).skip(offset).limit(max_results)
        files = await cursor.to_list(length=max_results)
        total_results = await col.count_documents(filter_query)
    next_offset = "" if (offset + max_results) >= total_results else (offset + max_results)
    return files, next_offset, total_results

async def get_bad_files(query, file_type=None, use_filter=False):
    """For given query return (results, total_results)"""
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = rf'(\b|[.+-_]){re.escape(query)}(\b|[.+-_])'
    else:
        raw_pattern = re.sub(r'\s+', r'.*[s.+-_]', query)
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except re.error:
        return [], 0
    filter_criteria = {'file_name': regex}
    if USE_CAPTION_FILTER:
        filter_criteria = {'$or': [filter_criteria, {'caption': regex}]}
    def count_documents(collection):
        return collection.count_documents(filter_criteria)
    def find_documents(collection):
        return list(collection.find(filter_criteria))
    if MULTIPLE_DATABASE and sec_col:
        total_results = await col.count_documents(filter_criteria) + await sec_col.count_documents(filter_criteria)
        files = await col.find(filter_criteria).to_list(length=0) + await sec_col.find(filter_criteria).to_list(length=0)
    else:
        total_results = await col.count_documents(filter_criteria)
        files = await col.find(filter_criteria).to_list(length=0)
    return files, total_results

async def get_file_details(query):
    result = await col.find_one({'file_id': query})
    if not result and MULTIPLE_DATABASE and sec_col:
        result = await sec_col.find_one({'file_id': query})
    return result

def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")

def unpack_new_file_id(new_file_id):
    """Return file_id"""
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash
        )
    )
    return file_id

async def send_msg(bot, filename, caption): 
    try:
        filename = re.sub(r'\(\@\S+\)|\[\@\S+\]|\b@\S+|\bwww\.\S+', '', filename).strip()
        caption = re.sub(r'\(\@\S+\)|\[\@\S+\]|\b@\S+|\bwww\.\S+', '', caption).strip()
        year_match = re.search(r"\b(19|20)\d{2}\b", caption)
        year = year_match.group(0) if year_match else None
        pattern = r"(?i)(?:s|season)0*(\d{1,2})"
        season = re.search(pattern, caption) or re.search(pattern, filename)
        season = season.group(1) if season else None 
        if year:
            filename = filename[: filename.find(year) + 4]  
        elif season and season in filename:
            filename = filename[: filename.find(season) + 1]
        qualities = [
            "ORG", "org", "hdcam", "HDCAM", "HQ", "hq", "HDRip", "hdrip", "camrip", "CAMRip", "hdtc",
            "predvd", "DVDscr", "dvdscr", "dvdrip", "dvdscr", "HDTC", "dvdscreen", "HDTS", "hdts"
        ]
        quality = await get_qualities(caption.lower(), qualities) or "HDRip"
        language = ""
        possible_languages = CAPTION_LANGUAGES
        for lang in possible_languages:
            if lang.lower() in caption.lower():
                language += f"{lang}, "
        language = language[:-2] if language else "Not idea 😄"
        filename = re.sub(r"[\(\)\[\]\{\}:;'\-!]", "", filename)
        text = "#𝑵𝒆𝒘_𝑭𝒊𝒍𝒆_𝑨𝒅𝒅𝒆𝒅 ✅\n\n👷𝑵𝒂𝒎𝒆: `{}`\n\n🌳𝑸𝒖𝒂𝒍𝒊𝒕𝒚: {}\n\n🍁𝑨𝒖𝒅𝒊𝒐: {}"
        text = text.format(filename, quality, language)
        if await add_name(OWNERID, filename):
            imdb = await get_movie_details(filename)  
            resized_poster = None
            if imdb:
                poster_url = imdb.get('poster_url')
                if poster_url:
                    resized_poster = await fetch_image(poster_url)  
            filenames = filename.replace(" ", '-')
            btn = [[InlineKeyboardButton('🌲 Get Files 🌲', url=f"https://telegram.me/{temp.U_NAME}?start=getfile-{filenames}")]]
            if resized_poster:
                await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=resized_poster,
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(btn)
                )
            else:              
                await bot.send_message(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(btn)
                )
    except Exception:
        pass

async def get_qualities(text, qualities: list):
    """Get all Quality from text"""
    quality = []
    for q in qualities:
        if q in text:
            quality.append(q)
    quality = ", ".join(quality)
    return quality[:-2] if quality.endswith(", ") else quality
