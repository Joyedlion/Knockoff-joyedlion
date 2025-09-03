import os
import discord
import requests
import asyncio
import datetime
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button
from discord import ButtonStyle, Embed, Interaction
from keep_alive import keep_alive
keep_alive()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))              # Channel for YouTube live messages
ROLE_ID = int(os.getenv("STREAM_PING_ROLE_ID"))                # Role to ping on YouTube live
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")

VERIFY_CHANNEL_ID = int(os.getenv("VERIFY_CHANNEL_ID"))        # Channel where verification messages are sent
VERIFY_ROLE_ID = int(os.getenv("DISCORD_VERIFY_ROLE_ID"))              # Role given on verification
STAFF_LOG_CHANNEL_ID = int(os.getenv("STAFF_LOG_CHANNEL_ID"))  # Channel for staff logs
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID"))      # Category where ticket channels are created
SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID"))            # Role to ping when tickets are created
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID"))      # Channel where welcome messages are sent

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

client = commands.Bot(command_prefix="!", intents=intents)

# We'll use the command tree for slash commands
tree = client.tree

was_live = False

# --- YouTube Stream check ---

async def is_stream_live():
    if not YOUTUBE_API_KEY or not YOUTUBE_CHANNEL_ID:
        print("YouTube API key or channel ID not configured")
        return False

    try:
        # Check for live broadcasts on the YouTube channel
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "channelId": YOUTUBE_CHANNEL_ID,
            "eventType": "live",
            "type": "video",
            "key": YOUTUBE_API_KEY,
            "maxResults": 1
        }
        
        response = requests.get(url, params=params)
        data = response.json()
        
        if "items" in data and len(data["items"]) > 0:
            return True, data["items"][0]["id"]["videoId"]  # Return live status and video ID
        return False, None
    except Exception as e:
        print(f"Error checking YouTube stream status: {e}")
        return False, None

# --- Verification View ---

