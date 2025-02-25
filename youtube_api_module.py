from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import yt_dlp
import json
import os
from datetime import datetime
import re

SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

class QuotaConfirmationError(Exception):
    """Raised when user declines a high-quota operation."""
    pass

async def prompt_user(prompt_text):
    """Async wrapper for input function"""
    return input(prompt_text)

class SearchModel:
    """Represents a trained search model for a specific game type."""
    def __init__(self, game_type):
        self.game_type = game_type
        # Persistent model-level exclusions (generic patterns)
        self.persistent_exclusions = set()
        # Session-specific exclusions (cleared between searches)
        self.session_exclusions = set()
        self.trusted_channels = set()
        self.noise_channels = set()
        self.scoring_weights = {
            'title_match': 20,
            'view_count': 10,
            'like_ratio': 15,
            'trusted_channel': 15,
            'noise_channel': -10,
            'duration_match': 10,
            'context_match': 15,
            'recency': 10
        }
        self.duration_ranges = {
            'how_to_play': {'min': 5, 'max': 20},
            'review': {'min': 10, 'max': 30},
            'playthrough': {'min': 30, 'max': None}
        }
        
    def to_dict(self):
        """Convert model to dictionary for serialization."""
        return {
            'game_type': self.game_type,
            'persistent_exclusions': list(self.persistent_exclusions),
            'trusted_channels': list(self.trusted_channels),
            'noise_channels': list(self.noise_channels),
            'scoring_weights': self.scoring_weights,
            'duration_ranges': self.duration_ranges
        }
        
    @classmethod
    def from_dict(cls, data):
        """Create model from dictionary."""
        model = cls(data['game_type'])
        model.persistent_exclusions = set(data.get('persistent_exclusions', []))
        model.trusted_channels = set(data['trusted_channels'])
        model.noise_channels = set(data['noise_channels'])
        model.scoring_weights = data['scoring_weights']
        model.duration_ranges = data['duration_ranges']
        return model

    def add_exclusion(self, phrase, persistent=False):
        """Add an exclusion phrase. If persistent=True, save to model."""
        if persistent:
            self.persistent_exclusions.add(phrase.lower())
        else:
            self.session_exclusions.add(phrase.lower())
        
        # Save model after adding exclusion
        if persistent:
            self._save_model(self.game_type)

    def remove_exclusion(self, phrase, persistent=False):
        """Remove an exclusion phrase."""
        if persistent:
            self.persistent_exclusions.discard(phrase.lower())
            self._save_model(self.game_type)
        else:
            self.session_exclusions.discard(phrase.lower())

    def get_all_exclusions(self):
        """Get combined set of persistent and session exclusions."""
        return self.persistent_exclusions.union(self.session_exclusions)

    def clear_session_exclusions(self):
        """Clear temporary session-specific exclusions."""
        self.session_exclusions.clear()

    def add_trusted_channel(self, channel):
        """Add a trusted channel and save the model."""
        self.trusted_channels.add(channel)
        self.noise_channels.discard(channel)  # Remove from noise if present

    def add_noise_channel(self, channel):
        """Add a noise channel and save the model."""
        self.noise_channels.add(channel)
        self.trusted_channels.discard(channel)  # Remove from trusted if present

