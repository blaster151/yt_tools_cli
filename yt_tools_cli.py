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

async def list_my_playlists(yt):
    print("\n=== My YouTube Playlists ===")
    playlists = await yt.get_my_playlists()
    
    if not playlists:
        print("No playlists found or error occurred.")
        return
        
    print("\nYour playlists (newest first):")
    for idx, playlist in enumerate(playlists, 1):
        print(f"{idx}. {playlist['title']}")
        print(f"   ID: {playlist['id']}")
        print(f"   Videos: {playlist['video_count']}\n")
    
    while True:
        print("\nOptions:")
        print("1. Delete playlists")
        print("2. Merge playlists")
        print("3. View/edit playlist contents")
        print("4. Return to main menu")
        
        choice = await prompt_user("\nEnter your choice (1-4): ")
        
        if choice == '1':
            await delete_playlists(yt, playlists)
        elif choice == '2':
            await merge_playlists(yt, playlists)
        elif choice == '3':
            await view_edit_playlist(yt, playlists)
        elif choice == '4':
            break
        else:
            print("Invalid choice. Please try again.")

async def delete_playlists(yt, playlists):
    cleanup = await prompt_user('\nEnter playlist numbers to delete (e.g., "1,3-5,7" or press Enter to cancel): ')
    
    if not cleanup.strip():
        return
        
    try:
        to_delete = await parse_range(cleanup.replace(',', ';'))
        valid_indices = [i for i in to_delete if 1 <= i <= len(playlists)]
        
        if not valid_indices:
            print("No valid playlist numbers entered.")
            return
            
        print("\nYou're about to delete these playlists:")
        for idx in valid_indices:
            print(f"- {playlists[idx-1]['title']}")
            
        confirm = await prompt_user('\nAre you sure? This cannot be undone! (yes/no): ')
        
        if confirm.lower() == 'yes':
            deleted = 0
            for idx in valid_indices:
                playlist = playlists[idx-1]
                try:
                    await yt.delete_playlist(playlist['id'])
                    print(f"Deleted: {playlist['title']}")
                    deleted += 1
                except Exception as e:
                    print(f"Error deleting {playlist['title']}: {e}")
            
            print(f"\nSuccessfully deleted {deleted} playlist(s)")
        else:
            print("Operation cancelled.")
            
    except ValueError as e:
        print(f"Error parsing numbers: {e}")

async def merge_playlists(yt, playlists):
    print("\nEnter the numbers of the playlists to merge (in desired order)")
    indices = await prompt_user('Playlist numbers (e.g., "1,3"): ')
    
    try:
        to_merge = [int(idx.strip()) for idx in indices.split(',')]
        valid_indices = [i for i in to_merge if 1 <= i <= len(playlists)]
        
        if len(valid_indices) < 2:
            print("Please select at least 2 valid playlist numbers.")
            return
            
        print("\nMerging these playlists (in this order):")
        for idx in valid_indices:
            print(f"- {playlists[idx-1]['title']}")
            
        new_title = await prompt_user('\nEnter name for the merged playlist: ')
        if not new_title.strip():
            print("Operation cancelled: playlist name cannot be empty")
            return
            
        confirm = await prompt_user('\nThis will create a new playlist and delete the originals. Continue? (yes/no): ')
        
        if confirm.lower() == 'yes':
            # Create new playlist
            new_playlist_id = await yt.create_playlist(new_title)
            if not new_playlist_id:
                print("Failed to create new playlist")
                return
                
            total_added = 0
            # Copy videos in order
            for idx in valid_indices:
                source_playlist = playlists[idx-1]
                items = await yt.get_playlist_items(source_playlist['id'])
                print(f"\nCopying from: {source_playlist['title']}")
                
                for item in items:
                    video_id = item['snippet']['resourceId']['videoId']
                    await yt.add_video_to_playlist(new_playlist_id, video_id)
                    print(f"Added: {item['snippet']['title']}")
                    total_added += 1
            
            # Delete original playlists
            for idx in valid_indices:
                playlist = playlists[idx-1]
                await yt.delete_playlist(playlist['id'])
                print(f"Deleted original playlist: {playlist['title']}")
                
            print(f"\nSuccess! Created new playlist '{new_title}' with {total_added} videos")
        else:
            print("Operation cancelled.")
            
    except ValueError as e:
        print(f"Error parsing numbers: {e}")