class VerifyView(View):
    def __init__(self, member):
        super().__init__(timeout=60)
        self.member = member
        self.verified = False

    @discord.ui.button(label="Click me to verify!", style=ButtonStyle.success)
    async def verify_button(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("This button isn't for you!", ephemeral=True)
            return

        role = interaction.guild.get_role(VERIFY_ROLE_ID)
        await self.member.add_roles(role)
        self.verified = True

        embed = Embed(
            title="Verification Passed",
            description=f"{self.member.mention} verified successfully.",
            color=0x57F287,
            timestamp=datetime.datetime.utcnow()
        )
        staff_channel = interaction.guild.get_channel(STAFF_LOG_CHANNEL_ID)
        await staff_channel.send(embed=embed)

        await interaction.response.send_message("You have been verified!", ephemeral=True)
        self.stop()

    async def on_timeout(self):
        if not self.verified:
            try:
                await self.member.kick(reason="Failed to verify in time")
                embed = Embed(
                    title="Verification Failed",
                    description=f"{self.member.mention} failed to verify and was kicked.",
                    color=0xED4245,
                    timestamp=datetime.datetime.utcnow()
                )
                staff_channel = self.member.guild.get_channel(STAFF_LOG_CHANNEL_ID)
                await staff_channel.send(embed=embed)
            except Exception as e:
                print(f"Error kicking user: {e}")

# --- Events ---

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await client.change_presence(
        status=discord.Status.do_not_disturb,
        activity=discord.Activity(type=discord.ActivityType.watching, name="JoyedLion on YouTube")
    )
    load_reaction_roles()  # Load saved reaction roles
    client.loop.create_task(check_stream_status())
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} commands:")
        for command in synced:
            print(f"  - {command.name}: {command.description}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@client.event
async def on_member_join(member):
    print(f"Member joined: {member.name} (ID: {member.id})")
    
    # Send welcome message
    welcome_channel = client.get_channel(WELCOME_CHANNEL_ID)
    if welcome_channel:
        try:
            welcome_embed = Embed(
                title="🎉 Welcome to the Server!",
                description=f"Welcome to the server {member.display_name}!",
                color=0x57F287,
                timestamp=datetime.datetime.utcnow()
            )
            welcome_embed.set_thumbnail(url=member.display_avatar.url)
            welcome_embed.set_image(url="https://media.giphy.com/media/Cmr1OMJ2FN0B2/giphy.gif")  # Welcome GIF
            welcome_embed.add_field(
                name="🚀 Getting Started",
                value="Make sure to read the rules and verify yourself to get full access to the server!",
                inline=False
            )
            
            await welcome_channel.send(f"Welcome to the server {member.mention}!", embed=welcome_embed)
            print(f"Welcome message sent successfully for {member.name}")
        except Exception as e:
            print(f"ERROR: Failed to send welcome message: {e}")
    else:
        print(f"ERROR: Welcome channel {WELCOME_CHANNEL_ID} not found!")
    
    # Send verification message
    print(f"Looking for verify channel with ID: {VERIFY_CHANNEL_ID}")
    
    verify_channel = client.get_channel(VERIFY_CHANNEL_ID)
    if not verify_channel:
        print(f"ERROR: Verify channel {VERIFY_CHANNEL_ID} not found!")
        # Try to find the channel by name as backup
        for guild_channel in member.guild.channels:
            if guild_channel.name.lower() in ['verify', 'verification']:
                print(f"Found potential verify channel: {guild_channel.name} (ID: {guild_channel.id})")
        return

    try:
        embed = Embed(
            title="Verification Required",
            description="Click the button below to verify and gain access to the server.",
            color=0x5865F2
        )
        view = VerifyView(member)
        await verify_channel.send(f"{member.mention}", embed=embed, view=view)
        print(f"Verification message sent successfully for {member.name}")
    except Exception as e:
        print(f"ERROR: Failed to send verification message: {e}")
        print(f"Channel permissions: {verify_channel.permissions_for(member.guild.me)}")

# --- YouTube Stream Check Loop ---

async def check_stream_status():
    global was_live
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        print(f"YouTube notification channel {CHANNEL_ID} not found!")
        return

    while not client.is_closed():
        try:
            live, video_id = await is_stream_live()
            if live and not was_live:
                was_live = True
                youtube_url = f"https://youtube.com/watch?v={video_id}" if video_id else "https://youtube.com/@joyedlion"
                await channel.send(
                    f"<@&{ROLE_ID}> JoyedLion is now LIVE on YouTube!\nCheck it out: {youtube_url}"
                )
            elif not live and was_live:
                was_live = False
        except Exception as e:
            print(f"Error checking YouTube stream status: {e}")
        await asyncio.sleep(60)  # Check every 60 seconds

# --- Slash commands ---

def is_staff(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.manage_messages

@tree.command(name="warn", description="Warn a user")
@app_commands.check(is_staff)
async def warn(interaction: discord.Interaction, member: discord.Member, *, reason: str = "No reason provided"):
    embed = Embed(
        title="User Warned",
        description=f"{member.mention} was warned by {interaction.user.mention}",
        color=0xFEE75C,
        timestamp=datetime.datetime.utcnow()
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    staff_channel = interaction.guild.get_channel(STAFF_LOG_CHANNEL_ID)
    if staff_channel:
        await staff_channel.send(embed=embed)
    await interaction.response.send_message(f"{member.mention} has been warned.", ephemeral=True)

@tree.command(name="mute", description="Mute a user (adds Muted role) with optional time limit")
@app_commands.describe(
    member="The user to mute",
    duration="Duration (e.g., 10s, 5m, 2h) - leave empty for permanent mute",
    reason="Reason for the mute"
)
@app_commands.check(is_staff)
async def mute(interaction: discord.Interaction, member: discord.Member, duration: str = None, *, reason: str = "No reason provided"):
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not muted_role:
        await interaction.response.send_message("Muted role does not exist.", ephemeral=True)
        return
    
    # Parse duration if provided
    unmute_time = None
    duration_seconds = 0
    duration_display = "Permanent"
    
    if duration:
        try:
            # Parse duration string (e.g., "10s", "5m", "2h")
            duration = duration.lower().strip()
            if duration.endswith('s'):
                duration_seconds = int(duration[:-1])
                duration_display = f"{duration_seconds} seconds"
            elif duration.endswith('m'):
                duration_seconds = int(duration[:-1]) * 60
                duration_display = f"{int(duration[:-1])} minutes"
            elif duration.endswith('h'):
                duration_seconds = int(duration[:-1]) * 3600
                duration_display = f"{int(duration[:-1])} hours"
            else:
                # Try to parse as just a number (assume minutes)
                duration_seconds = int(duration) * 60
                duration_display = f"{duration} minutes"
            
            unmute_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)
        except (ValueError, IndexError):
            await interaction.response.send_message("Invalid duration format! Use format like: 10s, 5m, 2h", ephemeral=True)
            return
    
    await member.add_roles(muted_role, reason=reason)
    
    embed = Embed(
        title="User Muted",
        description=f"{member.mention} was muted by {interaction.user.mention}",
        color=0xFAA61A,
        timestamp=datetime.datetime.utcnow()
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Duration", value=duration_display, inline=True)
    
    if unmute_time:
        embed.add_field(name="Unmute Time", value=f"<t:{int(unmute_time.timestamp())}:F>", inline=True)
    
    staff_channel = interaction.guild.get_channel(STAFF_LOG_CHANNEL_ID)
    if staff_channel:
        await staff_channel.send(embed=embed)
    
    response_message = f"{member.mention} has been muted"
    if duration:
        response_message += f" for {duration_display}"
    response_message += "."
    
    await interaction.response.send_message(response_message, ephemeral=True)
    
    # Schedule automatic unmute if duration was specified
    if unmute_time and duration_seconds > 0:
        async def auto_unmute():
            await asyncio.sleep(duration_seconds)
            try:
                # Check if user still has the muted role
                if muted_role in member.roles:
                    await member.remove_roles(muted_role, reason="Automatic unmute - time expired")
                    
                    # Log the automatic unmute
                    unmute_embed = Embed(
                        title="User Automatically Unmuted",
                        description=f"{member.mention} was automatically unmuted (time expired)",
                        color=0x57F287,
                        timestamp=datetime.datetime.utcnow()
                    )
                    unmute_embed.add_field(name="Original Duration", value=duration_display, inline=True)
                    
                    if staff_channel:
                        await staff_channel.send(embed=unmute_embed)
                    
                    print(f"Automatically unmuted {member.name} after {duration_display}")
            except Exception as e:
                print(f"Error during automatic unmute for {member.name}: {e}")
        
        # Create the auto-unmute task
        client.loop.create_task(auto_unmute())

@tree.command(name="unmute", description="Unmute a user (removes Muted role)")
@app_commands.check(is_staff)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not muted_role:
        await interaction.response.send_message("Muted role does not exist.", ephemeral=True)
        return
    await member.remove_roles(muted_role)
    embed = Embed(
        title="User Unmuted",
        description=f"{member.mention} was unmuted by {interaction.user.mention}",
        color=0x57F287,
        timestamp=datetime.datetime.utcnow()
    )
    staff_channel = interaction.guild.get_channel(STAFF_LOG_CHANNEL_ID)
    if staff_channel:
        await staff_channel.send(embed=embed)
    await interaction.response.send_message(f"{member.mention} has been unmuted.", ephemeral=True)

@tree.command(name="kick", description="Kick a user")
@app_commands.check(is_staff)
async def kick(interaction: discord.Interaction, member: discord.Member, *, reason: str = "No reason provided"):
    try:
        await member.kick(reason=reason)
        embed = Embed(
            title="User Kicked",
            description=f"{member.mention} was kicked by {interaction.user.mention}",
            color=0xED4245,
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        staff_channel = interaction.guild.get_channel(STAFF_LOG_CHANNEL_ID)
        if staff_channel:
            await staff_channel.send(embed=embed)
        await interaction.response.send_message(f"{member.mention} has been kicked.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Failed to kick: {e}", ephemeral=True)

@tree.command(name="ban", description="Ban a user")
@app_commands.check(is_staff)
async def ban(interaction: discord.Interaction, member: discord.Member, *, reason: str = "No reason provided"):
    try:
        await member.ban(reason=reason)
        embed = Embed(
            title="User Banned",
            description=f"{member.mention} was banned by {interaction.user.mention}",
            color=0xED4245,
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        staff_channel = interaction.guild.get_channel(STAFF_LOG_CHANNEL_ID)
        if staff_channel:
            await staff_channel.send(embed=embed)
        await interaction.response.send_message(f"{member.mention} has been banned.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Failed to ban: {e}", ephemeral=True)

@tree.command(name="verify", description="Send a verification embed to a channel")
@app_commands.check(is_staff)
async def verify_command(interaction: discord.Interaction, channel: discord.TextChannel):
    embed = Embed(
        title="Verification Required",
        description="Click the button below to verify and gain access to the server.",
        color=0x5865F2
    )
    
    class VerifyButtonView(View):
        def __init__(self):
            super().__init__(timeout=None)
        
        @discord.ui.button(label="✅ Verify", style=ButtonStyle.success)
        async def verify_button(self, interaction: Interaction, button: Button):
            role = interaction.guild.get_role(VERIFY_ROLE_ID)
            if not role:
                await interaction.response.send_message("Verification role not found.", ephemeral=True)
                return
            
            if role in interaction.user.roles:
                await interaction.response.send_message("You are already verified!", ephemeral=True)
                return
            
            await interaction.user.add_roles(role)
            
            embed = Embed(
                title="User Verified",
                description=f"{interaction.user.mention} verified successfully.",
                color=0x57F287,
                timestamp=datetime.datetime.utcnow()
            )
            staff_channel = interaction.guild.get_channel(STAFF_LOG_CHANNEL_ID)
            if staff_channel:
                await staff_channel.send(embed=embed)
            
            await interaction.response.send_message("You have been verified! Welcome to the server!", ephemeral=True)
    
    view = VerifyButtonView()
    await channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"Verification embed sent to {channel.mention}!", ephemeral=True)

# --- Ticket System ---

class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🎫 Create Ticket", style=ButtonStyle.primary, emoji="🎫")
    async def create_ticket(self, interaction: Interaction, button: Button):
        guild = interaction.guild
        user = interaction.user
        
        # Check if user already has a ticket
        category = guild.get_channel(TICKET_CATEGORY_ID)
        if category:
            for channel in category.channels:
                if channel.name == f"ticket-{user.name.lower()}":
                    await interaction.response.send_message("You already have an open ticket!", ephemeral=True)
                    return
        
        # Respond to interaction immediately to prevent timeout
        await interaction.response.send_message("Creating your ticket... Please wait!", ephemeral=True)
        
        # Create ticket channel - ticket creator, support role, and mods can see it
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True)  # Ticket creator can see
        }
        
        # Add support role permissions
        support_role = guild.get_role(SUPPORT_ROLE_ID)
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        # Add staff/mod permissions
        for role in guild.roles:
            if role.permissions.manage_messages:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        try:
            ticket_channel = await category.create_text_channel(
                name=f"ticket-{user.name.lower()}",
                overwrites=overwrites
            )
            
            # Ping support role and notify about ticket
            support_role = guild.get_role(SUPPORT_ROLE_ID)
            ping_message = f"<@&{SUPPORT_ROLE_ID}>" if support_role else "@here"
            
            embed = Embed(
                title="🎫 New Support Ticket",
                description=f"**User:** {user.mention} ({user.display_name})\n**User ID:** {user.id}\n\nSupport will be with you shortly!",
                color=0x5865F2,
                timestamp=datetime.datetime.utcnow()
            )
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="Use the button below to close this ticket")
            
            class TicketControlView(View):
                def __init__(self):
                    super().__init__(timeout=None)
                    self.claimed_by = None
                
                @discord.ui.button(label="🎯 Claim Ticket", style=ButtonStyle.success)
                async def claim_ticket(self, interaction: Interaction, button: Button):
                    if not interaction.user.guild_permissions.manage_messages:
                        await interaction.response.send_message("Only staff can claim tickets!", ephemeral=True)
                        return
                    
                    if self.claimed_by:
                        await interaction.response.send_message(f"This ticket is already claimed by {self.claimed_by.mention}!", ephemeral=True)
                        return
                    
                    self.claimed_by = interaction.user
                    button.label = f"✅ Claimed by {interaction.user.display_name}"
                    button.disabled = True
                    button.style = ButtonStyle.secondary
                    
                    # Update channel permissions to only allow claimed staff member to type
                    overwrites = ticket_channel.overwrites
                    overwrites[interaction.user] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                    
                    # Remove send_messages permission from support role (they can still read)
                    support_role = interaction.guild.get_role(SUPPORT_ROLE_ID)
                    if support_role:
                        overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=False)
                    
                    # Remove send_messages from other staff (except the claimer)
                    for role in interaction.guild.roles:
                        if role.permissions.manage_messages and role != support_role:
                            existing_overwrite = overwrites.get(role, discord.PermissionOverwrite())
                            existing_overwrite.send_messages = False
                            overwrites[role] = existing_overwrite
                    
                    await ticket_channel.edit(overwrites=overwrites)
                    
                    claim_embed = Embed(
                        title="🎯 Ticket Claimed",
                        description=f"This ticket has been claimed by {interaction.user.mention}",
                        color=0x57F287,
                        timestamp=datetime.datetime.utcnow()
                    )
                    
                    await interaction.response.edit_message(view=self)
                    await ticket_channel.send(embed=claim_embed)
                
                @discord.ui.button(label="🔒 Close Ticket", style=ButtonStyle.danger)
                async def close_ticket(self, interaction: Interaction, button: Button):
                    if not interaction.user.guild_permissions.manage_messages:
                        await interaction.response.send_message("Only staff can close tickets!", ephemeral=True)
                        return
                    
                    # Store references we need
                    current_channel = interaction.channel
                    current_user = interaction.user
                    claimed_by = self.claimed_by
                    
                    class ConfirmCloseView(View):
                        def __init__(self):
                            super().__init__(timeout=60)
                        
                        @discord.ui.button(label="✅ Confirm Close", style=ButtonStyle.danger)
                        async def confirm_close(self, button_interaction: Interaction, button: Button):
                            await button_interaction.response.send_message("Closing ticket in 3 seconds...", ephemeral=True)
                            
                            # Log ticket closure
                            staff_channel = button_interaction.guild.get_channel(STAFF_LOG_CHANNEL_ID)
                            if staff_channel:
                                log_embed = Embed(
                                    title="Ticket Closed",
                                    description=f"Ticket {current_channel.mention} was closed by {current_user.mention}",
                                    color=0xED4245,
                                    timestamp=datetime.datetime.utcnow()
                                )
                                if claimed_by:
                                    log_embed.add_field(name="Claimed by", value=claimed_by.mention, inline=True)
                                log_embed.add_field(name="Original User", value=f"<@{user.id}>", inline=True)
                                await staff_channel.send(embed=log_embed)
                            
                            await asyncio.sleep(3)
                            await current_channel.delete()
                        
                        @discord.ui.button(label="❌ Cancel", style=ButtonStyle.secondary)
                        async def cancel_close(self, button_interaction: Interaction, button: Button):
                            await button_interaction.response.send_message("Ticket closure cancelled.", ephemeral=True)
                    
                    confirm_view = ConfirmCloseView()
                    await interaction.response.send_message("Are you sure you want to close this ticket?", view=confirm_view, ephemeral=True)
            
            control_view = TicketControlView()
            await ticket_channel.send(f"{ping_message}", embed=embed, view=control_view)
            
            # Log ticket creation
            staff_channel = guild.get_channel(STAFF_LOG_CHANNEL_ID)
            if staff_channel:
                log_embed = Embed(
                    title="New Ticket Created",
                    description=f"{user.mention} created a ticket: {ticket_channel.mention}",
                    color=0x5865F2,
                    timestamp=datetime.datetime.utcnow()
                )
                await staff_channel.send(embed=log_embed)
            
            # Edit the original response to show success
            await interaction.edit_original_response(content=f"Ticket created! Check {ticket_channel.mention} - staff have been notified!")
            
        except Exception as e:
            # Edit the original response to show error
            await interaction.edit_original_response(content=f"Failed to create ticket: {e}")

