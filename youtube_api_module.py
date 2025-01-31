from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import yt_dlp
import json
import os
from datetime import datetime

SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

class QuotaConfirmationError(Exception):
    """Raised when user declines a high-quota operation."""
    pass

class YouTubeTools:
    def __init__(self):
        # Initialize YouTube API client and downloader
        self.youtube = self._authenticate()
        self.downloader = self._setup_downloader()
        self.session_quota_used = 0
        self.DAILY_QUOTA = 10000
        self.history_file = 'playlist_history.json'
        self._load_history()
        
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
        
    async def advanced_search(self, query, resource_type=None, order='relevance',
                            channel_id=None, published_after=None, published_before=None,
                            duration_filter=None, max_results=50, light_mode=False):
        """Performs an advanced YouTube search with quota-aware operations."""
        try:
            # Estimate initial quota cost
            base_cost = 100  # Search operation
            estimated_cost = base_cost
            
            if not light_mode:
                # Estimate additional costs based on expected results
                estimated_details_cost = max_results  # 1 point per item details
                estimated_cost += estimated_details_cost
                
            self._track_quota(estimated_cost, "Advanced search")
            
            min_duration, max_duration = self._parse_duration_filter(duration_filter)
            
            # Initial search with maximum results since we'll filter some out
            params = {
                'q': query,
                'maxResults': max(50, max_results * 2),  # Request extra results to account for duration filtering
                'type': resource_type,
                'order': order,
                'part': 'snippet'
            }
            
            if channel_id:
                params['channelId'] = channel_id
            
            if published_after:
                params['publishedAfter'] = f"{published_after}T00:00:00Z"
            
            if published_before:
                params['publishedBefore'] = f"{published_before}T23:59:59Z"
            
            # Execute search
            request = self.youtube.search().list(**params)
            response = request.execute()
            
            results = []
            for item in response.get('items', []):
                result = {
                    'id': item['id'].get('videoId') or item['id'].get('playlistId') or item['id'].get('channelId'),
                    'type': item['id']['kind'].split('#')[1],
                    'title': item['snippet']['title'],
                    'channel_title': item['snippet']['channelTitle'],
                    'published_at': item['snippet']['publishedAt']
                }
                
                if light_mode:
                    results.append(result)
                    continue
                    
                # Get additional details based on type
                if result['type'] == 'video':
                    self._track_quota(1, "Video details")
                    video_response = self.youtube.videos().list(
                        part='contentDetails,statistics',
                        id=result['id']
                    ).execute()
                    
                    if video_response['items']:
                        stats = video_response['items'][0]
                        duration_str = stats['contentDetails']['duration']
                        duration_minutes = self._parse_iso_duration(duration_str)
                        
                        # Apply duration filter
                        if min_duration and duration_minutes < min_duration:
                            continue
                        if max_duration and duration_minutes > max_duration:
                            continue
                            
                        result.update({
                            'duration': f"{duration_minutes} minutes",
                            'duration_minutes': duration_minutes,
                            'view_count': int(stats['statistics'].get('viewCount', 0))
                        })
                        results.append(result)
                        
                elif result['type'] == 'playlist':
                    self._track_quota(1, "Playlist details")
                    try:
                        # Get basic playlist info
                        playlist_response = self.youtube.playlists().list(
                            part='contentDetails',
                            id=result['id']
                        ).execute()
                        
                        if playlist_response['items']:
                            result['video_count'] = playlist_response['items'][0]['contentDetails']['itemCount']
                            
                            # Get videos in playlist to determine date range
                            videos = []
                            next_page_token = None
                            
                            while True:
                                playlist_items = self.youtube.playlistItems().list(
                                    part='snippet',
                                    playlistId=result['id'],
                                    maxResults=50,
                                    pageToken=next_page_token
                                ).execute()
                                
                                for video in playlist_items['items']:
                                    published = video['snippet']['publishedAt']
                                    videos.append(published)
                                
                                next_page_token = playlist_items.get('nextPageToken')
                                if not next_page_token:
                                    break
                            
                            if videos:
                                videos.sort()
                                result['earliest_video'] = videos[0][:10]  # YYYY-MM-DD
                                result['latest_video'] = videos[-1][:10]  # YYYY-MM-DD
                            
                            results.append(result)
                            
                    except Exception as e:
                        print(f"Error fetching playlist details: {e}")
                        continue
                        
                elif result['type'] == 'channel':
                    # ... (channel handling remains the same) ...
                    results.append(result)
                    
                if len(results) >= max_results:
                    break
                
            return results
            
        except QuotaConfirmationError:
            print("\nOperation cancelled to preserve quota")
            return None
        except Exception as e:
            print(f"Error in advanced search: {e}")
            return None

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
