# PostgreSQL Database Backup System

This document describes how the automated backup system for the PostgreSQL database works and how to configure it.

## Overview

The backup system uses [docker-volume-backup](https://github.com/offen/docker-volume-backup) to:

1. Create regular backups of the PostgreSQL database volume
2. Upload backups to Cloudflare R2 storage (S3-compatible)
3. Maintain a local backup copy
4. Automatically prune old backups based on retention policy

## Configuration

### 1. Set up Cloudflare R2

Before using the backup system, you need to set up Cloudflare R2:

1. Create a Cloudflare R2 account if you don't have one
2. Create a new R2 bucket for your backups
3. Generate API keys (Access Key ID and Secret Access Key) with appropriate permissions

### 2. Configure `backup.env`

Edit the `backup.env` file and update the following variables:

```
# Replace with your actual Cloudflare R2 credentials
AWS_ACCESS_KEY_ID=your_cloudflare_r2_access_key_id
AWS_SECRET_ACCESS_KEY=your_cloudflare_r2_secret_access_key
AWS_ENDPOINT=https://your_account_id.r2.cloudflarestorage.com
AWS_S3_BUCKET_NAME=your_backup_bucket_name
```

### 3. Backup Schedule and Retention

By default, backups run daily at 4:00 AM and are kept for 30 days. You can modify these settings in the `backup.env` file:

```
# Run at 4 AM daily (cron syntax)
BACKUP_CRON_EXPRESSION=0 4 * * *

# Keep backups for 30 days
BACKUP_RETENTION_DAYS=30
```

## Restore from Backup

To restore from a backup:

1. Stop the running services:
   ```
   docker-compose down
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
   docker-compose up -d
   ```

## Additional Options

The backup system supports additional features that are commented out in the `backup.env` file:

- Compression level adjustment
- Backup encryption
- Custom backup paths

Uncomment and configure these options as needed.

## Testing Backups

To manually trigger a backup:

```
docker-compose exec backup /bin/backup
```

To check backup logs:

```
docker-compose logs backup
``` 