@tree.command(name="ticket", description="Send a ticket creation embed to a channel")
@app_commands.check(is_staff)
async def ticket_command(interaction: discord.Interaction, channel: discord.TextChannel):
    embed = Embed(
        title="🎫 Need Help?",
        description="Click the button below to create a support ticket. Staff will be notified and assist you as soon as possible!",
        color=0x5865F2
    )
    embed.add_field(
        name="What happens when you create a ticket?",
        value="• A private support channel will be created\n• Staff will be pinged and notified\n• Only moderators can see your ticket\n• Support will arrive shortly to help you",
        inline=False
    )
    
    view = TicketView()
    await channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"Ticket embed sent to {channel.mention}!", ephemeral=True)

@tree.command(name="closeticket", description="Close a ticket by name")
@app_commands.check(is_staff)
async def closeticket_command(interaction: discord.Interaction, ticket_name: str):
    guild = interaction.guild
    category = guild.get_channel(TICKET_CATEGORY_ID)
    
    if not category:
        await interaction.response.send_message("Ticket category not found!", ephemeral=True)
        return
    
    # Find the ticket channel
    ticket_channel = None
    for channel in category.channels:
        if channel.name.lower() == ticket_name.lower() or channel.name == f"ticket-{ticket_name.lower()}":
            ticket_channel = channel
            break
    
    if not ticket_channel:
        await interaction.response.send_message(f"Ticket `{ticket_name}` not found!", ephemeral=True)
        return
    
    # Extract username from ticket name if it's in format ticket-username
    username = None
    if ticket_channel.name.startswith("ticket-"):
        try:
            username = ticket_channel.name.split("-", 1)[1]
        except:
            pass
    
    # Log ticket closure
    staff_channel = guild.get_channel(STAFF_LOG_CHANNEL_ID)
    if staff_channel:
        log_embed = Embed(
            title="Ticket Closed (Command)",
            description=f"Ticket {ticket_channel.mention} was closed by {interaction.user.mention} using /closeticket",
            color=0xED4245,
            timestamp=datetime.datetime.utcnow()
        )
        if username:
            log_embed.add_field(name="Original User", value=f"@{username}", inline=True)
        await staff_channel.send(embed=log_embed)
    
    await interaction.response.send_message(f"Closing ticket {ticket_channel.mention} in 3 seconds...", ephemeral=True)
    await asyncio.sleep(3)
    await ticket_channel.delete()

