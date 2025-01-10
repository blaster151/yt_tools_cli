import asyncio
from youtube_api_module import YouTubeTools
import os

async def prompt_user(message):
    return input(message).strip()

async def get_playlist_details(yt, playlist_id):
    try:
        request = yt.youtube.playlists().list(
            part='snippet',
            id=playlist_id
        )
        response = request.execute()
        if response['items']:
            return response['items'][0]
        return None
    except Exception as e:
        print(f"Error fetching playlist details: {e}")
        return None

async def validate_playlist(yt, playlist_id):
    try:
        request = yt.youtube.playlists().list(
            part='snippet',
            id=playlist_id
        )
        response = request.execute()
        if response['items']:
            return {
                'valid': True,
                'name': response['items'][0]['snippet']['title']
            }
        return {'valid': False, 'name': None}
    except:
        return {'valid': False, 'name': None}

async def get_channel_id_from_username(yt, username):
    try:
        # Try search first as it's more likely to work with modern channel names
        request = yt.youtube.search().list(
            part='snippet',
            q=username,
            type='channel',
            maxResults=5
        )
        response = request.execute()

        if response['items']:
            print('\nFound channels:')
            for idx, item in enumerate(response['items'], 1):
                print(f"{idx}. {item['snippet']['channelTitle']}")

            if len(response['items']) > 1:
                choice = await prompt_user('Enter number of correct channel (or press Enter for first result): ')
                idx = int(choice) - 1 if choice else 0
                if 0 <= idx < len(response['items']):
                    return response['items'][idx]['snippet']['channelId']
            
            return response['items'][0]['snippet']['channelId']

        print('No channels found with that name.')
        return None
    except Exception as e:
        print(f'Error finding channel: {e}')
        return None

async def parse_range(range_string):
    range_nums = []
    parts = range_string.split(';')
    
    for part in parts:
        if '-' in part:
            start, end = map(int, part.split('-'))
            range_nums.extend(range(start, min(end + 1, 251)))  # Cap at 250
        else:
            num = int(part)
            if num <= 250:
                range_nums.append(num)
    
    return range_nums

