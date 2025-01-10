from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import yt_dlp
import json
import os

SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

class YouTubeTools:
    def __init__(self):
        # Initialize YouTube API client and downloader
        self.youtube = self._authenticate()
        self.downloader = self._setup_downloader()

    def _authenticate(self):
        # Try to load existing credentials from token.json
        creds = None
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        
        # If no valid credentials found, either refresh or create new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                # Start OAuth flow using credentials.json (must be obtained from Google Cloud Console)
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

    async def get_playlist_items(self, playlist_id, channel_id=None):
        # Handles YouTube's pagination system (max 50 items per request)
        items = []
        next_page_token = None
        
        while True:
            # Fetch batch of up to 50 items
            request = self.youtube.playlistItems().list(
                part='snippet',
                playlistId=playlist_id,
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
        request = self.youtube.playlistItems().insert(
            part='snippet',
            body={
                'snippet': {
                    'playlistId': playlist_id,
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
            request = self.youtube.playlists().delete(
                id=playlist_id
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

    async def remove_video_from_playlist(self, playlist_item_id):
        """Removes a video from a playlist using the playlist item ID."""
        try:
            request = self.youtube.playlistItems().delete(
                id=playlist_item_id
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

    async def advanced_search(self, query, resource_type=None, order='relevance',
                            channel_id=None, published_after=None, published_before=None,
                            duration_filter=None, max_results=50):
        """Performs an advanced YouTube search with multiple filters."""
        try:
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
                
                # Get additional details based on type
                if result['type'] == 'video':
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
                        
                elif result['type'] in ['playlist', 'channel']:
                    # ... (existing playlist/channel handling) ...
                    results.append(result)
                    
                if len(results) >= max_results:
                    break
                
            return results
            
        except Exception as e:
            print(f"Error in advanced search: {e}")
            return None