@tree.command(name="openticket", description="Create a ticket for a specific user")
@app_commands.check(is_staff)
async def openticket_command(interaction: discord.Interaction, user: discord.Member, reason: str = "Staff opened ticket"):
    guild = interaction.guild
    category = guild.get_channel(TICKET_CATEGORY_ID)
    
    if not category:
        await interaction.response.send_message("Ticket category not found!", ephemeral=True)
        return
    
    # Check if user already has a ticket
    for channel in category.channels:
        if channel.name == f"ticket-{user.name.lower()}":
            await interaction.response.send_message(f"{user.mention} already has an open ticket: {channel.mention}!", ephemeral=True)
            return
    
    # Create ticket channel
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    # Add support role permissions
    support_role = guild.get_role(SUPPORT_ROLE_ID)
    if support_role:
        overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    
    # Add staff/mod permissions
    for role in guild.roles:
        if role.permissions.manage_messages:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    
    try:
        ticket_channel = await category.create_text_channel(
            name=f"ticket-{user.name.lower()}",
            overwrites=overwrites
        )
        
        embed = Embed(
            title="🎫 Support Ticket (Staff Created)",
            description=f"**User:** {user.mention} ({user.display_name})\n**User ID:** {user.id}\n**Opened by:** {interaction.user.mention}\n**Reason:** {reason}",
            color=0x5865F2,
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Use the button below to close this ticket")
        
        class TicketControlView(View):
            def __init__(self):
                super().__init__(timeout=None)
                self.claimed_by = None
            
            @discord.ui.button(label="🎯 Claim Ticket", style=ButtonStyle.success)
            async def claim_ticket(self, interaction: Interaction, button: Button):
                if not interaction.user.guild_permissions.manage_messages:
                    await interaction.response.send_message("Only staff can claim tickets!", ephemeral=True)
                    return
                
                if self.claimed_by:
                    await interaction.response.send_message(f"This ticket is already claimed by {self.claimed_by.mention}!", ephemeral=True)
                    return
                
                self.claimed_by = interaction.user
                button.label = f"✅ Claimed by {interaction.user.display_name}"
                button.disabled = True
                button.style = ButtonStyle.secondary
                
                # Update channel permissions
                overwrites = ticket_channel.overwrites
                overwrites[interaction.user] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                
                support_role = interaction.guild.get_role(SUPPORT_ROLE_ID)
                if support_role:
                    overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=False)
                
                for role in interaction.guild.roles:
                    if role.permissions.manage_messages and role != support_role:
                        existing_overwrite = overwrites.get(role, discord.PermissionOverwrite())
                        existing_overwrite.send_messages = False
                        overwrites[role] = existing_overwrite
                
                await ticket_channel.edit(overwrites=overwrites)
                
                claim_embed = Embed(
                    title="🎯 Ticket Claimed",
                    description=f"This ticket has been claimed by {interaction.user.mention}",
                    color=0x57F287,
                    timestamp=datetime.datetime.utcnow()
                )
                
                await interaction.response.edit_message(view=self)
                await ticket_channel.send(embed=claim_embed)
            
            @discord.ui.button(label="🔒 Close Ticket", style=ButtonStyle.danger)
            async def close_ticket(self, interaction: Interaction, button: Button):
                if not interaction.user.guild_permissions.manage_messages:
                    await interaction.response.send_message("Only staff can close tickets!", ephemeral=True)
                    return
                
                # Store references we need
                current_channel = interaction.channel
                current_user = interaction.user
                claimed_by = self.claimed_by
                
                class ConfirmCloseView(View):
                    def __init__(self):
                        super().__init__(timeout=60)
                    
                    @discord.ui.button(label="✅ Confirm Close", style=ButtonStyle.danger)
                    async def confirm_close(self, button_interaction: Interaction, button: Button):
                        await button_interaction.response.send_message("Closing ticket in 3 seconds...", ephemeral=True)
                        
                        # Log ticket closure
                        staff_channel = button_interaction.guild.get_channel(STAFF_LOG_CHANNEL_ID)
                        if staff_channel:
                            log_embed = Embed(
                                title="Ticket Closed",
                                description=f"Ticket {current_channel.mention} was closed by {current_user.mention}",
                                color=0xED4245,
                                timestamp=datetime.datetime.utcnow()
                            )
                            if claimed_by:
                                log_embed.add_field(name="Claimed by", value=claimed_by.mention, inline=True)
                            log_embed.add_field(name="Original User", value=f"<@{user.id}>", inline=True)
                            await staff_channel.send(embed=log_embed)
                        
                        await asyncio.sleep(3)
                        await current_channel.delete()
                    
                    @discord.ui.button(label="❌ Cancel", style=ButtonStyle.secondary)
                    async def cancel_close(self, button_interaction: Interaction, button: Button):
                        await button_interaction.response.send_message("Ticket closure cancelled.", ephemeral=True)
                
                confirm_view = ConfirmCloseView()
                await interaction.response.send_message("Are you sure you want to close this ticket?", view=confirm_view, ephemeral=True)
        
        control_view = TicketControlView()
        support_role = guild.get_role(SUPPORT_ROLE_ID)
        ping_message = f"<@&{SUPPORT_ROLE_ID}>" if support_role else "@here"
        
        await ticket_channel.send(f"{ping_message}", embed=embed, view=control_view)
        
        # Log ticket creation
        staff_channel = guild.get_channel(STAFF_LOG_CHANNEL_ID)
        if staff_channel:
            log_embed = Embed(
                title="New Ticket Created (Staff)",
                description=f"{interaction.user.mention} created a ticket for {user.mention}: {ticket_channel.mention}",
                color=0x5865F2,
                timestamp=datetime.datetime.utcnow()
            )
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await staff_channel.send(embed=log_embed)
        
        await interaction.response.send_message(f"Ticket created for {user.mention}: {ticket_channel.mention}", ephemeral=True)
        
    except Exception as e:
        await interaction.response.send_message(f"Failed to create ticket: {e}", ephemeral=True)

