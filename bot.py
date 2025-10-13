import discord
from discord.ext import commands, tasks
import os
import logging
from dotenv import load_dotenv
import aiohttp
import asyncio
import time
from typing import Optional, Dict, Any

# Load environment variables from .env file
load_dotenv()

# --- Logging Setup ---
log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)

logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('suwayomi_bot')

# Filter out Discord's connection messages if desired
logging.getLogger('discord').setLevel(logging.WARNING)


class Config:
    """Configuration manager with validation."""
    def __init__(self):
        self.TOKEN = os.getenv("DISCORD_TOKEN")
        self.GUILD_ID = os.getenv("GUILD_ID")
        self.SUWAYOMI_URL = os.getenv("SUWAYOMI_URL", "").strip().strip('"').strip("'").rstrip('/')
        self.SUWAYOMI_API_KEY = os.getenv("SUWAYOMI_API_KEY", "").strip().strip('"').strip("'")
        
        self.validate()

    def validate(self):
        """Validate configuration values."""
        required = {'TOKEN', 'SUWAYOMI_URL', 'SUWAYOMI_API_KEY'}
        missing = [k for k, v in vars(self).items() if k in required and not v]
        
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        # Validate URL format
        if not self.SUWAYOMI_URL.startswith(('http://', 'https://')):
            raise ValueError(f"SUWAYOMI_URL must start with http:// or https://, got: {self.SUWAYOMI_URL}")
        
        # Check for common URL issues
        if '"' in self.SUWAYOMI_URL or "'" in self.SUWAYOMI_URL:
            raise ValueError(f"SUWAYOMI_URL contains quotes. Remove quotes from .env file: {self.SUWAYOMI_URL}")
        
        # Convert GUILD_ID to int if provided
        if self.GUILD_ID:
            try:
                self.GUILD_ID = int(self.GUILD_ID)
            except ValueError:
                raise ValueError("GUILD_ID must be a numeric value")


