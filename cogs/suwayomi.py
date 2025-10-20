import discord
from discord.ext import commands
from discord.commands import slash_command, Option
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from discord.ui import View, Button, Select
import asyncio
import io

logger = logging.getLogger('suwayomi_bot.cog')

# Suwayomi logo for embed thumbnails
SUWAYOMI_LOGO_URL = "https://raw.githubusercontent.com/idio-sync/suwayomi-bot/96eb7742c14a4356904069c1b66d87dc2b93e4c2/suwayomi_logo.png"

class MangaSelectView(discord.ui.View):
    """View with dropdown for selecting manga from search results."""
    
    def __init__(self, bot, search_results, timeout=180):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.search_results = search_results
        self.selected_manga = None
        
        # Create dropdown with search results
        options = []
        for i, manga in enumerate(search_results[:25]):  # Discord limit: 25 options
            # Get source info if available
            source_name = manga.get('source', {}).get('displayName', 'Unknown Source')
            
            options.append(
                discord.SelectOption(
                    label=manga['title'][:100],  # Truncate if too long
                    description=f"Source: {source_name}"[:100],
                    value=str(i),
                    emoji="📖"
                )
            )
        
        # Add the select menu
        select = Select(
            placeholder="Choose a manga...",
            options=options,
            custom_id="manga_select"
        )
        select.callback = self.select_callback
        self.add_item(select)
    
    def build_full_url(self, path):
        """Build a proper full URL from Suwayomi's response."""
        if not path:
            return None
        
        # If it's already a full URL, return it
        if path.startswith(('http://', 'https://')):
            return path
        
        # Otherwise, prepend the Suwayoshi base URL
        base_url = self.bot.config.SUWAYOMI_URL
        
        # Handle relative paths
        if path.startswith('/'):
            return f"{base_url}{path}"
        else:
            return f"{base_url}/{path}"
    
    async def fetch_and_attach_image(self, url):
        """
        Fetch an image from URL and prepare it as a Discord attachment.
        
        Args:
            url: The image URL to fetch
            
        Returns:
            discord.File object or None if fetch fails
        """
        if not url:
            return None
            
        try:
            session = await self.bot.ensure_session()
            
            # Add headers to mimic a browser request
            headers = {
                "User-Agent": "SuwayomiBot/1.0",
                "Accept": "image/*"
            }
            
            logger.debug(f"Fetching image from: {url}")
            
            # Reduced timeout from 10 to 5 seconds
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    
                    # Create a Discord file from the image data
                    # Use BytesIO to create an in-memory file
                    file = discord.File(io.BytesIO(image_data), filename="manga_cover.jpg")
                    logger.debug(f"Successfully fetched image, size: {len(image_data)} bytes")
                    return file
                else:
                    logger.debug(f"Failed to fetch image, status: {resp.status}")
                    
        except asyncio.TimeoutError:
            logger.debug(f"Timeout fetching image from {url}")
        except Exception as e:
            logger.debug(f"Error fetching image: {e}")
        
        return None
    
    async def select_callback(self, interaction: discord.Interaction):
        """Handle manga selection from dropdown."""
        selected_index = int(interaction.data['values'][0])
        self.selected_manga = self.search_results[selected_index]
        
        # Disable the select menu
        for item in self.children:
            if isinstance(item, Select):
                item.disabled = True
        
        # Show "Loading..." message
        await interaction.response.edit_message(
            content="📚 Loading manga details...",
            view=self
        )
        
        # Fetch full manga details - FIXED QUERY
        manga_id = self.selected_manga['id']
        
        details_query = """
        query GetMangaDetails($id: Int!) {
            manga(id: $id) {
                id
                title
                author
                artist
                description
                status
                genre
                thumbnailUrl
                inLibrary
                realUrl
                sourceId
                lastFetchedAt
                inLibraryAt
                initialized
                bookmarkCount
            }
            chapters(filter: { mangaId: { equalTo: $id } }) {
                totalCount
                nodes {
                    id
                    name
                    chapterNumber
                    uploadDate
                    isRead
                    isDownloaded
                }
            }
        }
        """
        
        try:
            manga_details = await self.bot.graphql_query(details_query, {"id": manga_id})
            
            if not manga_details or not manga_details.get('manga'):
                # Try to at least show basic info from search result
                basic_embed = discord.Embed(
                    title=self.selected_manga.get('title', 'Unknown'),
                    description="⚠️ Could not load full details. Basic information shown.",
                    color=discord.Color.orange()
                )
                basic_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
                
                # Try to add thumbnail from search result
                image_file = None
                if self.selected_manga.get('thumbnailUrl'):
                    thumbnail_url = self.build_full_url(self.selected_manga['thumbnailUrl'])
                    image_file = await self.fetch_and_attach_image(thumbnail_url)
                    if image_file:
                        basic_embed.set_image(url="attachment://manga_cover.jpg")
                
                basic_embed.add_field(
                    name="Source",
                    value=self.selected_manga.get('source', {}).get('displayName', 'Unknown'),
                    inline=False
                )
                
                basic_embed.set_footer(text=f"Manga ID: {manga_id}")
                
                # Still show the action buttons
                button_view = MangaActionView(self.bot, self.selected_manga, timeout=300)
                
                if image_file:
                    await interaction.edit_original_response(
                        content=None,
                        embed=basic_embed,
                        view=button_view,
                        files=[image_file]
                    )
                else:
                    await interaction.edit_original_response(
                        content=None,
                        embed=basic_embed,
                        view=button_view
                    )
                return
            
            manga = manga_details['manga']
            
            # If manga isn't initialized, fetch chapters to initialize it
            if not manga.get('initialized'):
                logger.info(f"Manga {manga_id} not initialized, fetching chapters...")
                
                # Update loading message
                await interaction.edit_original_response(
                    content="📚 Initializing manga data...",
                    view=self
                )
                
                fetch_mutation = """
                mutation FetchChapters($mangaId: Int!) {
                    fetchChapters(input: { mangaId: $mangaId }) {
                        clientMutationId
                    }
                }
                """
                
                fetch_result = await self.bot.graphql_query(fetch_mutation, {"mangaId": manga_id})
                
                if fetch_result and fetch_result.get('fetchChapters'):
                    # Small wait to allow data to propagate
                    await asyncio.sleep(0.3)
                    
                    # Re-query to get updated data
                    updated_data = await self.bot.graphql_query(details_query, {"id": manga_id})
                    if updated_data:
                        if updated_data.get('manga'):
                            manga = updated_data['manga']
                        if updated_data.get('chapters'):
                            chapters_data = updated_data['chapters']
            
            chapters_data = manga_details.get('chapters', {})
            
            # Start fetching the cover image in parallel (don't wait for it)
            image_task = None
            thumbnail_url = manga.get('thumbnailUrl')
            if thumbnail_url:
                full_image_url = self.build_full_url(thumbnail_url)
                logger.debug(f"Starting background fetch of cover from: {full_image_url}")
                image_task = asyncio.create_task(self.fetch_and_attach_image(full_image_url))
            
            # Create detailed embed
            embed = discord.Embed(
                title=manga['title'],
                url=self.build_full_url(manga.get('realUrl')) if manga.get('realUrl') else None,
                color=discord.Color.blue() if not manga.get('inLibrary') else discord.Color.green()
            )
            embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
            
            # Add description (truncate if too long)
            description = (manga.get('description') or '').strip()
            if description:
                if len(description) > 200:
                    description = description[:197] + "..."
                embed.description = description
            else:
                embed.description = "*No description available*"
            
            # Fetch and attach cover image
            image_file = None
            thumbnail_url = manga.get('thumbnailUrl')
            if thumbnail_url:
                full_image_url = self.build_full_url(thumbnail_url)
                logger.debug(f"Attempting to fetch cover from: {full_image_url}")
                
                # Try to fetch and attach the image
                image_file = await self.fetch_and_attach_image(full_image_url)
                if image_file:
                    # Set the embed to use the attached image
                    embed.set_image(url="attachment://manga_cover.jpg")
                    logger.debug("Successfully attached manga cover image")
                else:
                    logger.warning(f"Failed to fetch image from {full_image_url}")
            
            # Author and Artist
            author = (manga.get('author') or '').strip() or 'Unknown'
            artist = (manga.get('artist') or '').strip() or 'Unknown'
            
            if author == artist or artist == 'Unknown':
                if author != 'Unknown':
                    embed.add_field(name="👤 Creator", value=author, inline=True)
            else:
                embed.add_field(name="✍️ Author", value=author, inline=True)
                embed.add_field(name="🎨 Artist", value=artist, inline=True)
            
            # Status with emoji
            status = manga.get('status', 'UNKNOWN')
            status_display = {
                "ONGOING": "📖 Ongoing",
                "COMPLETED": "✅ Completed",
                "LICENSED": "©️ Licensed",
                "PUBLISHING": "📰 Publishing",
                "HIATUS": "⸏ On Hiatus",
                "CANCELLED": "❌ Cancelled",
                "UNKNOWN": "❓ Unknown"
            }.get(status, f"❓ {status}")
            
            embed.add_field(name="📊 Status", value=status_display, inline=True)
            
            # Chapter information - FIXED
            all_chapters = chapters_data.get('nodes', [])
            total_chapters = chapters_data.get('totalCount', 0)
            
            # Calculate unread/downloaded from chapter data
            unread_count = sum(1 for ch in all_chapters if not ch.get('isRead', False))
            downloaded_count = sum(1 for ch in all_chapters if ch.get('isDownloaded', False))
            
            chapter_info = f"**Total:** {total_chapters}"
            if manga.get('inLibrary'):
                if unread_count > 0:
                    chapter_info += f"\n**Unread:** {unread_count}"
                if downloaded_count > 0:
                    chapter_info += f"\n**Downloaded:** {downloaded_count}"
            
            embed.add_field(name="📚 Chapters", value=chapter_info, inline=True)
            
            # Genres (limit to first 3)
            genres = manga.get('genre') or []
            if genres and isinstance(genres, list):
                genre_list = genres[:3]  # Limit to 3 genres
                if len(genres) > 3:
                    genre_text = ", ".join(genre_list) + f" +{len(genres)-3} more"
                else:
                    genre_text = ", ".join(genre_list)
                embed.add_field(name="🏷️ Genres", value=genre_text, inline=False)
            
            # Latest chapters preview - FIXED
            recent_chapters = sorted(all_chapters, key=lambda x: x.get('chapterNumber', 0), reverse=True)[:5]
            
            if recent_chapters and total_chapters > 0:
                chapter_preview = []
                for ch in recent_chapters[:3]:  # Show up to 3 recent chapters
                    ch_name = (ch.get('name') or '').strip()
                    ch_num = ch.get('chapterNumber', 0)
                    if ch_name and ch_name != f"Chapter {ch_num}":
                        chapter_preview.append(f"Ch. {ch_num}: {ch_name[:30]}")
                    else:
                        chapter_preview.append(f"Chapter {ch_num}")
                
                if chapter_preview:
                    embed.add_field(
                        name="📖 Recent Chapters",
                        value="\n".join(chapter_preview),
                        inline=False
                    )
            
            # Library status
            if manga.get('inLibrary'):
                status_text = "✅ **In Library**"
                
                # Add library added date if available
                if manga.get('inLibraryAt'):
                    try:
                        timestamp = int(manga['inLibraryAt'])
                        # Check if timestamp is in milliseconds or seconds
                        # If it's a very small number, it's likely in seconds
                        # Timestamps after year 2000 in seconds are > 946684800
                        # Timestamps in milliseconds would be much larger
                        if timestamp > 100000000000:  # Likely milliseconds
                            added_date = datetime.fromtimestamp(timestamp / 1000)
                        else:  # Likely seconds
                            added_date = datetime.fromtimestamp(timestamp)
                        
                        # Only show date if it's reasonable (after 2000 and before 2100)
                        if added_date.year >= 2000 and added_date.year < 2100:
                            status_text += f"\nAdded: {added_date.strftime('%Y-%m-%d')}"
                    except (ValueError, OSError):
                        # Skip if timestamp is invalid
                        pass
                
                # Add bookmark count if available
                bookmark_count = manga.get('bookmarkCount', 0)
                if bookmark_count > 0:
                    status_text += f"\n🔖 {bookmark_count} bookmarks"
                
                embed.add_field(name="📚 Library Status", value=status_text, inline=False)
            
            # Source information
            source = manga.get('source', {})
            if not source:
                source = self.selected_manga.get('source', {})
            
            source_name = source.get('displayName', 'Unknown')
            source_lang = source.get('lang', 'Unknown').upper()
            
            footer_text = f"Source: {source_name} ({source_lang}) | ID: {manga_id}"
            
            # Add last fetched info if available
            if manga.get('lastFetchedAt'):
                try:
                    timestamp = int(manga['lastFetchedAt'])
                    # Check if timestamp is in milliseconds or seconds
                    if timestamp > 100000000000:  # Likely milliseconds
                        last_fetch = datetime.fromtimestamp(timestamp / 1000)
                    else:  # Likely seconds
                        last_fetch = datetime.fromtimestamp(timestamp)
                    
                    # Only show if date is reasonable
                    if last_fetch.year >= 2000 and last_fetch.year < 2100:
                        time_ago = datetime.now() - last_fetch
                        if time_ago.days > 0:
                            footer_text += f" | Updated {time_ago.days}d ago"
                        elif time_ago.seconds > 3600:
                            footer_text += f" | Updated {time_ago.seconds // 3600}h ago"
                        else:
                            footer_text += f" | Updated recently"
                except (ValueError, OSError):
                    pass
            
            embed.set_footer(text=footer_text)
            
            # Create view with action buttons
            button_view = MangaActionView(self.bot, manga, timeout=300)
            
            # Send response with or without image attachment
            if image_file:
                await interaction.edit_original_response(
                    content=None,
                    embed=embed,
                    view=button_view,
                    files=[image_file]
                )
            else:
                await interaction.edit_original_response(
                    content=None,
                    embed=embed,
                    view=button_view
                )
            
        except Exception as e:
            logger.error(f"Error in select_callback: {e}", exc_info=True)
            await interaction.edit_original_response(
                content="❌ An error occurred while loading manga details. Please try again.",
                view=None
            )
        finally:
            # Stop this view
            self.stop()


