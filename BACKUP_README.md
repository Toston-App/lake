# PostgreSQL Database Backup System

The backup system uses [docker-volume-backup](https://github.com/offen/docker-volume-backup) to:

1. Create regular backups of the PostgreSQL database volume
2. Upload backups to Cloudflare R2 storage (S3-compatible)
3. Maintain a local backup copy
4. Automatically prune old backups based on retention policy
5. Send notifications to Telegram about backup status

## Configuration

### 1. Set up Cloudflare R2

Before using the backup system, you need to set up Cloudflare R2:

1. Create a Cloudflare R2 account if you don't have one
2. Create a new R2 bucket for your backups
3. Generate API keys (Access Key ID and Secret Access Key) with appropriate permissions

### 2. Configure Your .env File

Add the following variables to your existing `.env` file:

```
# Backup Settings
# Cron expression on UTC time zone
BACKUP_CRON_EXPRESSION=0 4 * * *
BACKUP_FILENAME=postgres-backup-%Y-%m-%dT%H-%M-%S.tar.gz
BACKUP_RETENTION_DAYS=30
BACKUP_PRUNING_PREFIX=postgres-backup-
BACKUP_STOP_DURING_BACKUP_LABEL=docker-volume-backup.stop-during-backup

# Cloudflare R2 Configuration (S3 compatible)
AWS_ACCESS_KEY_ID=your_cloudflare_r2_access_key_id
AWS_SECRET_ACCESS_KEY=your_cloudflare_r2_secret_access_key
AWS_ENDPOINT=your_account_id.r2.cloudflarestorage.com
AWS_S3_BUCKET_NAME=your_backup_bucket_name
AWS_DEFAULT_REGION=auto
AWS_S3_PATH=postgres-backups/

# Telegram Notifications
TELEGRAM_NOTIFICATION_URL=telegram://bot_token@telegram/?chats=chat_id
# info for all events, even successful backups
BACKUP_NOTIFICATION_LEVEL=error  # Options: info, error

# Local backup path. Uncomment docker-compose.yml to use local backup
BACKUP_ARCHIVE_PATH=./backups

# Optional: set compression level
# BACKUP_COMPRESSION=gzip
# BACKUP_COMPRESSION_LEVEL=9

# Optional: set encryption if needed
# BACKUP_ENCRYPTION_KEY=your_encryption_key
```

Make sure your `.env` file is properly excluded from your Git repository (add it to `.gitignore` if not already there).

### 3. Backup Schedule and Retention

By default, backups run daily at 4:00 AM and are kept for 30 days. You can modify these settings in the `.env` file:

```
# Run at 4 AM daily (cron syntax)
BACKUP_CRON_EXPRESSION=0 4 * * *

# Keep backups for 30 days
BACKUP_RETENTION_DAYS=30
```

### 4. Telegram Notifications Setup

To receive notifications about your backups via Telegram:

1. Create a Telegram bot using [@BotFather](https://t.me/botfather) and get your bot token
2. Get your chat ID (you can use [@userinfobot](https://t.me/userinfobot) or other methods)
3. Configure the Telegram notification URL in your `.env` file:

```
TELEGRAM_NOTIFICATION_URL=telegram://bot_token@telegram/?chat_id=chat_id
```

Replace `bot_token` with your actual bot token and `chat_id` with your chat ID.

You can also configure the notification level:
```
BACKUP_NOTIFICATION_LEVEL=all  # Get notifications for all events
```

Available notification levels:
- `all`: Receive notifications for all events (success, warnings, errors)
- `warning`: Only receive notifications for warnings and errors
- `error`: Only receive notifications for errors

## Restore from Backup

To restore from a backup:

1. Stop the running services:
   ```
   docker compose down
   ```

2. Download the desired backup file from Cloudflare R2

3. Extract the backup:
   ```
   tar -xzvf postgres-backup-YYYY-MM-DDTHH-MM-SS.tar.gz
   ```

4. Replace the database volume with the extracted backup
   ```
   # This will vary depending on your specific setup
   cp -r ./extracted_backup_path/* /path/to/your/postgres_data/
   ```

5. Start the services again:
   ```
   docker compose up -d
   ```

## Additional Options

The backup system supports additional features that can be configured in your `.env` file:

- Compression level adjustment
- Backup encryption
- Custom backup paths
- Notifications (Telegram, Discord, Slack, etc.)

## Testing Backups

To manually trigger a backup:

```
docker compose exec backup backup
```

To check backup logs:

```
docker compose logs backup
```