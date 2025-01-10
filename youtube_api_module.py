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