async def combine_playlists(yt):
    print("\n=== YouTube Playlist Combiner ===")
    
    # Get destination playlist
    dest_playlist_id = await prompt_user('Enter destination playlist ID: ')
    dest_details = await get_playlist_details(yt, dest_playlist_id)
    
    if not dest_details:
        print('Error: Could not find destination playlist')
        return
    
    print(f'Found destination playlist: "{dest_details["snippet"]["title"]}"')
    
    # Get source(s)
    source_input = await prompt_user('Enter source playlist ID(s) or video ID (comma-separated for multiple sources): ')
    source_ids = [s.strip() for s in source_input.split(',')]
    
    try:
        # Handle single source differently than multiple sources
        if len(source_ids) == 1:
            source_id = source_ids[0]
            
            # Try as playlist first
            playlist_info = await validate_playlist(yt, source_id)
            if playlist_info['valid']:
                print(f'Found playlist: "{playlist_info["name"]}"')
                
                # For single playlist, allow channel filter and range options
                channel_name = await prompt_user('Enter channel name to filter by (or press Enter to skip): ')
                channel_id = None
                if channel_name:
                    print('Looking up channel ID...')
                    channel_id = await get_channel_id_from_username(yt, channel_name)
                    if not channel_id:
                        print('Could not find channel. Proceeding without channel filter.')
                    else:
                        print(f'Found channel ID: {channel_id}')

                range_string = await prompt_user('Enter range of videos to copy (e.g., "1-5", "3", "3;7"): ')
                range_nums = await parse_range(range_string)
                items_to_copy = await yt.get_playlist_items(source_id, channel_id)
                
                # Apply range filter
                items_to_copy = [items_to_copy[i-1] for i in range_nums if i <= len(items_to_copy)]
                
                print(f'Source playlist has {len(items_to_copy)} videos in the selected range.')
                added = skipped = 0

                for item in items_to_copy:
                    video_id = item['snippet']['resourceId']['videoId']
                    if await yt.is_video_in_playlist(dest_playlist_id, video_id):
                        print(f'Skipped duplicate video: {item["snippet"]["title"]}')
                        skipped += 1
                    else:
                        await yt.add_video_to_playlist(dest_playlist_id, video_id)
                        print(f'Added video: {item["snippet"]["title"]}')
                        added += 1
                
                print(f'\nSummary: Added {added} videos, Skipped {skipped} duplicates')
                
            else:
                # Try as video
                video_details = await yt.get_video_details(source_id)
                if video_details:
                    print(f'Found video: "{video_details["snippet"]["title"]}"')
                    if await yt.is_video_in_playlist(dest_playlist_id, source_id):
                        print('Video is already in the playlist.')
                    else:
                        await yt.add_video_to_playlist(dest_playlist_id, source_id)
                        print(f'Added video: {video_details["snippet"]["title"]}')
                else:
                    print('Error: Invalid playlist ID or video ID provided')
                    return
                
        else:
            # Multiple sources - validate all first
            print('Validating multiple sources...')
            
            for source_id in source_ids:
                playlist_info = await validate_playlist(yt, source_id)
                if playlist_info['valid']:
                    print(f'Found playlist: "{playlist_info["name"]}"')
                    continue

                video_details = await yt.get_video_details(source_id)
                if video_details:
                    print(f'Found video: "{video_details["snippet"]["title"]}"')
                    continue

                print(f'Error: Could not find playlist or video with ID: {source_id}')
                return

            print('\nAll sources validated. Beginning copy process...')
            total_added = total_skipped = 0

            for source_id in source_ids:
                items = await yt.get_playlist_items(source_id)
                
                if items:
                    print(f'\nProcessing playlist: {source_id}')
                    for item in items:
                        video_id = item['snippet']['resourceId']['videoId']
                        if await yt.is_video_in_playlist(dest_playlist_id, video_id):
                            print(f'Skipped duplicate video: {item["snippet"]["title"]}')
                            total_skipped += 1
                        else:
                            await yt.add_video_to_playlist(dest_playlist_id, video_id)
                            print(f'Added video: {item["snippet"]["title"]}')
                            total_added += 1
                else:
                    video_details = await yt.get_video_details(source_id)
                    if video_details:
                        print(f'\nProcessing single video: {video_details["snippet"]["title"]}')
                        if await yt.is_video_in_playlist(dest_playlist_id, source_id):
                            print(f'Skipped duplicate video: {video_details["snippet"]["title"]}')
                            total_skipped += 1
                        else:
                            await yt.add_video_to_playlist(dest_playlist_id, source_id)
                            print(f'Added video: {video_details["snippet"]["title"]}')
                            total_added += 1

            print(f'\nFinal Summary: Added {total_added} videos, Skipped {total_skipped} duplicates')

    except Exception as e:
        print(f'Error processing sources: {e}')
        return

    print('Finished copying videos.')

async def download_playlist(yt):
    print("\n=== YouTube Playlist Downloader ===")
    playlist_id = await prompt_user('Enter playlist ID to download: ')
    
    # Validate playlist
    playlist_info = await validate_playlist(yt, playlist_id)
    if not playlist_info['valid']:
        print('Error: Invalid playlist ID')
        return
    
    print(f'Found playlist: "{playlist_info["name"]}"')
    
    # Get output directory
    output_dir = await prompt_user('Enter output directory (or press Enter for current directory): ')
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    
    await yt.download_playlist(playlist_id, output_dir if output_dir else None)

def extract_video_id(url_or_id):
    """Extract video ID from various YouTube URL formats or return the ID if already clean."""
    if '?v=' in url_or_id:
        return url_or_id.split('?v=')[1].split('&')[0]
    elif 'youtu.be/' in url_or_id:
        return url_or_id.split('youtu.be/')[1].split('?')[0]
    elif '?si=' in url_or_id:
        return url_or_id.split('?si=')[0]
    return url_or_id

async def main():
    yt = YouTubeTools()
    
    while True:
        print("\n=== YouTube Tools ===")
        print("1. Combine Playlists")
        print("2. Download Playlist")
        print("3. Exit")
        
        choice = await prompt_user("\nEnter your choice (1-3): ")
        
        if choice == '1':
            await combine_playlists(yt)
        elif choice == '2':
            await download_playlist(yt)
        elif choice == '3':
            print("Goodbye!")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    asyncio.run(main())
