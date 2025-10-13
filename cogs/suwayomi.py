import discord
from discord.ext import commands
from discord.commands import slash_command, Option
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from discord.ui import View, Button, Select
import asyncio

logger = logging.getLogger('suwayomi_bot.cog')

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
            
            async with session.get(url, headers=headers, timeout=10) as resp:
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
            content="🔍 Loading manga details...",
            view=self
        )
        
        # Fetch full manga details with more fields
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
                chapters {
                    totalCount
                    nodes(first: 5) {
                        name
                        chapterNumber
                        uploadDate
                    }
                }
                unreadCount
                downloadCount
                chapterCount
                categories {
                    nodes {
                        name
                    }
                }
                source {
                    id
                    name
                    displayName
                    lang
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
                        attachments=[image_file]
                    )
                else:
                    await interaction.edit_original_response(
                        content=None,
                        embed=basic_embed,
                        view=button_view
                    )
                return
            
            manga = manga_details['manga']
            
            # Create detailed embed
            embed = discord.Embed(
                title=manga['title'],
                url=self.build_full_url(manga.get('realUrl')) if manga.get('realUrl') else None,
                color=discord.Color.blue() if not manga.get('inLibrary') else discord.Color.green()
            )
            
            # Add description (truncate if too long)
            description = manga.get('description', '').strip()
            if description:
                if len(description) > 800:
                    description = description[:797] + "..."
                embed.description = description
            else:
                embed.description = "*No description available*"
            
            # Fetch and attach cover image
            image_file = None
            thumbnail_url = manga.get('thumbnailUrl')
            if thumbnail_url:
                full_image_url = self.build_full_url(thumbnail_url)
                logger.info(f"Attempting to fetch cover from: {full_image_url}")
                
                # Try to fetch and attach the image
                image_file = await self.fetch_and_attach_image(full_image_url)
                if image_file:
                    # Set the embed to use the attached image
                    embed.set_image(url="attachment://manga_cover.jpg")
                    logger.info("Successfully attached manga cover image")
                else:
                    logger.warning(f"Failed to fetch image from {full_image_url}")
            
            # Author and Artist
            author = manga.get('author', '').strip() or 'Unknown'
            artist = manga.get('artist', '').strip() or 'Unknown'
            
            if author == artist or artist == 'Unknown':
                if author != 'Unknown':
                    embed.add_field(name="👤 Creator", value=author, inline=True)
            else:
                embed.add_field(name="✏️ Author", value=author, inline=True)
                embed.add_field(name="🎨 Artist", value=artist, inline=True)
            
            # Status with emoji
            status = manga.get('status', 'UNKNOWN')
            status_display = {
                "ONGOING": "📖 Ongoing",
                "COMPLETED": "✅ Completed",
                "LICENSED": "©️ Licensed",
                "PUBLISHING": "📰 Publishing",
                "HIATUS": "⏸️ On Hiatus",
                "CANCELLED": "❌ Cancelled",
                "UNKNOWN": "❓ Unknown"
            }.get(status, f"❓ {status}")
            
            embed.add_field(name="📊 Status", value=status_display, inline=True)
            
            # Chapter information
            total_chapters = manga.get('chapterCount', manga.get('chapters', {}).get('totalCount', 0))
            unread_count = manga.get('unreadCount', 0)
            download_count = manga.get('downloadCount', 0)
            
            chapter_info = f"**Total:** {total_chapters}"
            if manga.get('inLibrary'):
                if unread_count > 0:
                    chapter_info += f"\n**Unread:** {unread_count}"
                if download_count > 0:
                    chapter_info += f"\n**Downloaded:** {download_count}"
            
            embed.add_field(name="📚 Chapters", value=chapter_info, inline=True)
            
            # Genres
            genres = manga.get('genre', [])
            if genres:
                # Limit and format genres
                genre_list = genres[:12]  # Limit to 12 genres
                if len(genres) > 12:
                    genre_text = ", ".join(genre_list) + f" +{len(genres)-12} more"
                else:
                    genre_text = ", ".join(genre_list)
                embed.add_field(name="🏷️ Genres", value=genre_text, inline=False)
            
            # Latest chapters preview
            recent_chapters = manga.get('chapters', {}).get('nodes', [])
            if recent_chapters and total_chapters > 0:
                chapter_preview = []
                for ch in recent_chapters[:3]:  # Show up to 3 recent chapters
                    ch_name = ch.get('name', '').strip()
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
                categories = manga.get('categories', {}).get('nodes', [])
                cat_names = [cat['name'] for cat in categories]
                status_text = "✅ **In Library**"
                if cat_names:
                    status_text += f"\nCategories: {', '.join(cat_names)}"
                
                # Add library added date if available
                if manga.get('inLibraryAt'):
                    try:
                        # Convert timestamp to date
                        added_date = datetime.fromtimestamp(manga['inLibraryAt'] / 1000)
                        status_text += f"\nAdded: {added_date.strftime('%Y-%m-%d')}"
                    except:
                        pass
                
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
                    last_fetch = datetime.fromtimestamp(manga['lastFetchedAt'] / 1000)
                    time_ago = datetime.now() - last_fetch
                    if time_ago.days > 0:
                        footer_text += f" | Updated {time_ago.days}d ago"
                    elif time_ago.seconds > 3600:
                        footer_text += f" | Updated {time_ago.seconds // 3600}h ago"
                    else:
                        footer_text += f" | Updated recently"
                except:
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
                    attachments=[image_file]
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
    
    @discord.ui.button(label="Add to Library & Download", style=discord.ButtonStyle.success, custom_id="add_manga")
    async def add_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Handle adding manga to library and downloading."""
        await interaction.response.defer()
        
        # Disable button to prevent double-clicks
        button.disabled = True
        button.label = "Adding..."
        await interaction.edit_original_response(view=self)
        
        try:
            # Step 1: Add to library (without updateStrategy which doesn't exist in the patch)
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
            
            # Step 2: Fetch chapters to download
            chapters_query = """
            query GetChapters($mangaId: Int!) {
                manga(id: $mangaId) {
                    chapters {
                        totalCount
                        nodes {
                            id
                            chapterNumber
                            name
                        }
                    }
                }
            }
            """
            
            chapters_data = await self.bot.graphql_query(chapters_query, {"mangaId": self.manga_id})
            
            if not chapters_data or not chapters_data.get('manga'):
                button.label = "✅ Added (No Chapters)"
                button.style = discord.ButtonStyle.secondary
                await interaction.edit_original_response(view=self)
                return
            
            chapters = chapters_data['manga']['chapters']['nodes']
            total_chapters = len(chapters)
            
            if total_chapters == 0:
                button.label = "✅ Added (No Chapters)"
                button.style = discord.ButtonStyle.secondary
                await interaction.edit_original_response(view=self)
                return
            
            # Step 3: Queue all chapters for download
            chapter_ids = [ch['id'] for ch in chapters]
            
            download_mutation = """
            mutation DownloadChapters($chapterIds: [Int!]!) {
                enqueueChapterDownloads(input: { chapterIds: $chapterIds }) {
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
                await asyncio.sleep(0.5)  # Small delay between batches
            
            # Update button to show success
            if download_success:
                button.label = f"✅ Added & Queued {total_chapters} Chapters"
                button.style = discord.ButtonStyle.secondary
                
                # Create success embed
                success_embed = discord.Embed(
                    title="✅ Successfully Added to Library!",
                    description=f"**{title}**",
                    color=discord.Color.green()
                )
                
                success_embed.add_field(
                    name="📥 Download Status",
                    value=f"Queued {total_chapters} chapters for download",
                    inline=False
                )
                
                success_embed.add_field(
                    name="📝 Note on Auto-Updates",
                    value="To enable automatic chapter updates:\n" +
                          "• Go to your Suwayomi web interface\n" +
                          "• Navigate to Settings → Library\n" +
                          "• Enable automatic updates\n" +
                          "• Or use the library update commands",
                    inline=False
                )
                
                success_embed.add_field(
                    name="💡 Next Steps",
                    value="• Use `/downloads` to monitor progress\n" +
                          "• Use `/updates` to manually check for new chapters\n" +
                          "• Use `/update_library` to update all library manga\n" +
                          "• Chapters will download in the background",
                    inline=False
                )
                
                success_embed.set_footer(text=f"Manga ID: {self.manga_id}")
                
                await interaction.edit_original_response(embed=success_embed, view=self)
            else:
                button.label = f"✅ Added (Queued {downloaded_count}/{total_chapters})"
                button.style = discord.ButtonStyle.warning
                
                warning_embed = discord.Embed(
                    title="⚠️ Partially Added to Library",
                    description=f"**{title}**\n\nManga was added but some chapters couldn't be queued.",
                    color=discord.Color.yellow()
                )
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
            await interaction.edit_original_response(embed=error_embed, view=self)
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel")
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Cancel and close the view."""
        await interaction.response.edit_message(
            content="❌ Cancelled.",
            embed=None,
            view=None
        )
        self.stop()

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
            
            embed.add_field(
                name="📖 Library Manga", 
                value=f"{data.get('libraryMangas', {}).get('totalCount', 0):,}", 
                inline=True
            )
            embed.add_field(
                name="🔍 Total Manga", 
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
                name="🔌 Sources", 
                value=data.get('sources', {}).get('totalCount', 0), 
                inline=True
            )
            
            embed.set_footer(text=f"Connected to: {self.bot.config.SUWAYOMI_URL}")

            await ctx.respond(embed=embed)
            
        except Exception as e:
            logger.error("Error processing GraphQL response", exc_info=e)
            await ctx.respond("😵 An unexpected error occurred while processing the response.")

    @slash_command(
        name="manga_search",
        description="Search for manga in your library"
    )
    async def manga_search(
        self, 
        ctx: discord.ApplicationContext,
        query: Option(str, "Search query", required=True),
        in_library_only: Option(bool, "Only show manga in your library", required=False, default=True),
        limit: Option(int, "Number of results (1-25)", required=False, default=10, min_value=1, max_value=25)
    ):
        """Search for manga by title."""
        await ctx.defer()

        # Correct filter syntax with BooleanFilterInput
        graphql_query = """
        query SearchMangas($query: String!, $limit: Int!, $inLibrary: Boolean!) {
            mangas(
                filter: {
                    title: {likeInsensitive: $query}
                    inLibrary: {equalTo: $inLibrary}
                }
                first: $limit
            ) {
                totalCount
                nodes {
                    id
                    title
                    status
                    inLibrary
                    unreadCount
                    downloadCount
                    thumbnailUrl
                }
            }
        }
        """

        data = await self.bot.graphql_query(
            graphql_query, 
            {
                "query": query, 
                "limit": limit,
                "inLibrary": in_library_only
            }
        )
        
        if data is None:
            await ctx.respond("❌ Failed to search manga.")
            return

        mangas = data.get('mangas', {}).get('nodes', [])
        total_count = data.get('mangas', {}).get('totalCount', 0)
        
        if not mangas:
            location = "in your library" if in_library_only else "available"
            await ctx.respond(f"No manga found {location} matching '{query}'")
            return

        # Create embed with results
        location = "Library" if in_library_only else "All Sources"
        embed = discord.Embed(
            title=f"🔍 Search Results for '{query}'",
            description=f"Found {total_count} manga ({location})",
            color=discord.Color.blue()
        )
        
        for manga in mangas:
            status_emoji = {
                "ONGOING": "📖",
                "COMPLETED": "✅",
                "LICENSED": "©️",
                "PUBLISHING": "📰",
                "CANCELLED": "❌",
                "HIATUS": "⏸️",
                "UNKNOWN": "❓"
            }.get(manga.get('status', 'UNKNOWN'), "❓")
            
            unread = manga.get('unreadCount', 0)
            downloaded = manga.get('downloadCount', 0)
            in_lib = "📚" if manga.get('inLibrary') else "🔍"
            
            value_parts = [f"{in_lib} {status_emoji} {manga.get('status', 'Unknown')}"]
            if unread > 0:
                value_parts.append(f"📬 {unread} unread")
            if downloaded > 0:
                value_parts.append(f"⬇️ {downloaded} downloaded")
            
            embed.add_field(
                name=manga.get('title', 'Unknown'),
                value=" | ".join(value_parts),
                inline=False
            )
        
        if total_count > limit:
            embed.set_footer(text=f"Showing {limit} of {total_count} results")
        
        await ctx.respond(embed=embed)

    @slash_command(
        name="sources",
        description="List available manga sources"
    )
    async def list_sources(
        self,
        ctx: discord.ApplicationContext,
        limit: Option(int, "Number of sources to show (1-25)", required=False, default=15, min_value=1, max_value=25)
    ):
        """List available sources."""
        await ctx.defer()

        query = """
        query GetSources($limit: Int!) {
            sources(first: $limit) {
                totalCount
                nodes {
                    id
                    name
                    displayName
                    lang
                    iconUrl
                    isNsfw
                    supportsLatest
                }
            }
        }
        """

        data = await self.bot.graphql_query(query, {"limit": limit})
        
        if data is None:
            await ctx.respond("❌ Failed to fetch sources.")
            return

        sources = data.get('sources', {}).get('nodes', [])
        total_count = data.get('sources', {}).get('totalCount', 0)
        
        if not sources:
            await ctx.respond("No sources found.")
            return

        embed = discord.Embed(
            title="🔌 Manga Sources",
            description=f"Showing {len(sources)} of {total_count} sources",
            color=discord.Color.orange()
        )
        
        for source in sources[:15]:  # Limit to avoid embed limits
            name = source.get('displayName', source.get('name', 'Unknown'))
            lang = source.get('lang', 'Unknown').upper()
            
            flags = []
            if source.get('isNsfw'):
                flags.append("🔞 NSFW")
            if source.get('supportsLatest'):
                flags.append("🆕 Latest")
            
            value = f"Language: {lang}"
            if flags:
                value += f"\n{' | '.join(flags)}"
            
            embed.add_field(
                name=name,
                value=value,
                inline=True
            )
        
        if total_count > limit:
            embed.set_footer(text=f"Use /sources limit:{total_count} to see all sources")
        
        await ctx.respond(embed=embed)

    @slash_command(
        name="updates",
        description="Show update status and recent chapters"
    )
    async def check_updates(
        self,
        ctx: discord.ApplicationContext,
        limit: Option(int, "Number of recent chapters to show", required=False, default=10, min_value=1, max_value=25)
    ):
        """Show library update status and recent chapters."""
        await ctx.defer()

        # Now with correct UpdateStatus fields!
        query = """
        query UpdateInfo($limit: Int!) {
            updateStatus {
                isRunning
                completeJobs {
                    mangas {
                        id
                        title
                    }
                }
                runningJobs {
                    mangas {
                        id
                        title
                    }
                }
                pendingJobs {
                    mangas {
                        id
                        title
                    }
                }
                failedJobs {
                    mangas {
                        id
                        title
                    }
                }
            }
            chapters(first: $limit) {
                nodes {
                    id
                    name
                    chapterNumber
                    uploadDate
                    isRead
                    manga {
                        title
                    }
                }
            }
        }
        """

        data = await self.bot.graphql_query(query, {"limit": limit})
        
        if data is None:
            await ctx.respond("❌ Failed to check updates.")
            return

        update_status = data.get('updateStatus', {})
        is_updating = update_status.get('isRunning', False)
        
        embed = discord.Embed(
            title="🔄 Library Update Status",
            color=discord.Color.yellow() if is_updating else discord.Color.green()
        )
        
        # Update status info with actual counts
        if is_updating:
            running = len(update_status.get('runningJobs', {}).get('mangas', []))
            pending = len(update_status.get('pendingJobs', {}).get('mangas', []))
            complete = len(update_status.get('completeJobs', {}).get('mangas', []))
            failed = len(update_status.get('failedJobs', {}).get('mangas', []))
            
            status_parts = []
            if running > 0:
                status_parts.append(f"🔄 Running: {running}")
            if pending > 0:
                status_parts.append(f"⏳ Pending: {pending}")
            if complete > 0:
                status_parts.append(f"✅ Complete: {complete}")
            if failed > 0:
                status_parts.append(f"❌ Failed: {failed}")
            
            embed.description = "**Update in Progress**\n" + "\n".join(status_parts) if status_parts else "⚠️ Update starting..."
        else:
            complete = len(update_status.get('completeJobs', {}).get('mangas', []))
            if complete > 0:
                embed.description = f"✅ Last update: {complete} manga checked"
            else:
                embed.description = "✅ No active update"
        
        # Recent chapters
        chapters = data.get('chapters', {}).get('nodes', [])
        
        if chapters:
            embed.add_field(name="\u200b", value="**Recent Chapters:**", inline=False)
            
            for chapter in chapters[:limit]:
                manga_title = chapter.get('manga', {}).get('title', 'Unknown')
                chapter_name = chapter.get('name', f"Ch. {chapter.get('chapterNumber', '?')}")
                is_read = "✅" if chapter.get('isRead') else "📬"
                
                # Format upload date if available
                upload_date = chapter.get('uploadDate')
                date_str = ""
                if upload_date:
                    try:
                        # uploadDate is in milliseconds
                        dt = datetime.fromtimestamp(upload_date / 1000)
                        date_str = f" • {dt.strftime('%Y-%m-%d')}"
                    except:
                        pass
                
                embed.add_field(
                    name=f"{is_read} {manga_title}",
                    value=f"{chapter_name}{date_str}",
                    inline=False
                )
        else:
            embed.add_field(name="No Chapters", value="No chapters found", inline=False)
        
        await ctx.respond(embed=embed)

    @slash_command(
        name="inspect_schema",
        description="[Debug] Inspect GraphQL schema types"
    )
    async def inspect_schema(
        self,
        ctx: discord.ApplicationContext,
        type_name: Option(str, "Type name to inspect (e.g., UpdateStatus, ChapterFilterInput)", required=True)
    ):
        """Inspect a GraphQL type to see its fields."""
        await ctx.defer()

        query = """
        query InspectType($typeName: String!) {
            __type(name: $typeName) {
                name
                kind
                fields {
                    name
                    type {
                        name
                        kind
                        ofType {
                            name
                            kind
                        }
                    }
                }
                inputFields {
                    name
                    type {
                        name
                        kind
                        ofType {
                            name
                            kind
                        }
                    }
                }
            }
        }
        """

        data = await self.bot.graphql_query(query, {"typeName": type_name})
        
        if data is None:
            await ctx.respond("❌ Failed to inspect schema.")
            return

        type_info = data.get('__type')
        
        if not type_info:
            await ctx.respond(f"❌ Type '{type_name}' not found in schema.")
            return

        # Create response
        response = f"**Type: {type_info.get('name')}**\n"
        response += f"Kind: {type_info.get('kind')}\n\n"
        
        # Show fields (for OBJECT types)
        fields = type_info.get('fields', [])
        if fields:
            response += "**Fields:**\n```\n"
            for field in fields[:20]:  # Limit to 20 fields
                field_type = field.get('type', {})
                type_name_str = field_type.get('name') or field_type.get('ofType', {}).get('name', 'Unknown')
                response += f"• {field['name']}: {type_name_str}\n"
            response += "```\n"
        
        # Show input fields (for INPUT_OBJECT types)
        input_fields = type_info.get('inputFields', [])
        if input_fields:
            response += "**Input Fields:**\n```\n"
            for field in input_fields[:20]:
                field_type = field.get('type', {})
                type_name_str = field_type.get('name') or field_type.get('ofType', {}).get('name', 'Unknown')
                response += f"• {field['name']}: {type_name_str}\n"
            response += "```\n"
        
        # Split if too long
        if len(response) > 2000:
            await ctx.respond(response[:2000])
            if len(response) > 2000:
                await ctx.followup.send(response[2000:4000] if len(response) > 2000 else response[2000:])
        else:
            await ctx.respond(response)


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
        
        embed.add_field(
            name="Downloader State",
            value=f"{'🟢' if state == 'Running' else '⏸️'} {state}",
            inline=False
        )
        
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
        name="recent_manga",
        description="Show recently added manga to your library"
    )
    async def recent_manga(
        self,
        ctx: discord.ApplicationContext,
        limit: Option(int, "Number of manga to show", required=False, default=10, min_value=1, max_value=25)
    ):
        """Show recently added manga."""
        await ctx.defer()

        query = """
        query RecentManga($limit: Int!) {
            mangas(
                filter: { inLibrary: { equalTo: true } }
                first: $limit
            ) {
                nodes {
                    id
                    title
                    author
                    status
                    inLibraryAt
                    age
                    unreadCount
                }
            }
        }
        """

        data = await self.bot.graphql_query(query, {"limit": limit})
        
        if data is None:
            await ctx.respond("❌ Failed to fetch manga.")
            return

        mangas = data.get('mangas', {}).get('nodes', [])
        
        if not mangas:
            await ctx.respond("No manga in library.")
            return

        # Sort by inLibraryAt (most recent first)
        mangas.sort(key=lambda x: int(x.get('inLibraryAt', 0)), reverse=True)
        
        embed = discord.Embed(
            title="🆕 Recently Added Manga",
            color=discord.Color.green()
        )
        
        for manga in mangas[:limit]:
            title = manga.get('title', 'Unknown')
            author = manga.get('author', 'Unknown Author')
            status = manga.get('status', 'UNKNOWN')
            unread = manga.get('unreadCount', 0)
            
            value_parts = [f"by {author}", f"Status: {status}"]
            if unread > 0:
                value_parts.append(f"📬 {unread} unread")
            
            embed.add_field(
                name=title,
                value=" | ".join(value_parts),
                inline=False
            )
        
        await ctx.respond(embed=embed)

    @slash_command(
        name="manga_stats",
        description="Get detailed statistics for a specific manga"
    )
    async def manga_stats(
        self,
        ctx: discord.ApplicationContext,
        manga_title: Option(str, "Manga title (or part of it)", required=True)
    ):
        """Get detailed stats for a manga."""
        await ctx.defer()

        query = """
        query MangaStats($title: String!) {
            mangas(
                filter: {
                    title: { likeInsensitive: $title }
                    inLibrary: { equalTo: true }
                }
                first: 1
            ) {
                nodes {
                    id
                    title
                    author
                    artist
                    status
                    description
                    inLibraryAt
                    bookmarkCount
                    chapters {
                        totalCount
                    }
                    readChapters: chapters(filter: { isRead: { equalTo: true } }) {
                        totalCount
                    }
                    downloadedChapters: chapters(filter: { isDownloaded: { equalTo: true } }) {
                        totalCount
                    }
                    categories {
                        nodes {
                            name
                        }
                    }
                    trackRecords {
                        nodes {
                            tracker { name }
                            score
                            displayScore
                        }
                    }
                }
            }
        }
        """

        data = await self.bot.graphql_query(query, {"title": manga_title})
        
        if data is None:
            await ctx.respond("❌ Failed to fetch manga.")
            return

        mangas = data.get('mangas', {}).get('nodes', [])
        
        if not mangas:
            await ctx.respond(f"No manga found matching '{manga_title}' in your library.")
            return

        manga = mangas[0]
        
        embed = discord.Embed(
            title=manga.get('title', 'Unknown'),
            description=manga.get('description', 'No description')[:200] + "..." if len(manga.get('description', '')) > 200 else manga.get('description', 'No description'),
            color=discord.Color.purple()
        )
        
        # Basic info
        author = manga.get('author', 'Unknown')
        artist = manga.get('artist', 'Unknown')
        if author == artist:
            embed.add_field(name="Creator", value=author, inline=True)
        else:
            embed.add_field(name="Author", value=author, inline=True)
            embed.add_field(name="Artist", value=artist, inline=True)
        
        embed.add_field(name="Status", value=manga.get('status', 'UNKNOWN'), inline=True)
        
        # Chapter stats
        total_chapters = manga.get('chapters', {}).get('totalCount', 0)
        read_chapters = manga.get('readChapters', {}).get('totalCount', 0)
        downloaded = manga.get('downloadedChapters', {}).get('totalCount', 0)
        bookmarks = manga.get('bookmarkCount', 0)
        
        progress = (read_chapters / total_chapters * 100) if total_chapters > 0 else 0
        
        embed.add_field(
            name="📊 Progress",
            value=f"{read_chapters}/{total_chapters} chapters ({progress:.1f}%)",
            inline=False
        )
        embed.add_field(name="⬇️ Downloaded", value=str(downloaded), inline=True)
        embed.add_field(name="🔖 Bookmarks", value=str(bookmarks), inline=True)
        
        # Categories
        categories = manga.get('categories', {}).get('nodes', [])
        if categories:
            cat_names = [cat.get('name', 'Unknown') for cat in categories]
            embed.add_field(
                name="🏷️ Categories",
                value=", ".join(cat_names),
                inline=False
            )
        
        # Tracking
        tracks = manga.get('trackRecords', {}).get('nodes', [])
        if tracks:
            track_info = []
            for track in tracks:
                tracker_name = track.get('tracker', {}).get('name', 'Unknown')
                score = track.get('displayScore', track.get('score', 'N/A'))
                track_info.append(f"{tracker_name}: {score}")
            
            embed.add_field(
                name="📊 Tracking",
                value="\n".join(track_info),
                inline=False
            )
        
        await ctx.respond(embed=embed)


    @slash_command(
        name="library_by_status",
        description="Show library breakdown by manga status"
    )
    async def library_by_status(self, ctx: discord.ApplicationContext):
        """Show library breakdown by status."""
        await ctx.defer()

        query = """
        query {
            ongoing: mangas(filter: { 
                inLibrary: { equalTo: true }
                status: { equalTo: ONGOING }
            }) { totalCount }
            
            completed: mangas(filter: { 
                inLibrary: { equalTo: true }
                status: { equalTo: COMPLETED }
            }) { totalCount }
            
            hiatus: mangas(filter: { 
                inLibrary: { equalTo: true }
                status: { equalTo: HIATUS }
            }) { totalCount }
            
            cancelled: mangas(filter: { 
                inLibrary: { equalTo: true }
                status: { equalTo: CANCELLED }
            }) { totalCount }
            
            unknown: mangas(filter: { 
                inLibrary: { equalTo: true }
                status: { equalTo: UNKNOWN }
            }) { totalCount }
        }
        """

        data = await self.bot.graphql_query(query)
        
        if data is None:
            await ctx.respond("❌ Failed to fetch library stats.")
            return

        embed = discord.Embed(
            title="📊 Library by Status",
            color=discord.Color.blue()
        )
        
        statuses = [
            ("📖 Ongoing", data.get('ongoing', {}).get('totalCount', 0)),
            ("✅ Completed", data.get('completed', {}).get('totalCount', 0)),
            ("⏸️ Hiatus", data.get('hiatus', {}).get('totalCount', 0)),
            ("❌ Cancelled", data.get('cancelled', {}).get('totalCount', 0)),
            ("❓ Unknown", data.get('unknown', {}).get('totalCount', 0))
        ]
        
        total = sum(count for _, count in statuses)
        
        for status_name, count in statuses:
            percentage = (count / total * 100) if total > 0 else 0
            embed.add_field(
                name=status_name,
                value=f"{count} ({percentage:.1f}%)",
                inline=True
            )
        
        embed.set_footer(text=f"Total: {total} manga in library")
        
        await ctx.respond(embed=embed)

    @slash_command(
        name="latest_from_source",
        description="Get latest manga from a source"
    )
    async def latest_from_source(
        self,
        ctx: discord.ApplicationContext,
        source_name: Option(str, "Source name (e.g., 'MangaDex')", required=True),
        limit: Option(int, "Number of results", required=False, default=10, min_value=1, max_value=25)
    ):
        """Get latest manga from a source."""
        await ctx.defer()
        
        # Find the source
        sources_query = """
        query FindSource($sourceName: String!) {
            sources(filter: { displayName: { likeInsensitive: $sourceName } }) {
                nodes {
                    id
                    name
                    displayName
                    lang
                    supportsLatest
                }
            }
        }
        """
        
        sources_data = await self.bot.graphql_query(sources_query, {"sourceName": source_name})
        
        if not sources_data or not sources_data.get('sources', {}).get('nodes'):
            await ctx.respond(f"❌ No source found matching '{source_name}'.")
            return
        
        source = sources_data['sources']['nodes'][0]
        
        if not source.get('supportsLatest'):
            await ctx.respond(f"❌ {source['displayName']} doesn't support latest manga browsing.")
            return
        
        source_id = source['id']
        
        # FIXED: Changed sourceId to source and added page parameter
        latest_mutation = """
        mutation GetLatest($source: LongString!, $page: Int!) {
            fetchSourceManga(input: {
                source: $source
                type: LATEST
                page: $page
            }) {
                hasNextPage
                mangas {
                    id
                    title
                    thumbnailUrl
                    inLibrary
                }
            }
        }
        """
        
        latest_data = await self.bot.graphql_query(latest_mutation, {"source": source_id, "page": 1})
        
        if not latest_data or not latest_data.get('fetchSourceManga'):
            await ctx.respond(f"❌ Failed to fetch latest from {source['displayName']}.")
            return
        
        mangas = latest_data['fetchSourceManga'].get('mangas', [])
        
        if not mangas:
            await ctx.respond(f"No latest manga found on {source['displayName']}")
            return
        
        embed = discord.Embed(
            title=f"🆕 Latest from {source['displayName']}",
            description=f"Language: {source['lang'].upper()}",
            color=discord.Color.gold()
        )
        
        for i, manga in enumerate(mangas[:limit], 1):
            title = manga.get('title', 'Unknown')
            in_lib = "📚" if manga.get('inLibrary') else "➕"
            manga_id = manga.get('id', 'N/A')
            
            embed.add_field(
                name=f"{i}. {in_lib} {title}",
                value=f"ID: `{manga_id}`",
                inline=False
            )
        
        embed.set_footer(text="Use /add_manga id:<manga_id> to add to library")
        
        await ctx.respond(embed=embed)


    @slash_command(
        name="popular_from_source",
        description="Get popular manga from a source"
    )
    async def popular_from_source(
        self,
        ctx: discord.ApplicationContext,
        source_name: Option(str, "Source name (e.g., 'MangaDex')", required=True),
        limit: Option(int, "Number of results", required=False, default=10, min_value=1, max_value=25)
    ):
        """Get popular manga from a source."""
        await ctx.defer()
        
        # Find the source
        sources_query = """
        query FindSource($sourceName: String!) {
            sources(filter: { displayName: { likeInsensitive: $sourceName } }) {
                nodes {
                    id
                    name
                    displayName
                    lang
                }
            }
        }
        """
        
        sources_data = await self.bot.graphql_query(sources_query, {"sourceName": source_name})
        
        if not sources_data or not sources_data.get('sources', {}).get('nodes'):
            await ctx.respond(f"❌ No source found matching '{source_name}'.")
            return
        
        source = sources_data['sources']['nodes'][0]
        source_id = source['id']
        
        # FIXED: Changed sourceId to source and added page parameter
        popular_mutation = """
        mutation GetPopular($source: LongString!, $page: Int!) {
            fetchSourceManga(input: {
                source: $source
                type: POPULAR
                page: $page
            }) {
                hasNextPage
                mangas {
                    id
                    title
                    thumbnailUrl
                    inLibrary
                }
            }
        }
        """
        
        popular_data = await self.bot.graphql_query(popular_mutation, {"source": source_id, "page": 1})
        
        if not popular_data or not popular_data.get('fetchSourceManga'):
            await ctx.respond(f"❌ Failed to fetch popular from {source['displayName']}.")
            return
        
        mangas = popular_data['fetchSourceManga'].get('mangas', [])
        
        if not mangas:
            await ctx.respond(f"No popular manga found on {source['displayName']}")
            return
        
        embed = discord.Embed(
            title=f"🔥 Popular on {source['displayName']}",
            description=f"Language: {source['lang'].upper()}",
            color=discord.Color.red()
        )
        
        for i, manga in enumerate(mangas[:limit], 1):
            title = manga.get('title', 'Unknown')
            in_lib = "📚" if manga.get('inLibrary') else "➕"
            manga_id = manga.get('id', 'N/A')
            
            embed.add_field(
                name=f"{i}. {in_lib} {title}",
                value=f"ID: `{manga_id}`",
                inline=False
            )
        
        embed.set_footer(text="Use /add_manga id:<manga_id> to add to library and download")
        
        await ctx.respond(embed=embed)


    @slash_command(
        name="search",
        description="Search for manga across all sources"
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
            await status_message.edit(
                embed=discord.Embed(
                    title="❌ No Results Found",
                    description=f"No manga found matching '{query}' across {sources_searched} sources.\n\n**Tips:**\n• Try different search terms\n• Check spelling\n• Try more generic terms (e.g., 'naruto' instead of 'naruto shippuden')",
                    color=discord.Color.red()
                )
            )
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
        
        results_embed.add_field(
            name="📋 Results",
            value=f"**{min(25, len(unique_results))}** manga available in dropdown\n{'⚠️ Showing first 25 results' if len(unique_results) > 25 else ''}",
            inline=False
        )
        
        results_embed.set_footer(text="Select a manga to view details and add to library")
        
        # Create view with dropdown
        view = MangaSelectView(self.bot, unique_results)
        
        await status_message.edit(embed=results_embed, view=view)
        
    @slash_command(
        name="update_library",
        description="Manually trigger a library update to check for new chapters"
    )
    async def update_library(
        self,
        ctx: discord.ApplicationContext,
        category: Option(str, "Update specific category (leave empty for all)", required=False, default=None)
    ):
        """Trigger a library update."""
        await ctx.defer()
        
        try:
            # If category specified, find it first
            category_id = None
            if category:
                categories_query = """
                query FindCategory($name: String!) {
                    categories(filter: { name: { equalTo: $name } }) {
                        nodes {
                            id
                            name
                        }
                    }
                }
                """
                
                cat_data = await self.bot.graphql_query(categories_query, {"name": category})
                if cat_data and cat_data.get('categories', {}).get('nodes'):
                    category_id = cat_data['categories']['nodes'][0]['id']
                else:
                    await ctx.respond(f"❌ Category '{category}' not found.")
                    return
            
            # Start the update
            if category_id:
                update_mutation = """
                mutation UpdateCategory($categoryId: Int!) {
                    updateLibraryManga(input: { categories: [$categoryId] }) {
                        updateStatus {
                            isRunning
                            completeJobs {
                                mangas {
                                    id
                                    title
                                }
                            }
                            runningJobs {
                                mangas {
                                    id
                                    title  
                                }
                            }
                            pendingJobs {
                                mangas {
                                    id
                                    title
                                }
                            }
                        }
                    }
                }
                """
                result = await self.bot.graphql_query(update_mutation, {"categoryId": category_id})
            else:
                # Update entire library
                update_mutation = """
                mutation UpdateAllLibrary {
                    updateLibraryManga(input: {}) {
                        updateStatus {
                            isRunning
                            completeJobs {
                                mangas {
                                    id
                                    title
                                }
                            }
                            runningJobs {
                                mangas {
                                    id
                                    title
                                }
                            }
                            pendingJobs {
                                mangas {
                                    id
                                    title
                                }
                            }
                        }
                    }
                }
                """
                result = await self.bot.graphql_query(update_mutation)
            
            if not result or not result.get('updateLibraryManga'):
                await ctx.respond("❌ Failed to start library update.")
                return
            
            status = result['updateLibraryManga']['updateStatus']
            
            # Count jobs
            pending = len(status.get('pendingJobs', {}).get('mangas', []))
            running = len(status.get('runningJobs', {}).get('mangas', []))
            complete = len(status.get('completeJobs', {}).get('mangas', []))
            total = pending + running + complete
            
            embed = discord.Embed(
                title="🔄 Library Update Started",
                description=f"Updating {'category: ' + category if category else 'entire library'}",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="📊 Initial Status",
                value=f"• Total manga to check: {total}\n" +
                      f"• Pending: {pending}\n" +
                      f"• Running: {running}\n" +
                      f"• Complete: {complete}",
                inline=False
            )
            
            embed.add_field(
                name="💡 Info",
                value="• New chapters will be downloaded automatically\n" +
                      "• Use `/updates` to monitor progress\n" +
                      "• Updates run in the background",
                inline=False
            )
            
            embed.set_footer(text="This may take several minutes depending on library size")
            
            await ctx.respond(embed=embed)
            
        except Exception as e:
            logger.error(f"Error starting library update: {e}", exc_info=True)
            await ctx.respond(f"❌ Error starting update: {str(e)[:200]}")
    
    @slash_command(
        name="stop_update",
        description="Stop the currently running library update"
    )
    async def stop_update(self, ctx: discord.ApplicationContext):
        """Stop library update."""
        await ctx.defer()
        
        try:
            stop_mutation = """
            mutation StopUpdate {
                updateStop(input: {}) {
                    clientMutationId
                }
            }
            """
            
            result = await self.bot.graphql_query(stop_mutation)
            
            if result:
                await ctx.respond("⏹️ Library update stopped successfully.")
            else:
                await ctx.respond("❌ Failed to stop update (it may not be running).")
                
        except Exception as e:
            logger.error(f"Error stopping update: {e}", exc_info=True)
            await ctx.respond("❌ Error stopping update.")

def setup(bot: discord.Bot):
    """
    This function is called by py-cord when loading the cog.
    """
    bot.add_cog(SuwayomiCog(bot))
    logger.debug("SuwayomiCog registered")