class MangaActionView(discord.ui.View):
    """View with buttons for adding manga to library."""
    
    def __init__(self, bot, manga_data, timeout=300):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.manga_data = manga_data
        self.manga_id = manga_data['id']
        self.in_library = manga_data.get('inLibrary', False)
        
        # Customize button based on library status
        if self.in_library:
            self.add_button.label = "Already in Library ✓"
            self.add_button.disabled = True
            self.add_button.style = discord.ButtonStyle.secondary
        else:
            self.add_button.label = "Add to Library & Download"
            self.add_button.style = discord.ButtonStyle.success
    
    def build_full_url(self, path):
        """Build a proper full URL from Suwayomi's response."""
        if not path:
            return None
        
        # If it's already a full URL, return it
        if path.startswith(('http://', 'https://')):
            return path
        
        # Otherwise, prepend the Suwayomi base URL
        base_url = self.bot.config.SUWAYOMI_URL
        
        # Handle relative paths
        if path.startswith('/'):
            return f"{base_url}{path}"
        else:
            return f"{base_url}/{path}"
    
    async def fetch_and_attach_image(self, url):
        """
        Fetch an image from URL and prepare it as a Discord attachment.
        
        Args:
            url: The image URL to fetch
            
        Returns:
            discord.File object or None if fetch fails
        """
        if not url:
            return None
            
        try:
            session = await self.bot.ensure_session()
            
            # Add headers to mimic a browser request
            headers = {
                "User-Agent": "SuwayomiBot/1.0",
                "Accept": "image/*"
            }
            
            logger.debug(f"Fetching image from: {url}")
            
            # Reduced timeout from 10 to 5 seconds
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    
                    # Create a Discord file from the image data
                    # Use BytesIO to create an in-memory file
                    file = discord.File(io.BytesIO(image_data), filename="manga_cover.jpg")
                    logger.debug(f"Successfully fetched image, size: {len(image_data)} bytes")
                    return file
                else:
                    logger.debug(f"Failed to fetch image, status: {resp.status}")
                    
        except asyncio.TimeoutError:
            logger.debug(f"Timeout fetching image from {url}")
        except Exception as e:
            logger.debug(f"Error fetching image: {e}")
        
        return None
    
    @discord.ui.button(label="Add to Library & Download", style=discord.ButtonStyle.success, custom_id="add_manga")
    async def add_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Handle adding manga to library and downloading."""
        await interaction.response.defer()
        
        # Disable button to prevent double-clicks
        button.disabled = True
        button.label = "Adding..."
        await interaction.edit_original_response(view=self)
        
        try:
            # Step 1: Add to library
            add_mutation = """
            mutation AddToLibrary($id: Int!) {
                updateManga(input: {
                    id: $id
                    patch: {
                        inLibrary: true
                    }
                }) {
                    manga {
                        id
                        title
                        inLibrary
                    }
                }
            }
            """
            
            add_result = await self.bot.graphql_query(add_mutation, {"id": self.manga_id})
            
            if not add_result or not add_result.get('updateManga'):
                button.label = "❌ Failed to Add"
                button.style = discord.ButtonStyle.danger
                await interaction.edit_original_response(view=self)
                return
            
            title = self.manga_data['title']
            
            # Step 2: Fetch chapters from source (this also initializes manga metadata)
            button.label = "Fetching chapters..."
            await interaction.edit_original_response(view=self)
            
            # Trigger chapter fetch (doesn't return chapters, just triggers the fetch)
            fetch_mutation = """
            mutation FetchChapters($mangaId: Int!) {
                fetchChapters(input: { mangaId: $mangaId }) {
                    clientMutationId
                }
            }
            """
            
            fetch_result = await self.bot.graphql_query(fetch_mutation, {"mangaId": self.manga_id})
            
            if not fetch_result or not fetch_result.get('fetchChapters'):
                # Even if fetch fails, the manga was added to library
                button.label = "✅ Added (Fetch Failed)"
                button.style = discord.ButtonStyle.secondary
                
                warning_embed = discord.Embed(
                    title="⚠️ Partially Added",
                    description=f"**{title}** was added to library but couldn't fetch chapters from source.",
                    color=discord.Color.yellow()
                )
                warning_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
                warning_embed.add_field(
                    name="💡 Try manually",
                    value="Check the Suwayomi web interface to manually fetch chapters.",
                    inline=False
                )
                await interaction.edit_original_response(embed=warning_embed, view=self)
                return
            
            # Wait a moment for the fetch to complete
            await asyncio.sleep(2)
            
            # Now query the chapters that were fetched
            chapters_query = """
            query GetChapters($mangaId: Int!) {
                chapters(filter: { mangaId: { equalTo: $mangaId } }) {
                    totalCount
                    nodes {
                        id
                        name
                        chapterNumber
                    }
                }
            }
            """
            
            chapters_result = await self.bot.graphql_query(chapters_query, {"mangaId": self.manga_id})
            
            if not chapters_result or not chapters_result.get('chapters'):
                button.label = "✅ Added (No Chapters)"
                button.style = discord.ButtonStyle.secondary
                
                info_embed = discord.Embed(
                    title="✅ Added to Library",
                    description=f"**{title}** was added but no chapters were found.",
                    color=discord.Color.blue()
                )
                info_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
                await interaction.edit_original_response(embed=info_embed, view=self)
                return
            
            # Get chapters from query result
            chapters_data = chapters_result.get('chapters', {})
            chapters = chapters_data.get('nodes', [])
            total_chapters = chapters_data.get('totalCount', len(chapters))
            
            if total_chapters == 0:
                button.label = "✅ Added (No Chapters)"
                button.style = discord.ButtonStyle.secondary
                
                info_embed = discord.Embed(
                    title="✅ Added to Library",
                    description=f"**{title}** was added but no chapters are available yet.",
                    color=discord.Color.blue()
                )
                info_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
                await interaction.edit_original_response(embed=info_embed, view=self)
                return
            
            # Step 3: Queue all chapters for download
            button.label = f"Downloading {total_chapters} chapters..."
            await interaction.edit_original_response(view=self)
            
            chapter_ids = [ch['id'] for ch in chapters]
            
            download_mutation = """
            mutation DownloadChapters($chapterIds: [Int!]!) {
                enqueueChapterDownloads(input: { ids: $chapterIds }) {
                    downloadStatus {
                        state
                    }
                }
            }
            """
            
            # Download in batches of 50 to avoid overwhelming the API
            batch_size = 50
            download_success = True
            downloaded_count = 0
            
            for i in range(0, len(chapter_ids), batch_size):
                batch = chapter_ids[i:i + batch_size]
                result = await self.bot.graphql_query(download_mutation, {"chapterIds": batch})
                if not result:
                    download_success = False
                    break
                downloaded_count += len(batch)
                
                # Update progress
                if len(chapter_ids) > batch_size:
                    button.label = f"Queued {downloaded_count}/{total_chapters}..."
                    await interaction.edit_original_response(view=self)
                
                await asyncio.sleep(0.5)  # Small delay between batches
            
            # Step 4: Re-query manga to get updated metadata
            manga_query = """
            query GetManga($id: Int!) {
                manga(id: $id) {
                    id
                    title
                    author
                    artist
                    description
                    status
                    genre
                    thumbnailUrl
                    initialized
                }
            }
            """
            
            manga_result = await self.bot.graphql_query(manga_query, {"id": self.manga_id})
            manga_info = manga_result.get('manga', {}) if manga_result else {}
            
            # Update button to show success
            if download_success:
                button.label = f"✅ Added & Queued {total_chapters} Chapters"
                button.style = discord.ButtonStyle.secondary
                
                # Create success embed with metadata
                success_embed = discord.Embed(
                    title="✅ Successfully Added to Library!",
                    description=f"**{title}**",
                    color=discord.Color.green()
                )
                success_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
                
                # Add metadata if available
                if manga_info:
                    author = (manga_info.get('author') or '').strip()
                    status = manga_info.get('status', 'UNKNOWN')
                    description = (manga_info.get('description') or '').strip()
                    genres = manga_info.get('genre') or []
                    
                    if author:
                        success_embed.add_field(name="👤 Author", value=author, inline=True)
                    
                    status_display = {
                        "ONGOING": "📖 Ongoing",
                        "COMPLETED": "✅ Completed",
                        "LICENSED": "©️ Licensed",
                        "PUBLISHING": "📰 Publishing",
                        "HIATUS": "⸏ On Hiatus",
                        "CANCELLED": "❌ Cancelled",
                        "UNKNOWN": "❓ Unknown"
                    }.get(status, f"❓ {status}")
                    
                    success_embed.add_field(name="📊 Status", value=status_display, inline=True)
                    
                    # Add genres (limit to first 3)
                    if genres and isinstance(genres, list):
                        genre_list = genres[:3]
                        if len(genres) > 3:
                            genre_text = ", ".join(genre_list) + f" +{len(genres)-3} more"
                        else:
                            genre_text = ", ".join(genre_list)
                        success_embed.add_field(name="🏷️ Genres", value=genre_text, inline=False)
                    
                    if description:
                        # Truncate description for embed
                        desc_preview = description[:200] + "..." if len(description) > 200 else description
                        success_embed.add_field(name="📖 Description", value=desc_preview, inline=False)
                
                success_embed.add_field(
                    name="📥 Download Status",
                    value=f"Queued {total_chapters} chapters for download",
                    inline=False
                )
                
                success_embed.set_footer(text=f"Manga ID: {self.manga_id}")
                
                # Fetch and attach cover image
                image_file = None
                thumbnail_url = manga_info.get('thumbnailUrl') if manga_info else None
                if thumbnail_url:
                    full_image_url = self.build_full_url(thumbnail_url)
                    image_file = await self.fetch_and_attach_image(full_image_url)
                    if image_file:
                        success_embed.set_image(url="attachment://manga_cover.jpg")
                
                if image_file:
                    await interaction.edit_original_response(embed=success_embed, view=self, files=[image_file])
                else:
                    await interaction.edit_original_response(embed=success_embed, view=self)
            else:
                button.label = f"✅ Added (Queued {downloaded_count}/{total_chapters})"
                button.style = discord.ButtonStyle.secondary
                
                warning_embed = discord.Embed(
                    title="⚠️ Partially Added to Library",
                    description=f"**{title}**\n\nManga was added but some chapters couldn't be queued.",
                    color=discord.Color.yellow()
                )
                warning_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
                warning_embed.add_field(
                    name="Status",
                    value=f"Queued {downloaded_count} of {total_chapters} chapters",
                    inline=False
                )
                await interaction.edit_original_response(embed=warning_embed, view=self)
            
        except Exception as e:
            logger.error(f"Error adding manga to library: {e}", exc_info=True)
            button.label = "❌ Error Occurred"
            button.style = discord.ButtonStyle.danger
            
            error_embed = discord.Embed(
                title="❌ Error Adding Manga",
                description=f"An error occurred while adding the manga.\nError: {str(e)[:200]}",
                color=discord.Color.red()
            )
            error_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
            await interaction.edit_original_response(embed=error_embed, view=self)

class SuwayomiCog(commands.Cog):
    """
    This cog contains all commands for interacting with a Suwayomi server.
    """

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        logger.debug("SuwayomiCog initialized")

    @slash_command(
        name="library_stats",
        description="View statistics about your Suwayomi library"
    )
    async def library_stats(self, ctx: discord.ApplicationContext):
        """Get library statistics."""
        await ctx.defer()

        # Now with correct filter syntax!
        query = """
        query {
            mangas {
                totalCount
            }
            chapters {
                totalCount
            }
            categories {
                totalCount
            }
            sources {
                totalCount
            }
            libraryMangas: mangas(filter: {inLibrary: {equalTo: true}}) {
                totalCount
            }
            unreadChapters: chapters(filter: {isRead: {equalTo: false}}) {
                totalCount
            }
            downloadedChapters: chapters(filter: {isDownloaded: {equalTo: true}}) {
                totalCount
            }
        }
        """

        data = await self.bot.graphql_query(query)
        
        if data is None:
            await ctx.respond(
                "❌ **Connection Error!**\n"
                "Could not connect to the Suwayomi server. Please check the configuration."
            )
            return

        try:
            embed = discord.Embed(
                title="📚 Suwayomi Library Statistics",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
            
            embed.add_field(
                name="📖 Library Manga", 
                value=f"{data.get('libraryMangas', {}).get('totalCount', 0):,}", 
                inline=True
            )
            embed.add_field(
                name="📑 Total Manga", 
                value=f"{data.get('mangas', {}).get('totalCount', 0):,}", 
                inline=True
            )
            embed.add_field(
                name="📄 Total Chapters", 
                value=f"{data.get('chapters', {}).get('totalCount', 0):,}", 
                inline=True
            )
            embed.add_field(
                name="📬 Unread", 
                value=f"{data.get('unreadChapters', {}).get('totalCount', 0):,}", 
                inline=True
            )
            embed.add_field(
                name="⬇️ Downloaded", 
                value=f"{data.get('downloadedChapters', {}).get('totalCount', 0):,}", 
                inline=True
            )
            embed.add_field(
                name="🏷️ Categories", 
                value=data.get('categories', {}).get('totalCount', 0), 
                inline=True
            )
            embed.add_field(
                name="📌 Sources", 
                value=data.get('sources', {}).get('totalCount', 0), 
                inline=True
            )
            
            embed.set_footer(text=f"Connected to: {self.bot.config.SUWAYOMI_URL}")

            await ctx.respond(embed=embed)
            
        except Exception as e:
            logger.error("Error processing GraphQL response", exc_info=e)
            await ctx.respond("😵 An unexpected error occurred while processing the response.")

    @slash_command(
        name="downloads",
        description="Show download queue status"
    )
    async def download_status(self, ctx: discord.ApplicationContext):
        """Show current download queue."""
        await ctx.defer()

        query = """
        query {
            downloadStatus {
                state
                queue {
                    chapter {
                        name
                        chapterNumber
                        manga {
                            title
                        }
                    }
                    state
                    progress
                }
            }
        }
        """

        data = await self.bot.graphql_query(query)
        
        if data is None:
            await ctx.respond("❌ Failed to fetch download status.")
            return

        download_status = data.get('downloadStatus', {})
        state = download_status.get('state', 'Unknown')
        queue = download_status.get('queue', [])
        
        embed = discord.Embed(
            title="⬇️ Download Status",
            color=discord.Color.blue() if state == "Running" else discord.Color.greyple()
        )
        embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
        
        if queue:
            embed.add_field(name="\u200b", value="**Queue:**", inline=False)
            
            for item in queue[:10]:  # Limit to 10
                chapter = item.get('chapter', {})
                manga_title = chapter.get('manga', {}).get('title', 'Unknown')
                chapter_name = chapter.get('name', f"Ch. {chapter.get('chapterNumber', '?')}")
                progress = item.get('progress', 0)
                item_state = item.get('state', 'Unknown')
                
                progress_bar = "█" * int(progress / 10) + "░" * (10 - int(progress / 10))
                
                embed.add_field(
                    name=f"{manga_title}",
                    value=f"{chapter_name}\n{progress_bar} {progress:.1f}% - {item_state}",
                    inline=False
                )
            
            if len(queue) > 10:
                embed.set_footer(text=f"Showing 10 of {len(queue)} downloads")
        else:
            embed.add_field(name="Queue", value="Empty", inline=False)
        
        await ctx.respond(embed=embed)


    @slash_command(
        name="request_manga",
        description="Search for manga to download across all sources"
    )
    async def search_manga(
        self,
        ctx: discord.ApplicationContext,
        query: Option(str, "What manga are you looking for?", required=True),
        limit: Option(int, "Number of results per source", required=False, default=5, min_value=1, max_value=10),
        include_nsfw: Option(bool, "Include NSFW sources?", required=False, default=False)
    ):
        """
        Search for manga across all available sources.
        Returns interactive dropdown to select manga.
        """
        await ctx.defer()
        
        # Get all available sources
        sources_query = """
        query {
            sources(first: 50) {
                nodes {
                    id
                    name
                    displayName
                    lang
                    isNsfw
                }
            }
        }
        """
        
        sources_data = await self.bot.graphql_query(sources_query)
        
        if not sources_data or not sources_data.get('sources', {}).get('nodes'):
            await ctx.respond("❌ Failed to fetch sources.")
            return
        
        all_sources = sources_data['sources']['nodes']
        
        # Filter out NSFW sources if not requested
        if not include_nsfw:
            sources = [s for s in all_sources if not s.get('isNsfw', False)]
        else:
            sources = all_sources
        
        # Send initial status
        status_embed = discord.Embed(
            title="🔍 Searching for Manga...",
            description=f"Query: **{query}**\n\nSearching across {len(sources)} sources...",
            color=discord.Color.blue()
        )
        status_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
        status_message = await ctx.respond(embed=status_embed)
        
        # Search each source
        all_results = []
        sources_searched = 0
        
        for source in sources[:20]:  # Limit to first 20 sources to avoid timeout
            source_id = source['id']
            
            # FIXED: Added page and changed sourceId to source
            search_mutation = """
            mutation SearchSource($source: LongString!, $searchTerm: String!, $page: Int!) {
                fetchSourceManga(input: {
                    source: $source
                    type: SEARCH
                    query: $searchTerm
                    page: $page
                }) {
                    mangas {
                        id
                        title
                        thumbnailUrl
                        inLibrary
                    }
                    hasNextPage
                }
            }
            """
            
            try:
                search_data = await self.bot.graphql_query(
                    search_mutation,
                    {"source": source_id, "searchTerm": query, "page": 1}  # Changed sourceId to source, added page
                )
                
                if search_data and search_data.get('fetchSourceManga'):
                    mangas = search_data['fetchSourceManga'].get('mangas', [])
                    
                    # Add source info to each manga
                    for manga in mangas[:limit]:
                        manga['source'] = source
                        all_results.append(manga)
                
                sources_searched += 1
                
                # Update status every 5 sources
                if sources_searched % 5 == 0:
                    status_embed.description = f"Query: **{query}**\n\nSearched {sources_searched}/{len(sources[:20])} sources...\nFound {len(all_results)} results so far"
                    await status_message.edit(embed=status_embed)
                
                # Small delay to avoid overwhelming the API
                await asyncio.sleep(0.3)
                
            except Exception as e:
                # Log the specific error for debugging
                logger.debug(f"Error searching source {source.get('displayName', 'Unknown')}: {e}")
                continue
        
        # Check if we found any results
        if not all_results:
            no_results_embed = discord.Embed(
                title="❌ No Results Found",
                description=f"No manga found matching '{query}' across {sources_searched} sources.\n\n**Tips:**\n• Try different search terms\n• Check spelling\n• Try more generic terms (e.g., 'naruto' instead of 'naruto shippuden')",
                color=discord.Color.red()
            )
            no_results_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
            await status_message.edit(embed=no_results_embed)
            return
        
        # Remove duplicates based on title (keep first occurrence)
        seen_titles = set()
        unique_results = []
        for manga in all_results:
            title_lower = manga['title'].lower()
            if title_lower not in seen_titles:
                seen_titles.add(title_lower)
                unique_results.append(manga)
        
        # Sort by whether it's in library (library items first)
        unique_results.sort(key=lambda x: (not x.get('inLibrary', False), x['title']))
        
        # Create results embed
        results_embed = discord.Embed(
            title=f"📚 Search Results for '{query}'",
            description=f"Found {len(unique_results)} manga across {sources_searched} sources.\nSelect a manga from the dropdown below to see details.",
            color=discord.Color.green()
        )
        results_embed.set_thumbnail(url=SUWAYOMI_LOGO_URL)
        
        results_embed.add_field(
            name="📋 Results",
            value=f"**{min(25, len(unique_results))}** manga available in dropdown\n{'⚠️ Showing first 25 results' if len(unique_results) > 25 else ''}",
            inline=False
        )
        
        results_embed.set_footer(text="Select a manga to view details and add to library")
        
        # Create view with dropdown
        view = MangaSelectView(self.bot, unique_results)
        
        await status_message.edit(embed=results_embed, view=view)
        

def setup(bot: discord.Bot):
    """
    This function is called by py-cord when loading the cog.
    """
    bot.add_cog(SuwayomiCog(bot))
    logger.debug("SuwayomiCog registered")