async def view_edit_playlist(yt, playlists):
    playlist_num = await prompt_user('\nEnter playlist number to view/edit: ')
    try:
        idx = int(playlist_num) - 1
        if not (0 <= idx < len(playlists)):
            print("Invalid playlist number")
            return
            
        playlist = playlists[idx]
        print(f"\nViewing playlist: {playlist['title']}")
        
        items = await yt.get_playlist_items(playlist['id'])
        if not items:
            print("Playlist is empty or error occurred")
            return
            
        print("\nVideos in playlist:")
        for idx, item in enumerate(items, 1):
            print(f"{idx}. {item['snippet']['title']}")
            
        print("\nOptions:")
        print("1. Remove videos")
        print("2. Reverse playlist order")
        print("3. Return to playlist menu")
        
        choice = await prompt_user("\nEnter your choice (1-3): ")
        
        if choice == '1':
            # Existing remove functionality
            remove = await prompt_user('\nEnter video numbers to remove (e.g., "1,3-5,7" or press Enter to cancel): ')
            
            if remove.strip():
                to_remove = await parse_range(remove.replace(',', ';'))
                valid_indices = [i for i in to_remove if 1 <= i <= len(items)]
                
                if not valid_indices:
                    print("No valid video numbers entered.")
                    return
                    
                print("\nYou're about to remove these videos:")
                for idx in valid_indices:
                    print(f"- {items[idx-1]['snippet']['title']}")
                    
                confirm = await prompt_user('\nAre you sure? (yes/no): ')
                
                if confirm.lower() == 'yes':
                    removed = 0
                    for idx in sorted(valid_indices, reverse=True):  # Remove from end to avoid index shifting
                        item = items[idx-1]
                        try:
                            await yt.remove_video_from_playlist(item['id'])  # Note: this is the playlistItem ID
                            print(f"Removed: {item['snippet']['title']}")
                            removed += 1
                        except Exception as e:
                            print(f"Error removing video: {e}")
                    
                    print(f"\nSuccessfully removed {removed} video(s)")
                else:
                    print("Operation cancelled.")
                    
        elif choice == '2':
            # New reverse functionality
            confirm = await prompt_user('\nReverse the order of all videos in this playlist? (yes/no): ')
            
            if confirm.lower() == 'yes':
                # First, scan for private/deleted videos
                private_count = 0
                print("\nScanning for private/deleted videos...")
                for item in items:
                    video_id = item['snippet']['resourceId']['videoId']
                    try:
                        details = await yt.get_video_details(video_id)
                        if not details:
                            private_count += 1
                    except Exception:
                        private_count += 1
                
                if private_count > 0:
                    print(f"\nWarning: Found {private_count} private/deleted videos in the playlist.")
                    print("These videos will be removed during the reversal process.")
                    keep_going = await prompt_user('Continue anyway? (yes/no): ')
                    if keep_going.lower() != 'yes':
                        print("Operation cancelled.")
                        return
                
                print("\nReversing playlist order...")
                # Create a new playlist temporarily
                temp_title = f"TEMP_{playlist['title']}"
                temp_playlist_id = await yt.create_playlist(temp_title)
                
                if not temp_playlist_id:
                    print("Failed to create temporary playlist")
                    return
                
                # Add videos in reverse order
                added = skipped = 0
                total = len(items)
                for item in reversed(items):
                    video_id = item['snippet']['resourceId']['videoId']
                    try:
                        await yt.add_video_to_playlist(temp_playlist_id, video_id)
                        added += 1
                    except Exception as e:
                        print(f"\nSkipped video (likely private/deleted): {item['snippet']['title']}")
                        skipped += 1
                    print(f"Progress: {added + skipped}/{total} videos (Skipped: {skipped})", end='\r')
                
                print("\n\nRemoving videos from original playlist...")
                # Remove all videos from original playlist
                removed = 0
                for item in items:
                    try:
                        await yt.remove_video_from_playlist(item['id'])
                        removed += 1
                    except Exception as e:
                        print(f"\nCouldn't remove video: {item['snippet']['title']}")
                    print(f"Progress: {removed}/{total} videos removed", end='\r')
                
                print("\n\nRestoring videos in new order...")
                # Copy back from temp playlist in new order
                restored = 0
                temp_items = await yt.get_playlist_items(temp_playlist_id)
                for item in temp_items:
                    video_id = item['snippet']['resourceId']['videoId']
                    try:
                        await yt.add_video_to_playlist(playlist['id'], video_id)
                        restored += 1
                    except Exception as e:
                        print(f"\nCouldn't restore video: {item['snippet']['title']}")
                    print(f"Progress: {restored}/{added} videos restored", end='\r')
                
                # Delete temporary playlist
                await yt.delete_playlist(temp_playlist_id)
                
                print(f"\n\nFinished!")
                print(f"Successfully reversed {restored} videos")
                if skipped > 0:
                    print(f"Skipped {skipped} private/deleted videos")
            else:
                print("Operation cancelled.")
                
        elif choice == '3':
            return
        else:
            print("Invalid choice.")
                
    except ValueError as e:
        print(f"Error parsing numbers: {e}")

async def main():
    yt = YouTubeTools()
    
    while True:
        print("\n=== YouTube Tools ===")
        print("1. Combine Playlists")
        print("2. Download Playlist")
        print("3. List My Playlists")
        print("4. Exit")
        
        choice = await prompt_user("\nEnter your choice (1-4): ")
        
        if choice == '1':
            await combine_playlists(yt)
        elif choice == '2':
            await download_playlist(yt)
        elif choice == '3':
            await list_my_playlists(yt)
        elif choice == '4':
            print("Goodbye!")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    asyncio.run(main())