# --- Reaction Role System ---

import json

# Dictionary to store reaction role data: {message_id: {emoji: role_id}}
reaction_roles = {}

# Load reaction roles from file on startup
def load_reaction_roles():
    global reaction_roles
    try:
        with open('reaction_roles.json', 'r') as f:
            data = json.load(f)
            # Convert string keys back to integers for message IDs
            reaction_roles = {int(k): v for k, v in data.items()}
        print(f"Loaded {len(reaction_roles)} reaction role setups")
    except FileNotFoundError:
        print("No reaction roles file found, starting fresh")
        reaction_roles = {}
    except Exception as e:
        print(f"Error loading reaction roles: {e}")
        reaction_roles = {}

# Save reaction roles to file
def save_reaction_roles():
    try:
        with open('reaction_roles.json', 'w') as f:
            # Convert integer keys to strings for JSON serialization
            data = {str(k): v for k, v in reaction_roles.items()}
            json.dump(data, f, indent=2)
        print("Reaction roles saved")
    except Exception as e:
        print(f"Error saving reaction roles: {e}")

@tree.command(name="autorole", description="Manage reaction roles")
@app_commands.describe(
    action="Add or remove a reaction role",
    role="The role to assign/remove",
    emoji="The emoji to react with",
    message_id="The message ID to add reactions to"
)
@app_commands.check(is_staff)
async def autorole_command(interaction: discord.Interaction, action: str, message_id: str, role: discord.Role = None, emoji: str = None):
    if action.lower() not in ["add", "remove"]:
        await interaction.response.send_message("Action must be either 'add' or 'remove'!", ephemeral=True)
        return
    
    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("Invalid message ID!", ephemeral=True)
        return
    
    if action.lower() == "add":
        if not role or not emoji:
            await interaction.response.send_message("Role and emoji are required when adding a reaction role!", ephemeral=True)
            return
        
        # Find the message in any channel the bot can see
        message = None
        for channel in interaction.guild.text_channels:
            try:
                message = await channel.fetch_message(msg_id)
                break
            except:
                continue
        
        if not message:
            await interaction.response.send_message("Message not found!", ephemeral=True)
            return
        
        # Add to reaction roles dictionary
        if msg_id not in reaction_roles:
            reaction_roles[msg_id] = {}
        
        reaction_roles[msg_id][emoji] = role.id
        save_reaction_roles()  # Save to file
        
        # Add the reaction to the message
        try:
            await message.add_reaction(emoji)
        except:
            await interaction.response.send_message("Failed to add reaction. Make sure the emoji is valid!", ephemeral=True)
            return
        
        print(f"Reaction role added: Message {msg_id}, Emoji {emoji}, Role {role.name} (ID: {role.id})")
        await interaction.response.send_message(f"Reaction role added! Users can now react with {emoji} to get the {role.name} role.", ephemeral=True)
    
    elif action.lower() == "remove":
        if msg_id not in reaction_roles:
            await interaction.response.send_message("No reaction roles found for this message!", ephemeral=True)
            return
        
        # Find the message to remove reactions
        message = None
        for channel in interaction.guild.text_channels:
            try:
                message = await channel.fetch_message(msg_id)
                break
            except:
                continue
        
        if message:
            # Remove all bot reactions from the message
            for reaction in message.reactions:
                if reaction.me:
                    try:
                        await message.remove_reaction(reaction.emoji, interaction.guild.me)
                    except:
                        pass
        
        # Remove from dictionary
        del reaction_roles[msg_id]
        save_reaction_roles()  # Save to file
        
        await interaction.response.send_message("All reaction roles removed from this message!", ephemeral=True)