class SuwayomiBot(discord.Bot):
    """Extended Discord bot with Suwayomi integration."""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(intents=intents)

        # Initialize bot attributes    
        self.config = Config()
        self.session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        
        # Command sync flag
        self.synced = False
        
        # Token refresh management
        self.token_refresh_task_running = False
        
        logger.info("SuwayomiBot initialized")

    async def setup_hook(self):
        """
        This is called once the bot logs in.
        Setup resources before the bot fully starts.
        """
        try:
            logger.debug("Running setup_hook...")
            
            # Create a single, shared aiohttp session
            self.session = await self.ensure_session()
            logger.debug("Aiohttp session created")
            
            # Test GraphQL connection
            if await self.test_graphql_connection():
                logger.debug("✅ Successfully connected to Suwayomi GraphQL API")
            else:
                logger.warning("⚠️ Could not connect to Suwayomi - check your configuration")
            
            logger.debug("Setup hook completed successfully")
            
        except Exception as e:
            logger.error(f"Error in setup_hook: {e}", exc_info=True)
            raise

    async def on_ready(self):
        """Called when the bot is connected and ready."""
        logger.info(f'{self.user} has connected to Discord!')
        logger.debug(f'Bot ID: {self.user.id}')
        
        # Load cogs AFTER bot is ready
        self.load_all_cogs()
        
        loaded_cogs = list(self.cogs.keys())
        logger.debug(f"Loaded cogs: {loaded_cogs}")
        
        # Sync commands only once
        if not self.synced:
            try:
                if self.config.GUILD_ID:
                    # Sync to specific guild (faster for testing)
                    guild = self.get_guild(self.config.GUILD_ID)
                    if guild:
                        synced = await self.sync_commands(guild_ids=[guild.id])
                        if synced is not None:
                            logger.debug(f"✅ Synced {len(synced)} commands to guild: {guild.name}")
                        else:
                            logger.debug("⚠️ Command sync returned None - Either command sync has run recently or this may indicate an issue")
                    else:
                        logger.error(f"Could not find guild with ID {self.config.GUILD_ID}")
                else:
                    # Sync globally (takes up to 1 hour to propagate)
                    synced = await self.sync_commands()
                    if synced is not None:
                        logger.debug(f"✅ Synced {len(synced)} commands globally")
                    else:
                        logger.debug("⚠️ Command sync returned None - Either command sync has run recently or this may indicate an issue")
                
                self.synced = True
                
                # Log all available commands
                all_commands = [cmd.name for cmd in self.application_commands]
                logger.info(f"Available commands: {all_commands}")
                
            except Exception as e:
                logger.error(f"Failed to sync commands: {e}", exc_info=True)
        
        # Start periodic token refresh if not already running
        if not self.token_refresh_task_running:
            self.refresh_graphql_task.start()
            self.token_refresh_task_running = True

    def load_all_cogs(self):
        """Load all cogs from the cogs directory."""
        cogs_to_load = [
            'cogs.suwayomi'
        ]
        
        for cog in cogs_to_load:
            try:
                self.load_extension(cog)
                logger.debug(f"✅ Loaded cog: {cog}")
            except Exception as e:
                logger.error(f"❌ Failed to load {cog}: {e}", exc_info=True)

    async def ensure_session(self) -> aiohttp.ClientSession:
        """Ensure an active session exists."""
        async with self._session_lock:
            if self.session is None or self.session.closed:
                connector = aiohttp.TCPConnector(
                    limit=10,
                    limit_per_host=5,
                    ttl_dns_cache=300,
                    force_close=False,
                    enable_cleanup_closed=True,
                    keepalive_timeout=75
                )
                
                self.session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(
                        total=30,
                        connect=5,
                        sock_read=30
                    ),
                    headers={
                        "User-Agent": "SuwayomiBot/1.0",
                        "Accept": "application/json",
                        "Connection": "keep-alive"
                    }
                )
                logger.debug("Created new aiohttp session")
            return self.session

    async def test_graphql_connection(self) -> bool:
        """Test the GraphQL connection."""
        query = """
        query {
            aboutServer {
                name
                version
            }
        }
        """
        
        try:
            result = await self.graphql_query(query)
            if result:
                server_info = result.get('aboutServer', {})
                logger.info(f"Connected to {server_info.get('name', 'Suwayomi')} v{server_info.get('version', 'unknown')}")
                return True
        except Exception as e:
            logger.error(f"GraphQL connection test failed: {e}")
        
        return False

    async def graphql_query(self, query: str, variables: dict = None) -> Optional[Dict[str, Any]]:
        """
        Execute a GraphQL query against the Suwayomi server.
        
        Args:
            query: The GraphQL query string
            variables: Optional variables for the query
            
        Returns:
            The JSON response data or None if error
        """
        # Store the working endpoint after first successful connection
        if not hasattr(self, '_working_endpoint'):
            self._working_endpoint = None
        
        # Try both possible GraphQL endpoint paths
        endpoints = []
        
        # If we have a working endpoint, try it first
        if self._working_endpoint:
            endpoints.append(self._working_endpoint)
        
        # Add both possible endpoints
        base_endpoints = [
            f"{self.config.SUWAYOMI_URL}/api/graphql",  # Most likely
            f"{self.config.SUWAYOMI_URL}/graphql"       # Alternative
        ]
        
        for endpoint in base_endpoints:
            if endpoint not in endpoints:
                endpoints.append(endpoint)
        
        headers = {
            "Authorization": f"Bearer {self.config.SUWAYOMI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        
        session = await self.ensure_session()
        
        # Try each endpoint
        for graphql_url in endpoints:
            try:
                logger.debug(f"Trying GraphQL endpoint: {graphql_url}")
                
                # Create a new session if the old one might be stale
                if session.closed:
                    session = await self.ensure_session()
                
                async with session.post(
                    graphql_url, 
                    json=payload, 
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15, connect=5)  # Increased timeout
                ) as response:
                    if response.status == 404:
                        logger.debug(f"Endpoint {graphql_url} not found, trying next...")
                        continue
                        
                    if response.status == 200:
                        result = await response.json()
                        if "errors" in result:
                            logger.error(f"GraphQL errors: {result['errors']}")
                            # Don't return None immediately - the query might still have returned partial data
                            if not result.get("data"):
                                return None
                        
                        # Store the working endpoint for future use
                        self._working_endpoint = graphql_url
                        logger.debug(f"✅ Using GraphQL endpoint: {graphql_url}")
                        return result.get("data")
                    else:
                        logger.error(f"GraphQL request failed with status {response.status}")
                        text = await response.text()
                        logger.error(f"Response: {text}")
                        
            except asyncio.TimeoutError:
                logger.debug(f"Timeout on {graphql_url}, trying next endpoint...")
                continue
            except aiohttp.ClientError as e:
                logger.debug(f"Connection error on {graphql_url}: {e}")
                # Reset session on connection errors
                self.session = None
                session = await self.ensure_session()
                continue
            except Exception as e:
                logger.debug(f"Error trying {graphql_url}: {e}")
                continue
        
        # If we get here, no endpoint worked
        logger.error("❌ Could not find working GraphQL endpoint. Possible causes:")
        logger.error("1. Suwayomi server is not responding")
        logger.error("2. Network connectivity issues")
        logger.error("3. Authentication is failing")
        logger.error(f"Tried endpoints: {endpoints}")
        
        # Reset the working endpoint since it failed
        self._working_endpoint = None
        
        return None

    @tasks.loop(minutes=30)
    async def refresh_graphql_task(self):
        """Periodically test the GraphQL connection."""
        logger.debug("Running periodic GraphQL connection check...")
        await self.test_graphql_connection()

    @refresh_graphql_task.before_loop
    async def before_refresh_task(self):
        """Wait until the bot is ready before starting refresh task."""
        await self.wait_until_ready()

    async def close(self):
        """Cleanup resources on shutdown."""
        logger.info("Shutting down bot...")
        
        if self.session and not self.session.closed:
            await self.session.close()
            logger.debug("Closed aiohttp session")
        
        await super().close()


async def main():
    """Main entry point for the bot."""
    bot = SuwayomiBot()
    
    try:
        logger.debug("Starting bot...")
        await bot.start(bot.config.TOKEN)
    except KeyboardInterrupt:
        logger.debug("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Error running bot: {e}", exc_info=True)
    finally:
        if bot.session and not bot.session.closed:
            await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
