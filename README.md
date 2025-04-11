# CouchDB Async Purge Tool

A Python tool to asynchronously purge deleted documents from a CouchDB database. This tool efficiently processes large numbers of deleted documents by using async HTTP requests with retry mechanisms.

## Features

- Asynchronous document purging using aiohttp
- Automatic retry mechanism with exponential backoff
- Detailed logging with structlog
- Progress tracking and timing information
- Configurable batch processing
- Environment-based configuration

## Prerequisites

- Python 3.x
- CouchDB instance
- Virtual environment (optional, but recommended)

## Installation

1. Clone the repository
2. Create and activate a virtual environment (optional):
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Unix/macOS
   # or
   .venv\Scripts\activate  # On Windows
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Create a `.env` file in the project root with the following variables:

```env
COUCHDB_URL=http://localhost:18004
COUCHDB_DB_NAME=your_database_name
COUCHDB_USER=your_username
COUCHDB_PASSWORD=your_password
```

## Usage

### Running the Script

You can run the script in two ways:

1. Using the provided shell script (Unix/macOS):
   ```bash
   chmod +x run
   ./run
   ```

2. Directly with Python:
   ```bash
   python main.py
   ```

### How It Works

1. The script connects to your CouchDB instance using the provided credentials
2. It reads the last processed sequence number from `last_seq.txt` (if exists)
3. Fetches changes from the database, focusing on deleted documents
4. Processes documents in batches of 10
5. Sends purge requests asynchronously with retry logic
6. Updates the last sequence number for future runs

### Logging

The script provides detailed logging including:
- Processing time for each batch
- Number of documents purged
- Retry attempts and timing
- Error information if any occurs

## Error Handling

The script includes comprehensive error handling:
- Automatic retries with exponential backoff
- Detailed error logging
- Graceful handling of keyboard interrupts

## Configuration Options

You can modify the following constants in `main.py` to adjust the behavior:

- `RETRY_ATTEMPTS`: Number of retry attempts (default: 4)
- `RETRY_START_TIMEOUT`: Initial retry timeout in seconds (default: 1)
- `RETRY_FACTOR`: Exponential factor for retry timeouts (default: 2)
- `ASYNC_REQUEST_BATCH`: Number of concurrent purge requests (default: 10)
