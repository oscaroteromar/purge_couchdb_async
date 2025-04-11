# https://martinxpn.medium.com/making-requests-with-asyncio-in-python-78-100-days-of-python-eb1570b3f986

import asyncio
import os
import timeit
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Generator

import aiohttp
import requests
from aiohttp import ClientSession, TraceConfig, TraceRequestStartParams
from aiohttp_retry import ExponentialRetry, RetryClient
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
from structlog import get_logger
from structlog.contextvars import bound_contextvars

load_dotenv()

logger = get_logger(__name__)

# Configuración
COUCHDB_URL = os.getenv("COUCHDB_URL")
DB_NAME = os.getenv("COUCHDB_DB_NAME")
USER = os.getenv("COUCHDB_USER")
PASSWORD = os.getenv("COUCHDB_PASSWORD")

# Retry options
RETRY_ATTEMPTS = 4
RETRY_START_TIMEOUT = 1
RETRY_FACTOR = 2


def human_time(secs):
    if secs < 60:
        return f"{secs:.3f}s"
    mins, secs = int(secs) // 60, secs % 60
    if mins < 60:
        return f"{mins}m:{secs:06.3f}s"
    hrs, mins = mins // 60, mins % 60
    if hrs < 24:
        return f"{hrs}h:{mins:02}m:{secs:06.3f}s"
    days, hrs = hrs // 24, hrs % 24
    return f"{days}d:{hrs:02}h:{mins:02}m:{secs:06.3f}s"


@contextmanager
def time_it(log_prefix: str = "Time taken", log_id: str | None = None):
    start = timeit.default_timer()
    yield
    end = timeit.default_timer()
    logger.debug(f"{log_prefix}: {human_time(end - start)}", log_id=log_id)


def async_error_catcher(f):
    """Decorador para capturar errores y mostrarlos en el logger"""

    async def wrapper(*args, **kwargs):
        try:
            return await f(*args, **kwargs)
        except Exception as e:
            await logger.aerror(f"Error in {f.__name__}", error=e, log_id=LogId().value)

    return wrapper


def sync_error_catcher(f):
    """Decorador para capturar errores y mostrarlos en el logger"""

    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}", error=e, log_id=LogId().value)

    return wrapper


class LogId:
    """Singleton to generate unique log ids."""

    def __new__(cls):
        # return cls()
        if not hasattr(cls, "instance"):
            cls.instance = super().__new__(cls)
        return cls.instance

    @property
    def value(self):
        return self._value

    def reset(self):
        self._value = str(uuid.uuid4())


class Changes:

    def __init__(self):
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(USER, PASSWORD)
        self._last_seq = None

    @staticmethod
    def params(last_seq=None):
        params = {"style": "all_docs", "include_docs": "true", "limit": "100"}
        if last_seq:
            params["since"] = last_seq
        return params

    @property
    def url(self):
        return f"{COUCHDB_URL}/{DB_NAME}/_changes"

    @property
    def last_seq(self):
        if self._last_seq is None:
            try:
                with open("last_seq.txt", "r") as f:
                    self._last_seq = f.read()
            except FileNotFoundError:
                pass
        return self._last_seq

    @last_seq.setter
    def last_seq(self, last_seq: str | None):
        if last_seq is None:
            return
        self._last_seq = last_seq
        with open("last_seq.txt", "w+") as f:
            f.write(last_seq)

    @sync_error_catcher
    def get(self):
        logger.info(f"Getting changes from {self.url}", params=self.params(self.last_seq), log_id=LogId().value)
        response = self.session.get(self.url, params=self.params(self.last_seq))
        response.raise_for_status()
        content = response.json()
        return content

    @sync_error_catcher
    def iter_deleted_docs(self) -> Generator[dict[str, list[str]], None, None]:
        pages = 1
        keep_going = True
        while keep_going:
            LogId().reset()
            logger.info(f"Page number: {pages}", log_id=LogId().value)
            pages += 1
            content = self.get()
            if not (changes := content.get("results", [])):
                logger.info("No changes found, exiting", log_id=LogId().value)
                break

            # Check the new last seq is different from the current one.
            # Otherwise, this will be the last iteration.
            new_last_seq = content.get("last_seq")
            if new_last_seq == self.last_seq:
                keep_going = False
            self.last_seq = new_last_seq

            changes = filter(
                lambda change: "doc" in change and change["doc"] is not None and "_deleted" in change["doc"], changes
            )
            deleted_docs: dict[str, list[str]]
            if not (deleted_docs := dict(map(lambda change: (change["id"], [change["changes"][0]["rev"]]), changes))):
                logger.info("No deleted docs found in this batch, continue", log_id=LogId().value)
                continue
            yield deleted_docs


