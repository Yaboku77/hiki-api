from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import urllib.parse

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

BASE_URL = "https://hianime.to"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL
}

def get_soup(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def get_ajax_soup(url):
    # AJAX endpoints usually return JSON with an 'html' field
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        data = resp.json()
        if data.get('status'):
            return BeautifulSoup(data.get('html'), 'html.parser')
    except Exception as e:
        print(f"Error fetching AJAX {url}: {e}")
    return None

@app.route('/')
def home():
    return jsonify({
        "message": "HiAnime API in Python is Running",
        "routes": {
            "/search?keyword=...": "Search Anime",
            "/anime/<id>": "Anime Details",
            "/episodes/<id>": "Episode List",
            "/servers?episodeId=...": "Get Streaming Servers"
        }
    })

# --- 1. SEARCH ---
@app.route('/search')
def search():
    keyword = request.args.get('keyword')
    page = request.args.get('page', 1)
    if not keyword:
        return jsonify({"error": "Missing keyword"}), 400

    url = f"{BASE_URL}/search?keyword={urllib.parse.quote(keyword)}&page={page}"
    soup = get_soup(url)
    if not soup:
        return jsonify({"error": "Failed to fetch data"}), 500

    results = []
    # Logic to parse standard HiAnime search results
    for item in soup.select('.flw-item'):
        title = item.select_one('.film-name a').text if item.select_one('.film-name a') else "N/A"
        img = item.select_one('img')['data-src'] if item.select_one('img') else ""
        link = item.select_one('.film-name a')['href'] if item.select_one('.film-name a') else ""
        anime_id = link.split('/')[-1] if link else ""
        
        results.append({
            "id": anime_id,
            "title": title,
            "image": img,
            "url": f"{BASE_URL}{link}"
        })

    return jsonify({"results": results, "page": page})

# --- 2. ANIME DETAILS ---
@app.route('/anime/<path:anime_id>')
def anime_details(anime_id):
    url = f"{BASE_URL}/{anime_id}"
    soup = get_soup(url)
    if not soup:
        return jsonify({"error": "Failed to fetch anime details"}), 500

    title = soup.select_one('.anisc-detail .film-name')
    desc = soup.select_one('.film-description .text')
    poster = soup.select_one('.film-poster img')
    
    # Extract Info (Type, Status, etc.)
    info = {}
    for item in soup.select('.anisc-info .item'):
        key = item.select_one('.item-head').text.strip().replace(':', '') if item.select_one('.item-head') else "Unknown"
        value = item.select_one('.name').text.strip() if item.select_one('.name') else item.text.replace(key+':', '').strip()
        info[key] = value

    return jsonify({
        "id": anime_id,
        "title": title.text.strip() if title else "Unknown",
        "description": desc.text.strip() if desc else "",
        "poster": poster['src'] if poster else "",
        "info": info
    })

# --- 3. EPISODE LIST ---
@app.route('/episodes/<path:anime_id>')
def episodes(anime_id):
    # HiAnime loads episodes via AJAX. We need the numeric ID from the anime_id string (e.g., "one-piece-100" -> 100)
    # Usually strictly the data-id from the page, but let's try scraping it from the detail page first if needed.
    # However, standard URL convention often has the ID at the end.
    
    # If the user passed the full slug "one-piece-100", we assume the ID is the stored data-id on the page.
    # We first fetch the detail page to get the internal Movie/Show ID.
    detail_url = f"{BASE_URL}/{anime_id}"
    soup = get_soup(detail_url)
    if not soup:
         return jsonify({"error": "Anime not found"}), 404

    # The hidden input or wrapper often holds the ID
    movie_id_tag = soup.select_one('#wrapper')
    if not movie_id_tag:
         movie_id_tag = soup.select_one('input#movie_id') # fallback
         
    # Often stored in a hidden input or just extract from URL if format matches
    # HiAnime logic: /ajax/v2/episode/list/{id}
    # We scrape the 'data-id' from the "Watch Now" button or similar container.
    wrapper = soup.select_one('#watch-block')
    internal_id = None
    if wrapper:
        # Sometimes it's hard to find without JS, but usually in a hidden input
        # Fallback: Look for <input type="hidden" id="movie_id" value="...">
        hidden_input = soup.select_one('#movie_id')
        if hidden_input:
            internal_id = hidden_input['value']
    
    if not internal_id:
        # Try to guess from URL string (less reliable)
        try:
            internal_id = anime_id.split('-')[-1]
        except:
            return jsonify({"error": "Could not extract Anime ID"}), 500

    ajax_url = f"{BASE_URL}/ajax/v2/episode/list/{internal_id}"
    ep_soup = get_ajax_soup(ajax_url)
    
    episodes_list = []
    if ep_soup:
        for link in ep_soup.select('.ssl-item-ep'):
            episodes_list.append({
                "id": link['data-id'], # This is the Episode ID needed for servers
                "number": link['data-number'],
                "title": link['title'],
                "is_filler": "ssl-item-filler" in link.get("class", [])
            })

    return jsonify({"anime_id": anime_id, "total": len(episodes_list), "episodes": episodes_list})

# --- 4. SERVERS (For Streaming) ---
@app.route('/servers')
def servers():
    episode_id = request.args.get('episodeId')
    if not episode_id:
        return jsonify({"error": "Missing episodeId"}), 400
        
    ajax_url = f"{BASE_URL}/ajax/v2/episode/servers?episodeId={episode_id}"
    soup = get_ajax_soup(ajax_url)
    
    servers_list = []
    if soup:
        for item in soup.select('.server-item'):
            servers_list.append({
                "name": item.text.strip(),
                "id": item['data-id'],
                "type": item['data-type'] # sub or dub
            })
            
    return jsonify({"episode_id": episode_id, "servers": servers_list})

# WSGI Entry point for cPanel
if __name__ == '__main__':
    app.run(debug=True)
