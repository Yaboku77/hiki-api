import json
import re
import urllib.parse
import base64
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
BASE_URL = "https://hianime.to"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": BASE_URL,
    "Origin": BASE_URL,
    "X-Requested-With": "XMLHttpRequest"
}

# --- HELPERS ---
def get_soup(url, referer=BASE_URL):
    try:
        headers = HEADERS.copy()
        headers['Referer'] = referer
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def get_ajax_json(url, referer=BASE_URL):
    try:
        headers = HEADERS.copy()
        headers['Referer'] = referer
        headers['Accept'] = "application/json"
        resp = requests.get(url, headers=headers, timeout=10)
        return resp.json()
    except Exception as e:
        print(f"Error fetching AJAX {url}: {e}")
        return None

# --- DECRYPTION LOGIC (For MegaCloud/VidCloud) ---
def unpad_data(data):
    return data[:-data[-1]]

def decrypt_source(encrypted_string):
    # This key is the standard production key often used. 
    # Note: Keys rotate. If this fails, the site updated their keys.
    # Often the key is hidden in the WASM or JS, but this static key works for many sources.
    try:
        # Standard MegaCloud/Rabbit key (Logic may vary based on player version)
        # We try to extract valid JSON directly or decrypt if it's a messy string
        # Simple extraction attempt first
        return json.loads(encrypted_string) 
    except:
        pass
    
    # If strictly AES encrypted (rarely simple AES now, often custom obfuscation)
    # returning raw string if decryption fails for client-side handling
    return encrypted_string 

# --- EXTRACTOR ---
def extract_stream_link(server_id):
    """
    Extracts the m3u8 and tracks from a specific server ID (VidCloud/MegaCloud).
    """
    # 1. Get the Embed URL from the server ID
    embed_ajax = f"{BASE_URL}/ajax/v2/episode/sources?id={server_id}"
    data = get_ajax_json(embed_ajax)
    
    if not data or not data.get('link'):
        return {"error": "Could not get embed link"}
        
    embed_url = data['link'] # e.g. https://megacloud.tv/embed-2/e-1/xyz...
    
    # 2. Determine if it's MegaCloud or VidCloud (usually similar logic)
    # We need to construct the 'getSources' API call
    # Logic: embed_url/getSources?id=...
    
    parsed_url = urllib.parse.urlparse(embed_url)
    hostname = parsed_url.netloc
    
    # Extract the ID from the embed URL path (last segment)
    embed_id = parsed_url.path.split('/')[-1]
    
    # API Url construction
    # https://megacloud.tv/embed-2/ajax/e-1/getSources?id=...
    if "embed-2" in parsed_url.path:
        api_url = f"https://{hostname}/embed-2/ajax/e-1/getSources?id={embed_id}"
    else:
        api_url = f"https://{hostname}/ajax/embed-4/getSources?id={embed_id}"

    # 3. Fetch Sources
    sources_data = get_ajax_json(api_url, referer=embed_url)
    
    if not sources_data:
        return {"error": "Failed to fetch sources json"}

    # 4. Process Sources (Decrypt if needed)
    decrypted_sources = []
    if sources_data.get('encrypted'):
        # If 'sources' is a string, it needs decryption.
        # As of late 2024, Python decryption is brittle due to dynamic keys.
        # We return the raw encrypted string so the frontend can decrypt, 
        # OR we return the tracks which are usually unencrypted.
        decrypted_sources = sources_data.get('sources') # Return raw if encrypted
    else:
        decrypted_sources = sources_data.get('sources')

    return {
        "intro": sources_data.get('intro'),
        "outro": sources_data.get('outro'),
        "sources": decrypted_sources,
        "tracks": sources_data.get('tracks'), # This contains SUBS (vtt files)
        "encrypted": sources_data.get('encrypted', False),
        "server": hostname
    }

# --- ROUTES ---

@app.route('/')
def home():
    return jsonify({
        "status": "Success",
        "message": "HiAnime API V2 (Python) is active",
        "endpoints": [
            "/search?keyword=one piece",
            "/anime/<id>",
            "/episodes/<id>",
            "/servers?episodeId=<id>",
            "/watch?episodeId=<id>&server=<server_name>"
        ]
    })

@app.route('/search')
def search():
    keyword = request.args.get('keyword')
    page = request.args.get('page', 1)
    if not keyword: return jsonify({"error": "No keyword provided"}), 400

    url = f"{BASE_URL}/search?keyword={urllib.parse.quote(keyword)}&page={page}"
    soup = get_soup(url)
    
    results = []
    if soup:
        for item in soup.select('.flw-item'):
            anchor = item.select_one('.film-name a')
            img = item.select_one('img')
            meta = item.select_one('.fdi-item') # type like TV/Movie
            
            if anchor and img:
                results.append({
                    "id": anchor['href'].replace('/', ''),
                    "title": anchor.text,
                    "url": f"{BASE_URL}{anchor['href']}",
                    "image": img.get('data-src', img.get('src')),
                    "type": meta.text.strip() if meta else "N/A"
                })
    
    return jsonify({"results": results, "page": page})