class YouTubeTools:
    SEARCH_PATTERNS = {
        'board': {
            'how_to_play': [
                '"{game_name}" "how to play"',
                '"{game_name}" rules explanation',
                '"{game_name}" tutorial board game',
                '"{game_name}" learn to play'
            ],
            'reviews': [
                '"{game_name}" review board game',
                '"{game_name}" review card game',
                '"{game_name}" board game overview',
                '"{game_name}" first impressions'
            ],
            'playthroughs': [
                '"{game_name}" playthrough board game',
                '"{game_name}" gameplay board game',
                '"{game_name}" full game',
                '"{game_name}" actual play'
            ]
        },
        'video': {
            'how_to_play': [
                '"{game_name}" beginners guide',
                '"{game_name}" tutorial',
                '"{game_name}" getting started',
                '"{game_name}" basics'
            ],
            'reviews': [
                '"{game_name}" review',
                '"{game_name}" worth playing',
                '"{game_name}" should you play',
                '"{game_name}" before you buy'
            ],
            'playthroughs': [
                '"{game_name}" full gameplay',
                '"{game_name}" walkthrough no commentary',
                '"{game_name}" longplay',
                '"{game_name}" complete game'
            ]
        }
    }

    def __init__(self):
        # Initialize YouTube API client and downloader
        self.youtube = self._authenticate()
        self.downloader = self._setup_downloader()
        self.session_quota_used = 0
        self.DAILY_QUOTA = 10000
        self.history_file = 'playlist_history.json'
        self._load_history()
        
        # Track last searched game for session management
        self._last_search_game = None
        
        # New: Channel classification data
        self.trusted_channels = {
            'board': set(),  # Board game trusted channels
            'video': set()   # Video game trusted channels
        }
        self.noise_channels = {
            'board': set(),  # Known noisy channels for board games
            'video': set()   # Known noisy channels for video games
        }
        
        # New: Context exclusion patterns learned from feedback
        self.learned_exclusions = {
            'board': set(),  # Words that indicate wrong context for board games
            'video': set()   # Words that indicate wrong context for video games
        }
        
        # New: Load learned data if exists
        self._load_learned_data()
        
        # Load or create models
        self.models = {
            'board': self._load_model('board'),
            'video': self._load_model('video')
        }
        
    def _authenticate(self):
        # Try to load existing credentials from token.json
        creds = None
        if os.path.exists('token.json'):
            try:
                creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            except Exception:
                # If token file is corrupted or invalid, remove it
                os.remove('token.json')
                creds = None
        
        # If no valid credentials found, either refresh or create new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    # If refresh fails, remove token and start fresh auth flow
                    print("Token expired. Starting new authorization flow...")
                    if os.path.exists('token.json'):
                        os.remove('token.json')
                    # Start fresh OAuth flow
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    creds = flow.run_local_server(port=0)
            else:
                # Start OAuth flow using credentials.json
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Save the credentials for future use
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
        
        return build('youtube', 'v3', credentials=creds)

    def _setup_downloader(self):
        ydl_opts = {
            # Format selection prioritizes:
            # 1. MP4 video (<=1080p) + M4A audio
            # 2. Any video (<=1080p) + audio
            # 3. Best combined format (<=1080p)
            'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]',
            
            # Format sorting preferences
            'format_sort': ['res:1080', 'res:720'],
            
            # Merge video and audio automatically
            'merge_output_format': 'mp4',
            
            # Metadata options
            'writethumbnail': True,
            'writesubtitles': True,
            'subtitleslangs': ['en'],
            'embedthumbnail': True,
            'embedsubtitles': True,
            
            # Output template
            'outtmpl': '%(title)s [%(resolution)s].%(ext)s',
            
            # Show progress
            'quiet': False,
            'no_warnings': False,
            'ignoreerrors': True,
            
            # Post-processing
            'postprocessors': [
                {'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mp4'},
                {'key': 'EmbedThumbnail'},
                {'key': 'FFmpegEmbedSubtitle'},
            ]
        }
        return yt_dlp.YoutubeDL(ydl_opts)

    def extract_playlist_id(self, url_or_id):
        """Extract playlist ID from various YouTube URL formats or return the ID if already clean."""
        if 'list=' in url_or_id:
            return url_or_id.split('list=')[1].split('&')[0]
        return url_or_id

    async def get_playlist_items(self, playlist_id, channel_id=None):
        # Handles YouTube's pagination system (max 50 items per request)
        items = []
        next_page_token = None
        
        while True:
            # Fetch batch of up to 50 items
            clean_id = self.extract_playlist_id(playlist_id)
            request = self.youtube.playlistItems().list(
                part='snippet',
                playlistId=clean_id,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            
            # Filter by channel if specified
            playlist_items = response['items']
            if channel_id:
                playlist_items = [
                    item for item in playlist_items 
                    if item['snippet']['videoOwnerChannelId'] == channel_id
                ]
            
            items.extend(playlist_items)
            next_page_token = response.get('nextPageToken')
            
            if not next_page_token:
                break
        
        return items

    async def add_video_to_playlist(self, playlist_id, video_id):
        # Creates a new playlist item linking the video to the playlist
        clean_id = self.extract_playlist_id(playlist_id)
        request = self.youtube.playlistItems().insert(
            part='snippet',
            body={
                'snippet': {
                    'playlistId': clean_id,
                    'resourceId': {
                        'kind': 'youtube#video',
                        'videoId': video_id
                    }
                }
            }
        )
        return request.execute()

    async def download_playlist(self, playlist_id, output_dir=None):
        # Create output directory if specified and change to it
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            os.chdir(output_dir)

        # Get all videos in playlist and download each one
        items = await self.get_playlist_items(playlist_id)
        for item in items:
            video_id = item['snippet']['resourceId']['videoId']
            url = f'https://www.youtube.com/watch?v={video_id}'
            try:
                self.downloader.download([url])
            except Exception as e:
                print(f"Error downloading {url}: {e}")

    async def is_video_in_playlist(self, playlist_id, video_id):
        # Checks if a video already exists in the playlist to prevent duplicates
        items = await self.get_playlist_items(playlist_id)
        return any(
            item['snippet']['resourceId']['videoId'] == video_id 
            for item in items
        )

    async def get_video_details(self, video_id):
        # Fetches metadata for a single video
        try:
            request = self.youtube.videos().list(
                part='snippet',
                id=video_id
            )
            response = request.execute()
            if response['items']:
                return response['items'][0]
            return None
        except Exception as e:
            print(f"Error fetching video details: {e}")
            return None

    async def get_my_playlists(self):
        """Fetches all playlists owned by the authenticated user, sorted by most recent first."""
        playlists = []
        next_page_token = None
        
        try:
            while True:
                # Fetch batch of playlists (max 50 per request)
                request = self.youtube.playlists().list(
                    part='snippet,contentDetails',
                    mine=True,
                    maxResults=50,
                    pageToken=next_page_token
                )
                response = request.execute()
                
                for playlist in response['items']:
                    playlists.append({
                        'id': playlist['id'],
                        'title': playlist['snippet']['title'],
                        'video_count': playlist['contentDetails']['itemCount'],
                        'created_at': playlist['snippet']['publishedAt']
                    })
                
                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break
            
            # Sort by creation date, newest first
            playlists.sort(key=lambda x: x['created_at'], reverse=True)
            return playlists
            
        except Exception as e:
            print(f"Error fetching playlists: {e}")
            return None

    async def delete_playlist(self, playlist_id):
        """Deletes a playlist owned by the authenticated user."""
        try:
            clean_id = self.extract_playlist_id(playlist_id)
            request = self.youtube.playlists().delete(
                id=clean_id
            )
            request.execute()
            return True
        except Exception as e:
            print(f"Error deleting playlist: {e}")
            return False

    async def create_playlist(self, title, description=""):
        """Creates a new playlist and returns its ID."""
        try:
            request = self.youtube.playlists().insert(
                part="snippet",
                body={
                    "snippet": {
                        "title": title,
                        "description": description
                    }
                }
            )
            response = request.execute()
            return response['id']
        except Exception as e:
            print(f"Error creating playlist: {e}")
            return None

    async def remove_video_from_playlist(self, playlist_id, item_id):
        """Remove a video from a playlist."""
        try:
            clean_id = self.extract_playlist_id(playlist_id)
            request = self.youtube.playlistItems().delete(
                id=item_id
            )
            request.execute()
            return True
        except Exception as e:
            print(f"Error removing video: {e}")
            return False

    def _parse_duration_filter(self, filter_str):
        """Parse duration filter string into min/max minutes."""
        if not filter_str:
            return None, None
        
        # Handle hyphen format (e.g., "60-120")
        if '-' in filter_str and ' ' not in filter_str:
            try:
                min_duration, max_duration = map(int, filter_str.split('-'))
                return min_duration, max_duration
            except ValueError:
                pass
            
        # Handle original format (e.g., ">=60 <=120")
        parts = filter_str.split()
        min_duration = max_duration = None
        
        for part in parts:
            if part.startswith('>='):
                min_duration = int(part[2:])
            elif part.startswith('<='):
                max_duration = int(part[2:])
            
        return min_duration, max_duration

    def _parse_iso_duration(self, duration):
        """Convert YouTube's ISO 8601 duration to minutes."""
        import re
        import isodate
        
        return int(isodate.parse_duration(duration).total_seconds() / 60)

    def _track_quota(self, points, operation_name="API call"):
        self.session_quota_used += points
        remaining = self.DAILY_QUOTA - self.session_quota_used
        
        if points > 100:
            print(f"\nHigh-quota operation: {operation_name} will use {points} points")
            confirm = input("Continue? (y/n): ").lower()
            if confirm != 'y':
                raise QuotaConfirmationError("Operation cancelled by user")
                
        if remaining < 1000:
            print(f"\n⚠️ Warning: Used {self.session_quota_used} points in this session")
            
    def get_quota_status(self):
        remaining = self.DAILY_QUOTA - self.session_quota_used
        return {
            'used': self.session_quota_used,
            'remaining': remaining,
            'total': self.DAILY_QUOTA,
            'percent_used': (self.session_quota_used / self.DAILY_QUOTA) * 100
        }
        
    async def advanced_search(self, query, resource_type='video', order='relevance', duration_filter=None, max_results=5, light_mode=True):
        try:
            # Estimate initial quota cost
            base_cost = 100  # Search operation
            estimated_cost = base_cost
            
            if not light_mode:
                estimated_details_cost = max_results
                estimated_cost += estimated_details_cost
                
            self._track_quota(estimated_cost, "Advanced search")
            
            min_duration, max_duration = self._parse_duration_filter(duration_filter)
            
            # Build search parameters
            params = {
                'q': query,
                'maxResults': max_results,  # Only request what we need
                'type': resource_type if resource_type else 'video,playlist',
                'order': order,
                'part': 'snippet',
                'relevanceLanguage': 'en'  # Add language preference for better results
            }
            
            print(f"\nDebug: YouTube API search parameters:")
            print(f"Query: {params['q']}")
            print(f"Type: {params['type']}")
            print(f"Order: {params['order']}")
            print(f"Max Results: {params['maxResults']}")
            
            # Execute search
            request = self.youtube.search().list(**params)
            response = request.execute()
            
            # For each video, get additional details
            detailed_results = []
            for item in response.get('items', []):
                # Get the type and ID from the response
                content_type = item['id']['kind'].split('#')[1]  # 'youtube#video' or 'youtube#playlist'
                content_id = item['id'].get('videoId') or item['id'].get('playlistId')
                
                if not content_id:
                    continue
                    
                result = {
                    'id': content_id,
                    'type': content_type,  # Add explicit type field
                    'url': f'https://www.youtube.com/watch?v={content_id}' if content_type == 'video' else f'https://www.youtube.com/playlist?list={content_id}',
                    'title': item['snippet']['title'],
                    'channel_title': item['snippet']['channelTitle'],
                    'upload_date': item['snippet']['publishedAt']
                }
                
                # Get additional details based on type
                if content_type == 'video':
                    video_response = self.youtube.videos().list(
                        part='statistics,contentDetails',
                        id=content_id
                    ).execute()
                    
                    if video_response['items']:
                        video_details = video_response['items'][0]
                        result.update({
                            'duration': self._format_duration(video_details['contentDetails']['duration']),
                            'view_count': int(video_details['statistics'].get('viewCount', 0)),
                            'like_count': int(video_details['statistics'].get('likeCount', 0))
                        })
                else:  # playlist
                    playlist_response = self.youtube.playlists().list(
                        part='contentDetails',
                        id=content_id
                    ).execute()
                    
                    if playlist_response['items']:
                        result.update({
                            'video_count': playlist_response['items'][0]['contentDetails']['itemCount']
                        })
                
                detailed_results.append(result)
                
                if len(detailed_results) >= max_results:
                    break
                
            return detailed_results
            
        except Exception as e:
            print(f"Error in advanced search: {e}")
            return None

    def _format_duration(self, duration_iso):
        """Convert ISO 8601 duration to readable format."""
        match = re.match(r'PT(\d+H)?(\d+M)?(\d+S)?', duration_iso)
        if not match:
            return 'Unknown'
        
        hours = match.group(1)[:-1] if match.group(1) else 0
        minutes = match.group(2)[:-1] if match.group(2) else 0
        seconds = match.group(3)[:-1] if match.group(3) else 0
        
        parts = []
        if int(hours):
            parts.append(f"{hours}h")
        if int(minutes):
            parts.append(f"{minutes}m")
        if int(seconds):
            parts.append(f"{seconds}s")
        
        return " ".join(parts)

    def _load_history(self):
        """Load playlist history from file"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r') as f:
                    self.playlist_history = json.load(f)
            else:
                self.playlist_history = []
        except:
            self.playlist_history = []

    def _save_history(self):
        """Save playlist history to file"""
        try:
            with open(self.history_file, 'w') as f:
                json.dump(self.playlist_history, f)
            print(f"Debug: Saved {len(self.playlist_history)} items to history")  # Temporary debug line
        except Exception as e:
            print(f"Debug: Failed to save history: {e}")  # Temporary debug line
            pass

    def add_to_history(self, playlist_id, title):
        """Add playlist to history, maintaining uniqueness and limiting size"""
        clean_id = self.extract_playlist_id(playlist_id)
        print(f"Debug: Adding playlist to history: {title} ({clean_id})")  # Temporary debug line
        # Remove if already exists
        self.playlist_history = [p for p in self.playlist_history if p['id'] != clean_id]
        # Add to front of list
        self.playlist_history.insert(0, {
            'id': clean_id,
            'title': title,
            'last_used': datetime.now().isoformat()
        })
        # Keep only last 10 items
        self.playlist_history = self.playlist_history[:10]
        self._save_history()

    def display_results(self, results, category_name=None):
        """Display search results with detailed information."""
        if not results:
            print(f"\nNo {category_name} results found.")
            return ""

        # Print appropriate header based on category
        print(f"\nAvailable {category_name}:")
        
        for i, item in enumerate(results, 1):
            duration = item.get('duration', 'Unknown')
            views = item.get('view_count', 0)
            likes = item.get('like_count', 0)
            ratio = f"{(likes / views * 100):.1f}%" if views > 0 else 'N/A'
            channel_subs = item.get('channel_subscriber_count', 0)
            upload_date = item.get('upload_date', 'Unknown')
            
            # Different display format for playlists vs videos
            if item.get('type') == 'playlist':
                video_count = item.get('video_count', 'Unknown')
                print(f"\n{i}. [PLAYLIST] {item['title']}")
                print(f"   Channel: {item['channel_title']} ({channel_subs:,} subscribers)")
                print(f"   Videos: {video_count} | Views: {views:,} | Created: {upload_date}")
            else:
                print(f"\n{i}. {item['title']}")
                print(f"   Channel: {item['channel_title']} ({channel_subs:,} subscribers)")
                print(f"   Duration: {duration} | Views: {views:,} | Like ratio: {ratio} | Uploaded: {upload_date}")
            
            print(f"   \033[34m{item['url']}\033[0m")  # Blue clickable link
            print("   " + "-" * 50)

        while True:
            print("\nTip: Enter numbers/ranges separated by commas (e.g., '1,3' or '1-3' or '1,2-4')")
            selection = input(f"Select {category_name.lower()} to add (or press Enter to skip): ")
            
            # Allow empty selection to skip
            if not selection.strip():
                return ""
            
            try:
                # Validate all numbers are within range
                all_nums = []
                for part in selection.split(','):
                    part = part.strip()
                    if '-' in part:
                        start, end = map(int, part.split('-'))
                        all_nums.extend(range(start, end + 1))
                    else:
                        all_nums.append(int(part))
                    
                # Validate indices
                if any(i < 1 or i > len(results) for i in all_nums):
                    print("Invalid selection. Please use numbers within range.")
                    continue
                    
                return selection  # Return the raw selection string
                
            except ValueError:
                print("Invalid input format. Please use numbers and ranges (e.g., '1,3' or '1-3')")

    async def generate_gameplay_playlist(self):
        print("\n=== Gameplay Guide Generator ===")
        
        # Get game type first
        print("\nGame type:")
        print("1. Video Game")
        print("2. Board Game")
        
        game_type = await prompt_user("\nChoose type (1-2): ")
        if game_type not in ['1', '2']:
            print("Invalid choice.")
            return
            
        game_type_str = "board" if game_type == '2' else "video"
        
        # Get game name
        game_name = await prompt_user("\nEnter game name: ")
        if not game_name.strip():
            print("Game name cannot be empty")
            return

        # Load the model and handle session management
        model = self.models[game_type_str]
        if self._last_search_game != game_name:
            print("\nNew game detected, clearing session-specific exclusions...")
            model.clear_session_exclusions()
            self._last_search_game = game_name
        else:
            print("\nContinuing with existing session exclusions for", game_name)

        # Show current model state that will be applied
        print("\nUsing model settings:")
        if model.persistent_exclusions:
            print("Persistent exclusions:", ', '.join(model.persistent_exclusions))
        if model.session_exclusions:
            print("Session exclusions:", ', '.join(model.session_exclusions))
        if model.trusted_channels:
            print("Trusted channels:", ', '.join(model.trusted_channels))
        if model.noise_channels:
            print("Noise channels:", ', '.join(model.noise_channels))

        # 1. How to Play videos
        print(f"\nSearching for tutorials...")
        tutorial_results = await self.search_videos(
            game_name=game_name,
            game_type=game_type_str,
            category='how_to_play'
        )
        
        print("\n=== How to Play Videos ===")
        formatted_tutorials = [video for video, score in tutorial_results]
        selected_tutorials = self.display_results(formatted_tutorials, "How to Play Videos")
        
        # 2. Tips & Tricks videos
        print(f"\nSearching for tips & tricks...")
        tips_results = await self.search_videos(
            game_name=game_name,
            game_type=game_type_str,
            category='reviews'
        )
        
        print("\n=== Tips & Tricks Videos ===")
        formatted_tips = [video for video, score in tips_results]
        selected_tips = self.display_results(formatted_tips, "Tips & Tricks")
        
        # 3. Playthrough videos/playlists
        print(f"\nSearching for playthroughs...")
        playthrough_results = await self.search_videos(
            game_name=game_name,
            game_type=game_type_str,
            category='playthroughs'
        )
        
        print("\n=== Playthroughs ===")
        formatted_playthroughs = [video for video, score in playthrough_results]
        selected_playthroughs = self.display_results(formatted_playthroughs, "Playthroughs")
        
        # Create new playlist
        playlist_title = f"{game_name} - Complete Guide"
        print(f"\nCreating playlist: {playlist_title}")
        
        new_playlist_id = await self.create_playlist(
            title=playlist_title,
            description=f"Curated gameplay guide for {game_name}\n\n" +
                       f"Type: {game_type_str.title()} Game\n\n" +
                       "Includes:\n- How to Play tutorials\n- Tips & Tricks\n- Full Playthroughs"
        )
        
        if not new_playlist_id:
            print("Failed to create playlist")
            return
        
        # Add selected videos in order
        print("\nAdding selected videos to playlist...")
        added = 0
        added_video_ids = set()
        
        # Helper function to add videos/playlists
        async def add_content(selected_indices, results, section_name):
            nonlocal added, added_video_ids
            
            if not selected_indices or not selected_indices.strip():
                print(f"No videos selected from {section_name}")
                return
            
            indices = []
            for part in selected_indices.split(','):
                part = part.strip()
                if not part:
                    continue
                if '-' in part:
                    start, end = map(int, part.split('-'))
                    indices.extend(range(start, end + 1))
                else:
                    indices.append(int(part))
            
            for idx in indices:
                item = results[idx - 1]
                try:
                    if item.get('type') == 'playlist':
                        print(f"\nAdding playlist: {item['title']}")
                        playlist_items = await self.get_playlist_items(item['id'])
                        for video in playlist_items:
                            video_id = video['snippet']['resourceId']['videoId']
                            if video_id not in added_video_ids:
                                await self.add_video_to_playlist(new_playlist_id, video_id)
                                print(f"Added: {video['snippet']['title']}")
                                added_video_ids.add(video_id)
                                added += 1
                            else:
                                print(f"Skipped duplicate: {video['snippet']['title']}")
                    else:
                        if item['id'] not in added_video_ids:
                            await self.add_video_to_playlist(new_playlist_id, item['id'])
                            print(f"Added: {item['title']}")
                            added_video_ids.add(item['id'])
                            added += 1
                        else:
                            print(f"Skipped duplicate: {item['title']}")
                except Exception as e:
                    print(f"Error adding content: {e}")
                    continue
        
        # Add videos in sections
        print("\nAdding How to Play videos...")
        await add_content(selected_tutorials, formatted_tutorials, "How to Play")
        
        print("\nAdding Tips & Tricks videos...")
        await add_content(selected_tips, formatted_tips, "Tips & Tricks")
        
        print("\nAdding Playthroughs...")
        await add_content(selected_playthroughs, formatted_playthroughs, "Playthroughs")
        
        print(f"\nSuccess! Created playlist '{playlist_title}' with {added} videos")
        
        # Add to history
        self.add_to_history(new_playlist_id, playlist_title)

    def filter_irrelevant_results(self, videos, game_name, game_type):
        """Pre-filter obviously irrelevant results using generic patterns."""
        
        # Generic patterns that suggest the video isn't about the game itself
        GENERIC_EXCLUSIONS = {
            'board': [
                'unboxing only',
                'collection video',
                'lot for sale',
                'printing',
                'manufacturing'
            ],
            'video': [
                'reaction video',
                'game bundle',
                'price guide',
                'collection video'
            ]
        }

        filtered = []
        generic_exclusions = GENERIC_EXCLUSIONS[game_type]
        
        for video in videos:
            # Check title and description for generic exclusion patterns
            lower_title = video['title'].lower()
            # Get description from snippet if available, otherwise use empty string
            lower_desc = video.get('snippet', {}).get('description', '').lower()
            
            # Skip if it matches generic exclusion patterns
            if any(pattern in lower_title or pattern in lower_desc 
                   for pattern in generic_exclusions):
                continue
            
            filtered.append(video)
        
        return filtered

    def _load_learned_data(self):
        """Load previously learned channel and exclusion data."""
        try:
            if os.path.exists('learned_data.json'):
                with open('learned_data.json', 'r') as f:
                    data = json.load(f)
                    self.trusted_channels = {k: set(v) for k, v in data.get('trusted_channels', {}).items()}
                    self.noise_channels = {k: set(v) for k, v in data.get('noise_channels', {}).items()}
                    self.learned_exclusions = {k: set(v) for k, v in data.get('learned_exclusions', {}).items()}
        except Exception as e:
            print(f"Error loading learned data: {e}")

    def _save_learned_data(self):
        """Save learned channel and exclusion data."""
        try:
            data = {
                'trusted_channels': {k: list(v) for k, v in self.trusted_channels.items()},
                'noise_channels': {k: list(v) for k, v in self.noise_channels.items()},
                'learned_exclusions': {k: list(v) for k, v in self.learned_exclusions.items()}
            }
            with open('learned_data.json', 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving learned data: {e}")

    async def detect_false_contexts(self, game_name, game_type, training_mode=False):
        """Perform initial search to detect irrelevant contexts that should be excluded."""
        # Do a broad search but include game type for better initial results
        initial_query = f'"{game_name}" {game_type} game'
        results = await self.advanced_search(initial_query, max_results=15)
        
        if not results:
            return []

        # Analyze titles and descriptions for common patterns
        phrase_counts = {}
        channel_counts = {}
        
        if training_mode:
            print("\nAnalyzing initial results for false contexts...")
            
        for video in results:
            # Get text from title and description
            text = f"{video['title']} {video.get('snippet', {}).get('description', '')}"
            text = text.lower()
            
            # Track channel frequencies
            channel = video['channel_title']
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
            
            if training_mode:
                print(f"\nAnalyzing video: {video['title']}")
                print(f"Channel: {channel}")
            
            # Extract phrases (1-3 words) and count frequencies
            words = text.split()
            for i in range(len(words)):
                for phrase_len in range(1, 4):  # Look for 1-3 word phrases
                    if i + phrase_len <= len(words):
                        phrase = ' '.join(words[i:i+phrase_len])
                        if len(phrase) > 3:  # Ignore very short phrases
                            phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1
                            if training_mode and phrase_counts[phrase] >= 3:
                                print(f"Frequent phrase found: '{phrase}' (count: {phrase_counts[phrase]})")

        # Find phrases that frequently appear with the game name but likely indicate wrong context
        game_words = set(game_name.lower().split())
        exclusions = []
        
        # First, add learned exclusions
        exclusions.extend(self.learned_exclusions[game_type])
        
        for phrase, count in phrase_counts.items():
            # Skip if it's part of the game name
            if any(word in game_words for word in phrase.split()):
                continue
            
            # If phrase appears in multiple videos and isn't a common gaming term
            if count >= 3 and not any(term in phrase for term in {'game', 'play', 'review', 'tutorial', 'guide', 'gameplay'}):
                # Check if it's a game-specific term
                if game_type == 'board':
                    if any(term in phrase for term in {'fortnite', 'minecraft', 'playstation', 'xbox', 'nintendo', 'steam', 'dlc', 'mod'}) or phrase in self.learned_exclusions['board']:
                        exclusions.append(phrase)
                        if training_mode:
                            print(f"Excluding '{phrase}' (appeared {count} times, known video game term)")
                else:  # video game
                    if any(term in phrase for term in {'board', 'card', 'tabletop', 'boardgame', 'cardgame', 'dice'}) or phrase in self.learned_exclusions['video']:
                        exclusions.append(phrase)
                        if training_mode:
                            print(f"Excluding '{phrase}' (appeared {count} times, known board game term)")

        # Analyze channels
        if training_mode:
            print("\nChannel analysis:")
            channels_to_show = []
            for channel, count in channel_counts.items():
                status = []
                if channel in self.trusted_channels[game_type]:
                    status.append("TRUSTED")
                if channel in self.noise_channels[game_type]:
                    status.append("NOISE")
                status_str = f" ({', '.join(status)})" if status else ""
                channels_to_show.append((channel, count, status_str))
            
            # Sort channels by frequency
            channels_to_show.sort(key=lambda x: x[1], reverse=True)
            
            # Display channels with numbers for selection
            for i, (channel, count, status_str) in enumerate(channels_to_show, 1):
                print(f"{i}. {channel}: {count} videos{status_str}")
            
            # Allow immediate channel classification
            while True:
                action = await prompt_user("\nEnter channel number to whitelist, 'n' for noise channels, or press Enter to continue: ")
                if not action:
                    break
                    
                if action.lower() == 'n':
                    noise_num = await prompt_user("Enter channel number to mark as noise (or Enter to skip): ")
                    if noise_num:
                        try:
                            idx = int(noise_num) - 1
                            if 0 <= idx < len(channels_to_show):
                                channel = channels_to_show[idx][0]
                                if channel not in self.noise_channels[game_type]:
                                    self.models[game_type].add_noise_channel(channel)
                                    print(f"Added noise channel: {channel}")
                        except ValueError:
                            print("Invalid input. Please enter a number.")
                else:
                    try:
                        idx = int(action) - 1
                        if 0 <= idx < len(channels_to_show):
                            channel = channels_to_show[idx][0]
                            if channel not in self.trusted_channels[game_type]:
                                self.models[game_type].add_trusted_channel(channel)
                                print(f"Added trusted channel: {channel}")
                    except ValueError:
                        print("Invalid input. Please enter a number.")

        return list(set(exclusions))  # Remove duplicates

    def add_trusted_channel(self, channel_name, game_type):
        """Add a channel to the trusted list for a game type."""
        self.trusted_channels[game_type].add(channel_name)
        # Remove from noise list if present
        self.noise_channels[game_type].discard(channel_name)
        self._save_learned_data()

    def add_noise_channel(self, channel_name, game_type):
        """Add a channel to the noise list for a game type."""
        self.noise_channels[game_type].add(channel_name)
        # Remove from trusted list if present
        self.trusted_channels[game_type].discard(channel_name)
        self._save_learned_data()

    def add_exclusion_word(self, word, game_type):
        """Add a word to the learned exclusions for a game type."""
        # Add to model's persistent exclusions
        self.models[game_type].add_exclusion(word, persistent=True)
        # Save the model
        self._save_model(game_type)
        
        # Also add to learned data for compatibility
        self.learned_exclusions[game_type].add(word.lower())
        self._save_learned_data()

    def remove_exclusion_word(self, word, game_type):
        """Remove a word from the learned exclusions for a game type."""
        # Remove from model's persistent exclusions
        self.models[game_type].remove_exclusion(word, persistent=True)
        # Save the model
        self._save_model(game_type)
        
        # Also remove from learned data for compatibility
        self.learned_exclusions[game_type].discard(word.lower())
        self._save_learned_data()

    async def training_search(self, game_name, game_type):
        """Perform a search with detailed output for training purposes."""
        print(f"\n=== Training Search for '{game_name}' ({game_type} game) ===")
        
        # Use existing model from self.models
        model = self.models[game_type]
        
        # Build base query
        base_query = f'"{game_name}" {game_type} game'
        
        # Build search query with exclusions
        search_parts = [base_query]
        
        # Add exclusions to query
        exclusions = model.get_all_exclusions()
        if exclusions:
            # YouTube API syntax for exclusions is -word
            exclusion_terms = [f'-"{phrase}"' for phrase in exclusions]
            search_parts.extend(exclusion_terms)
            print(f"\nApplying exclusions: {', '.join(exclusions)}")
        
        # Add trusted channels to query if any
        trusted_channels = model.trusted_channels
        if trusted_channels:
            # YouTube API syntax for channel filter
            channel_parts = [f'channel:"{channel}"' for channel in trusted_channels]
            if channel_parts:
                channel_query = ' | '.join(channel_parts)  # YouTube API uses | for OR
                search_parts.append(f'({channel_query})')
                print(f"\nPrioritizing trusted channels: {', '.join(trusted_channels)}")
        
        # Combine all parts into final query
        final_query = ' '.join(search_parts)
        print(f"\nUsing refined query: {final_query}")
        
        results = await self.advanced_search(final_query, max_results=20)
        
        if not results:
            print("No results found")
            return
        
        # Filter out noise channels post-query
        results = [r for r in results if r['channel_title'] not in model.noise_channels]
        
        print("\nResults with scoring breakdown:")
        for i, result in enumerate(results, 1):
            print(f"\n{i}. {result['title']}")
            print(f"   Channel: {result['channel_title']}")
            print(f"   URL: {result['url']}")
            
            # Score breakdown
            score = 0
            print("   Scoring factors:")
            
            # Title match
            if re.search(rf'\b{re.escape(game_name)}\b', result['title'], re.IGNORECASE):
                print("   + Title exact match: +20")
                score += 20
                
            # View count
            views = result.get('view_count', 0)
            if views > 1000:
                view_points = min(10, views // 1000)
                print(f"   + Views ({views:,}): +{view_points}")
                score += view_points
                
            # Channel trust/noise status
            channel = result['channel_title']
            if channel in model.trusted_channels:
                print("   + Trusted channel: +15")
                score += 15
            elif channel in model.noise_channels:
                print("   - Noise channel: -10")
                score -= 10
                
            # Duration appropriateness
            if 'duration' in result:
                duration_str = result['duration']
                minutes = sum(x * int(t) for x, t in zip([60, 1], re.findall(r'(\d+)[hm]', duration_str)))
                print(f"   Duration: {duration_str}")
                
            print(f"   Final score: {score}")
            
        return results

    async def search_videos(self, game_name, game_type, category):
        """Search with pre-filtering and increased results."""
        print(f"\nDebug: Starting search for {category} content")
        
        # Get the model and its exclusions
        model = self.models[game_type]
        model_exclusions = model.get_all_exclusions()
        
        all_results = []
        patterns = self.SEARCH_PATTERNS[game_type][category]
        
        print(f"\nDebug: Using {len(patterns)} search patterns for {category}")
        
        for query_template in patterns:
            # Build search parts
            search_parts = [query_template.format(game_name=game_name)]
            
            # Add model exclusions to query
            if model_exclusions:
                exclusion_terms = [f'-"{phrase}"' for phrase in model_exclusions]
                search_parts.extend(exclusion_terms)
                print(f"\nApplying exclusions: {', '.join(model_exclusions)}")
            
            # Add trusted channels to query if any
            if model.trusted_channels:
                channel_parts = [f'channel:"{channel}"' for channel in model.trusted_channels]
                if channel_parts:
                    channel_query = ' | '.join(channel_parts)  # YouTube API uses | for OR
                    search_parts.append(f'({channel_query})')
                    print(f"\nPrioritizing trusted channels: {', '.join(model.trusted_channels)}")
            
            # Combine all parts into final query
            formatted_query = ' '.join(search_parts)
            print(f"\nDebug: Trying search pattern: {formatted_query}")
            
            # Request more results since we'll be filtering some out
            results = await self.advanced_search(formatted_query, max_results=15)
            
            if not results:
                print("Debug: No results found for this pattern")
                continue
            
            print(f"Debug: Found {len(results)} initial results")
            
            # Filter out noise channels
            filtered_results = [r for r in results if r['channel_title'] not in model.noise_channels]
            print(f"Debug: {len(filtered_results)} results remained after filtering")
            
            # Score and store results
            scored_results = [
                (video, self.score_video(video, game_type, game_name))
                for video in filtered_results
            ]
            
            # Print top scores for this pattern
            top_scores = sorted(scored_results, key=lambda x: x[1], reverse=True)[:3]
            print("\nDebug: Top 3 scores for this pattern:")
            for video, score in top_scores:
                print(f"- {video['title'][:50]}... (Score: {score})")
            
            all_results.extend(scored_results)
        
        # Remove duplicates (same video from different queries)
        unique_results = {v[0]['id']: v for v in all_results}.values()
        
        # Sort by score and take top 10
        final_results = sorted(unique_results, key=lambda x: x[1], reverse=True)[:10]
        print(f"\nDebug: Final result count: {len(final_results)}")
        
        return final_results

    def score_video(self, video, game_type, game_name):
        """Score a video based on relevance to the game."""
        score = 0
        
        # Base score from title match
        if re.search(rf'\b{re.escape(game_name)}\b', video['title'], re.IGNORECASE):
            score += 20
        
        # View count bonus (if available)
        views = video.get('view_count', 0)
        if views > 1000:
            score += min(10, views // 1000)  # Up to 10 points for views
        
        # Like ratio bonus (if available)
        if views > 0 and 'like_count' in video:
            like_ratio = video['like_count'] / views
            score += min(15, int(like_ratio * 100))  # Up to 15 points for good like ratio
        
        # Duration appropriateness (if available)
        if 'duration' in video:
            duration_str = video['duration']
            # Convert duration string to minutes
            minutes = sum(x * int(t) for x, t in zip([60, 1], re.findall(r'(\d+)[hm]', duration_str)))
            
            # Different ideal durations for different content types
            if minutes:  # Only if we successfully parsed duration
                if 'how to play' in video['title'].lower():
                    # Tutorial videos: 5-20 minutes ideal
                    if 5 <= minutes <= 20:
                        score += 10
                elif 'review' in video['title'].lower():
                    # Reviews: 10-30 minutes ideal
                    if 10 <= minutes <= 30:
                        score += 10
                elif any(x in video['title'].lower() for x in ['playthrough', 'gameplay']):
                    # Playthroughs: 30+ minutes ideal
                    if minutes >= 30:
                        score += 10
        
        # Description context bonus
        desc_lower = video.get('description', '').lower()
        if f'{game_type} game' in desc_lower:
            score += 15
        
        # Recent video bonus (if upload date available)
        if 'upload_date' in video:
            try:
                upload_date = datetime.fromisoformat(video['upload_date'].replace('Z', '+00:00'))
                age_days = (datetime.now() - upload_date).days
                if age_days < 365:  # Videos less than a year old
                    score += min(10, (365 - age_days) // 36)  # Up to 10 points for recency
            except (ValueError, TypeError):
                pass
        
        return score

    def _load_model(self, game_type):
        """Load a model from file or create new if not exists."""
        model_file = f'model_{game_type}.json'
        try:
            if os.path.exists(model_file):
                with open(model_file, 'r') as f:
                    data = json.load(f)
                    return SearchModel.from_dict(data)
        except Exception as e:
            print(f"Error loading {game_type} model: {e}")
        
        return SearchModel(game_type)

    def _save_model(self, game_type):
        """Save a model to file."""
        model_file = f'model_{game_type}.json'
        try:
            with open(model_file, 'w') as f:
                json.dump(self.models[game_type].to_dict(), f, indent=2)
        except Exception as e:
            print(f"Error saving {game_type} model: {e}")

    async def training_session(self, game_name, game_type):
        """Interactive training session for model improvement."""
        model = self.models[game_type]
        print(f"\n=== Training Session: {game_name} ({game_type} game) ===")
        
        # Clear any session-specific exclusions from previous runs
        model.clear_session_exclusions()
        
        # Initial search
        results = await self.training_search(game_name, game_type)
        if not results:
            return
            
        while True:
            print("\nTraining Options:")
            print("1. Flag result as irrelevant")
            print("2. Mark channel as trusted")
            print("3. Mark channel as noise")
            print("4. Add exclusion phrase for this game")
            print("5. Add persistent exclusion pattern")
            print("6. Remove exclusion")
            print("7. Show current model state")
            print("8. Refresh search with current model")
            print("9. Generate playlist with current settings")
            print("10. Save and exit")
            
            choice = await prompt_user("\nEnter choice (1-10): ")
            
            if choice == '9':
                print("\nGenerating playlist with current model settings...")
                # Set the last search game to preserve session exclusions
                self._last_search_game = game_name
                await self.generate_gameplay_playlist()
                break
            elif choice == '10':
                self._save_model(game_type)
                print(f"\nSaved {game_type} game model")
                print("Note: Session-specific exclusions were not persisted")
                break
            elif choice == '1':
                if not results:
                    print("No results to flag. Try refreshing the search.")
                    continue
                    
                print("\nCurrent results:")
                for i, result in enumerate(results, 1):
                    print(f"{i}. {result['title']} ({result['channel_title']})")
                    
                try:
                    idx = int(await prompt_user("Enter result number to flag: ")) - 1
                    if 0 <= idx < len(results):
                        result = results[idx]
                        print(f"\nFlagging: {result['title']}")
                        words = await prompt_user("Enter phrases that indicate irrelevance (comma-separated): ")
                        for phrase in words.split(','):
                            phrase = phrase.strip()
                            if phrase:
                                model.add_exclusion(phrase, persistent=False)
                                print(f"Added session exclusion: {phrase}")
                    else:
                        print("Invalid result number")
                except ValueError:
                    print("Invalid input. Please enter a number.")
                    
            elif choice == '2' or choice == '3':
                if not results:
                    print("No results to mark. Try refreshing the search.")
                    continue
                    
                # Filter out channels that are already classified
                channels_to_show = []
                for i, result in enumerate(results, 1):
                    channel = result['channel_title']
                    if choice == '2' and channel not in model.trusted_channels:
                        channels_to_show.append((i, channel))
                    elif choice == '3' and channel not in model.noise_channels:
                        channels_to_show.append((i, channel))
                
                if not channels_to_show:
                    print("\nNo unclassified channels to mark.")
                    continue
                
                print("\nAvailable channels to mark:")
                for i, (result_idx, channel) in enumerate(channels_to_show, 1):
                    print(f"{i}. {channel} (result #{result_idx})")
                    
                try:
                    idx = int(await prompt_user("Enter number to mark channel: ")) - 1
                    if 0 <= idx < len(channels_to_show):
                        _, channel = channels_to_show[idx]
                        if choice == '2':
                            model.add_trusted_channel(channel)
                            print(f"Added trusted channel: {channel}")
                        else:
                            model.add_noise_channel(channel)
                            print(f"Added noise channel: {channel}")
                    else:
                        print("Invalid number")
                except ValueError:
                    print("Invalid input. Please enter a number.")
                    
            elif choice == '4':
                phrase = await prompt_user("Enter game-specific phrase to exclude: ")
                if phrase:
                    model.add_exclusion(phrase, persistent=False)
                    print(f"Added session exclusion: {phrase}")
                    
            elif choice == '5':
                print("\nAdd a persistent pattern that applies to all searches")
                print("Examples: 'unboxing only', 'reaction video', 'price guide'")
                phrase = await prompt_user("Enter persistent pattern to exclude: ")
                if phrase:
                    model.add_exclusion(phrase, persistent=True)
                    print(f"Added persistent exclusion: {phrase}")
                    
            elif choice == '6':
                print("\nCurrent exclusions:")
                print("Session-specific:", ', '.join(model.session_exclusions))
                print("Persistent:", ', '.join(model.persistent_exclusions))
                phrase = await prompt_user("Enter phrase to remove: ")
                is_persistent = phrase in model.persistent_exclusions
                if phrase in model.get_all_exclusions():
                    model.remove_exclusion(phrase, persistent=is_persistent)
                    print(f"Removed {'persistent' if is_persistent else 'session'} exclusion: {phrase}")
                    
            elif choice == '7':
                print("\nCurrent Model State:")
                print("Session-specific exclusions:")
                for excl in sorted(model.session_exclusions):
                    print(f"  - {excl}")
                print("\nPersistent exclusions:")
                for excl in sorted(model.persistent_exclusions):
                    print(f"  - {excl}")
                print("\nTrusted channels:")
                for channel in sorted(model.trusted_channels):
                    print(f"  - {channel}")
                print("\nNoise channels:")
                for channel in sorted(model.noise_channels):
                    print(f"  - {channel}")
                print("\nScoring weights:")
                for k, v in model.scoring_weights.items():
                    print(f"  {k}: {v}")
                    
            elif choice == '8':
                print("\nRefreshing search with current model...")
                results = await self.training_search(game_name, game_type)
                if not results:
                    print("No results found with current model settings.")
                    
            else:
                print("Invalid choice")
                
            # After each change that affects results, offer to refresh
            if choice in ['1', '2', '3', '4', '5', '6']:
                refresh = await prompt_user("\nWould you like to refresh the search with these changes? (y/n): ")
                if refresh.lower() == 'y':
                    print("\nRefreshing search...")
                    results = await self.training_search(game_name, game_type)