# Reaction events for role assignment
@client.event
async def on_reaction_add(reaction, user):
    if user.bot:
        print(f"Ignoring bot reaction from {user.name}")
        return
    
    message_id = reaction.message.id
    emoji_str = str(reaction.emoji)
    
    print(f"Reaction added: User {user.name}, Message ID {message_id}, Emoji {emoji_str}")
    print(f"Available reaction roles: {reaction_roles}")
    
    if message_id not in reaction_roles:
        print(f"No reaction roles configured for message {message_id}")
        return
    
    if emoji_str not in reaction_roles[message_id]:
        print(f"Emoji {emoji_str} not configured for message {message_id}. Available: {list(reaction_roles[message_id].keys())}")
        return
    
    role_id = reaction_roles[message_id][emoji_str]
    role = reaction.message.guild.get_role(role_id)
    
    if not role:
        print(f"Role with ID {role_id} not found!")
        return
    
    try:
        await user.add_roles(role)
        print(f"✅ Added role {role.name} to {user.name}")
    except Exception as e:
        print(f"❌ Failed to add role {role.name} to {user.name}: {e}")

@client.event
async def on_reaction_remove(reaction, user):
    if user.bot:
        return
    
    message_id = reaction.message.id
    emoji_str = str(reaction.emoji)
    
    print(f"Reaction removed: User {user.name}, Message ID {message_id}, Emoji {emoji_str}")
    
    if message_id not in reaction_roles:
        return
    
    if emoji_str not in reaction_roles[message_id]:
        return
    
    role_id = reaction_roles[message_id][emoji_str]
    role = reaction.message.guild.get_role(role_id)
    
    if not role:
        print(f"Role with ID {role_id} not found!")
        return
    
    try:
        await user.remove_roles(role)
        print(f"✅ Removed role {role.name} from {user.name}")
    except Exception as e:
        print(f"❌ Failed to remove role {role.name} from {user.name}: {e}")

# --- Run bot ---
client.run(TOKEN)