class Purge:

    # Async requests will be sent when the batch has a 10 size.
    ASYNC_REQUEST_BATCH = 10

    def __init__(self):
        self.changes = Changes()

    @property
    def url(self):
        return f"{COUCHDB_URL}/{DB_NAME}/_purge"

    @staticmethod
    async def on_request_start(
        session: ClientSession,
        trace_config_ctx: SimpleNamespace,
        params: TraceRequestStartParams,
    ) -> None:
        current_attempt = trace_config_ctx.trace_request_ctx["current_attempt"]
        if current_attempt == RETRY_ATTEMPTS:
            return
        log_id = trace_config_ctx.trace_request_ctx["log_id"]
        retry_period = RETRY_START_TIMEOUT * (RETRY_FACTOR**current_attempt)
        await logger.adebug(f"Attempt {current_attempt}, retrying in {retry_period} seconds", log_id=log_id)

    @staticmethod
    async def request(session, url, data, log_id, total_purged_docs):
        with bound_contextvars(url=url, log_id=log_id):
            await logger.adebug(f"Attempt to purge {len(data)} docs", total_purged_docs=total_purged_docs)
            with time_it(log_prefix="Single purge"):
                try:
                    async with RetryClient(
                        client_session=session,
                        retry_options=ExponentialRetry(
                            attempts=RETRY_ATTEMPTS, start_timeout=RETRY_START_TIMEOUT, factor=RETRY_FACTOR
                        ),
                    ).post(url, json=data, raise_for_status=True, trace_request_ctx={"log_id": log_id}):
                        await asyncio.sleep(0.3)
                        # content = await response.json()
                        await logger.ainfo(f"Purged {len(data)} docs")
                except Exception as e:
                    await logger.aerror(f"Error when purging docs: {e}")

    @async_error_catcher
    async def purge_async(self):
        total_purged_docs = 0
        tasks = []
        trace_config = TraceConfig()
        trace_config.on_request_start.append(Purge.on_request_start)
        auth = aiohttp.BasicAuth(login=USER, password=PASSWORD)
        async with aiohttp.ClientSession(auth=auth, trace_configs=[trace_config]) as session:
            for deleted_docs in self.changes.iter_deleted_docs():
                total_purged_docs += len(deleted_docs)
                task = asyncio.create_task(
                    coro=Purge.request(
                        session=session,
                        url=self.url,
                        data=deleted_docs,
                        total_purged_docs=total_purged_docs,
                        log_id=LogId().value,
                    )
                )
                tasks.append(task)
                # Tasks are ran with asyncio.gather
                # By setting `return_exceptions` to False, we will raise Exceptions within
                #   their asyncio task instance and everything will stop, by putting True, it
                #   will raise when `result()` is called on the future.
                if len(tasks) >= self.ASYNC_REQUEST_BATCH:
                    await asyncio.gather(*tasks, return_exceptions=False)
                    tasks = []

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=False)


if __name__ == "__main__":

    purger = Purge()
    try:
        with time_it(log_prefix="Global purge"):
            asyncio.run(purger.purge_async())
    except KeyboardInterrupt:
        logger.info("Leaving...", log_id=LogId().value)