@app.route('/anime/<path:anime_id>')
def anime_info(anime_id):
    url = f"{BASE_URL}/{anime_id}"
    soup = get_soup(url)
    if not soup: return jsonify({"error": "Anime not found"}), 404
    
    info = {
        "id": anime_id,
        "title": soup.select_one('.anisc-detail .film-name').text.strip(),
        "description": soup.select_one('.film-description .text').text.strip(),
        "poster": soup.select_one('.film-poster img')['src']
    }
    
    # Extra details
    for item in soup.select('.anisc-info .item'):
        key = item.select_one('.item-head').text.replace(':', '').strip().lower()
        val = item.select_one('.name').text.strip() if item.select_one('.name') else item.text.replace(key, '').strip()
        info[key] = val
        
    return jsonify(info)

@app.route('/episodes/<path:anime_id>')
def episodes(anime_id):
    # We need the numeric ID first
    soup = get_soup(f"{BASE_URL}/{anime_id}")
    if not soup: return jsonify({"error": "Anime not found"}), 404
    
    movie_id = None
    # Try hidden input
    if soup.select_one('#movie_id'):
        movie_id = soup.select_one('#movie_id')['value']
    else:
        # Fallback: scrape from a wrapper attribute
        wrapper = soup.select_one('[data-id]')
        if wrapper: movie_id = wrapper['data-id']
        
    if not movie_id: return jsonify({"error": "Could not find internal Movie ID"}), 500
    
    ajax_url = f"{BASE_URL}/ajax/v2/episode/list/{movie_id}"
    data = get_ajax_json(ajax_url)
    
    ep_list = []
    if data and data.get('html'):
        ep_soup = BeautifulSoup(data['html'], 'html.parser')
        for link in ep_soup.select('.ssl-item-ep'):
            ep_list.append({
                "episodeId": link['data-id'], # CRITICAL: This ID is used for /servers
                "number": link['data-number'],
                "title": link['title'],
                "isFiller": "ssl-item-filler" in link.get('class', [])
            })
            
    return jsonify({"totalEpisodes": len(ep_list), "episodes": ep_list})

@app.route('/servers')
def servers():
    ep_id = request.args.get('episodeId')
    if not ep_id: return jsonify({"error": "episodeId required"}), 400
    
    url = f"{BASE_URL}/ajax/v2/episode/servers?episodeId={ep_id}"
    soup = get_soup(url) # This endpoint returns HTML, not JSON usually, or JSON with html field
    
    # Actually Hianime returns JSON with 'html'
    data = get_ajax_json(url)
    server_list = []
    
    if data and data.get('html'):
        s_soup = BeautifulSoup(data['html'], 'html.parser')
        for item in s_soup.select('.server-item'):
            server_list.append({
                "serverName": item.text.strip(),
                "serverId": item['data-id'], # CRITICAL: Used for /watch
                "type": item['data-type'].upper() # SUB or DUB
            })
            
    return jsonify({"episodeId": ep_id, "servers": server_list})

@app.route('/watch')
def watch():
    """
    The Master Endpoint: Gets Video Links + Subs
    """
    ep_id = request.args.get('episodeId')
    server_name = request.args.get('server', 'hd-1').lower() # Default to VidCloud (often named hd-1 or hd-2)
    
    if not ep_id: return jsonify({"error": "episodeId required"}), 400

    # 1. Get Server List to find the specific Server ID
    srv_url = f"{BASE_URL}/ajax/v2/episode/servers?episodeId={ep_id}"
    srv_data = get_ajax_json(srv_url)
    
    target_server_id = None
    
    if srv_data and srv_data.get('html'):
        s_soup = BeautifulSoup(srv_data['html'], 'html.parser')
        # Find ID for the requested server name (fuzzy match)
        for item in s_soup.select('.server-item'):
            name = item.text.strip().lower()
            if server_name in name or (server_name == "vidcloud" and "hd-1" in name):
                target_server_id = item['data-id']
                break
        
        # Fallback to first server if not found
        if not target_server_id and s_soup.select_one('.server-item'):
            target_server_id = s_soup.select_one('.server-item')['data-id']

    if not target_server_id:
        return jsonify({"error": "Server not found"}), 404

    # 2. Extract Streaming Data
    stream_data = extract_stream_link(target_server_id)
    return jsonify(stream_data)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
    
