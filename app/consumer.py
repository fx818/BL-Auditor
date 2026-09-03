"""RabbitMQ consumer for BL_AUDITOR queue.

Reads messages of shape {"args": {"ofr_id": "<id>", "typ": 0}} and POSTs each
offer_id to the FastAPI /audit endpoint (UI service). Retries transient
failures up to MAX_RETRIES with exponential backoff, then nacks to DLX.

Run: `python -m app.consumer`
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

import aio_pika
import httpx

from app.redash_filter import is_offer_eligible

logging.basicConfig(
    
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bl-auditor.consumer")

RABBITMQ_URL = os.getenv(
    "RABBITMQ_URL", "amqp://admin:admin@35.200.203.4:5672/astbuy"
)
QUEUE_NAME = os.getenv("RABBITMQ_QUEUE", "BL_AUDITOR")
PREFETCH = int(os.getenv("RABBITMQ_PREFETCH", "4"))
AUDIT_API_URL = os.getenv("AUDIT_API_URL", "http://localhost:8080/windmill-batch/trigger-one")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
HTTP_TIMEOUT = float(os.getenv("AUDIT_HTTP_TIMEOUT", "180"))
STARTUP_DELAY = float(os.getenv("CONSUMER_STARTUP_DELAY", "15"))  # seconds to wait for FastAPI to be ready
MAX_OFFERS = int(os.getenv("MAX_OFFERS", "1000"))  # audits per calendar day
SAMPLE_EVERY = int(os.getenv("AUDIT_SAMPLE_EVERY", "10"))
DAY_TZ = ZoneInfo(os.getenv("AUDIT_DAY_TZ", "Asia/Kolkata"))  # day boundary tz
AUDIT_HOUR_START = int(os.getenv("AUDIT_HOUR_START", "9"))   # inclusive, IST
AUDIT_HOUR_END   = int(os.getenv("AUDIT_HOUR_END",   "22"))  # exclusive, IST
REDASH_API_KEY   = os.getenv("REDASH_KEY", "")

# Daily counters persist here so the per-day cap survives restarts/deploys.
# Co-located with the audit CSVs (same DATA_DIR), so it lives on the same
# persistent volume. NOTE: single-consumer only — multiple consumers sharing
# this file would race; that case needs shared state (Redis/DB) instead.
_DATA_DIR = os.environ.get("DATA_DIR") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE_PATH = os.path.join(_DATA_DIR, "consumer_state.json")
LOCK_PATH  = os.path.join(_DATA_DIR, "consumer_state.lock")


class BadPayload(Exception):
    """Permanent failure — message goes straight to DLX."""


def parse_offer_id(body: bytes) -> str:
    try:
        msg = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BadPayload(f"not valid utf-8 json: {e}")
    args = msg.get("args") if isinstance(msg, dict) else None
    if not isinstance(args, dict):
        raise BadPayload("missing 'args' object")
    ofr_id = args.get("ofr_id")
    if ofr_id is None:
        raise BadPayload("missing 'args.ofr_id'")
    ofr_id = str(ofr_id).strip()
    if not ofr_id.isdigit():
        raise BadPayload(f"ofr_id not numeric: {ofr_id!r}")
    return ofr_id


def today_str() -> str:
    return datetime.now(DAY_TZ).date().isoformat()


def load_daily_state() -> tuple[int, int]:
    """Restore (audited, seen) if the saved state is from the current day, else
    (0, 0). Lets the daily MAX_OFFERS cap survive consumer restarts/deploys."""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        return 0, 0
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not read state %s: %s; starting fresh", STATE_PATH, e)
        return 0, 0
    if not isinstance(st, dict) or st.get("day") != today_str():
        return 0, 0
    return int(st.get("audited", 0)), int(st.get("seen", 0))


def save_daily_state(day: str, audited: int, seen: int) -> None:
    """Atomically persist the daily counters. Fail-soft: a write error logs but
    does not crash the worker (degrades to in-memory counting for that day)."""
    tmp = f"{STATE_PATH}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"day": day, "audited": audited, "seen": seen}, f)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        log.error("could not persist state %s: %s", STATE_PATH, e)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def acquire_lock(timeout: float = 5.0) -> None:
    """Acquire an NFS-safe lock using O_EXCL (atomic across nodes).
    Removes stale lock after timeout and retries once."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return
        except FileExistsError:
            if time.monotonic() > deadline:
                try:
                    os.unlink(LOCK_PATH)
                except OSError:
                    pass
            time.sleep(0.01)


def release_lock() -> None:
    try:
        os.unlink(LOCK_PATH)
    except OSError:
        pass


async def call_audit(client: httpx.AsyncClient, offer_id: str) -> None:
    resp = await client.post(
        AUDIT_API_URL,
        json={"offer_id": offer_id},
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code >= 500:
        raise RuntimeError(f"audit api {resp.status_code}: {resp.text[:200]}")
    if resp.status_code == 400:
        raise BadPayload(f"audit api rejected offer_id: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise RuntimeError(f"audit api {resp.status_code}: {resp.text[:200]}")


async def audit_offer(message: aio_pika.IncomingMessage, client: httpx.AsyncClient, offer_id: str) -> None:
    """Audit one already-parsed, valid offer.

    Acks on success; nacks to DLX on audit-api rejection or exhausted retries.
    """
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("audit start offer_id=%s attempt=%d/%d", offer_id, attempt, MAX_RETRIES)
            await call_audit(client, offer_id)
            log.info("audit ok offer_id=%s", offer_id)
            await message.ack()
            return
        except BadPayload as e:
            log.error("audit rejected offer_id=%s: %s", offer_id, e)
            await message.nack(requeue=False)
            return
        except Exception as e:  # transient
            last_exc = e
            log.warning(
                "audit fail offer_id=%s attempt=%d: %s: %s",
                offer_id, attempt, type(e).__name__, e,
                exc_info=True,
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))

    log.error(
        "audit exhausted retries offer_id=%s last=%s: %s -> DLX",
        offer_id, type(last_exc).__name__, last_exc,
    )
    await message.nack(requeue=False)


async def run() -> None:
    log.info("connecting url=%s queue=%s prefetch=%d", RABBITMQ_URL.split("@")[-1], QUEUE_NAME, PREFETCH)
    connection = None
    backoff = 5
    while connection is None:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL, timeout=15)
        except Exception as e:
            log.warning("initial connect failed: %s: %s; retrying in %ds", type(e).__name__, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=PREFETCH)
        queue = await channel.get_queue(QUEUE_NAME, ensure=False)

        async with httpx.AsyncClient() as client:
            stop = asyncio.Event()

            def _signal_stop(*_):
                log.info("shutdown signal received")
                stop.set()

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, _signal_stop)
                except NotImplementedError:
                    signal.signal(sig, _signal_stop)

            # Wait for FastAPI to finish starting before consuming — avoids ConnectError
            # on messages queued up before this pod was ready.
            if STARTUP_DELAY > 0:
                log.info("startup delay %.0fs (waiting for FastAPI to be ready)...", STARTUP_DELAY)
                await asyncio.sleep(STARTUP_DELAY)

            log.info(
                "consuming... daily audit limit=%d, sampling 1 in %d, day-tz=%s (others acked without auditing)",
                MAX_OFFERS, SAMPLE_EVERY, DAY_TZ.key,
            )
            async with queue.iterator() as it:
                while not stop.is_set():
                    # Wait for the next delivery, but stay responsive to shutdown:
                    # race the iterator against the stop event so an idle consumer
                    # exits promptly on SIGTERM instead of blocking inside __anext__
                    # until a message happens to arrive (and getting SIGKILLed at
                    # the grace deadline). An in-flight audit still finishes first —
                    # the stop check only gates fetching the *next* message.
                    anext_task = asyncio.ensure_future(it.__anext__())
                    stop_task = asyncio.ensure_future(stop.wait())
                    done, _ = await asyncio.wait(
                        {anext_task, stop_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if stop_task in done:
                        # Shutdown requested while waiting. Drop the pending fetch;
                        # a message delivered in the same tick stays unacked and is
                        # requeued by the broker (no count moved -> no lost budget).
                        anext_task.cancel()
                        break
                    stop_task.cancel()
                    try:
                        message = anext_task.result()
                    except StopAsyncIteration:
                        break
                    async with message.process(ignore_processed=True, requeue=False):
                        # Bad payloads -> DLX. They don't consume a sampling
                        # slot or count toward the audit budget.
                        try:
                            offer_id = parse_offer_id(message.body)
                        except BadPayload as e:
                            log.error("bad payload, dropping to DLX: %s body=%r", e, message.body[:200])
                            await message.nack(requeue=False)
                            continue

                        # Off-hours: consume but never audit. Ack = discarded.
                        current_hour = datetime.now(DAY_TZ).hour
                        if not (AUDIT_HOUR_START <= current_hour < AUDIT_HOUR_END):
                            await message.ack()
                            log.info("off-hours (hour=%d IST); offer_id=%s consumed without audit", current_hour, offer_id)
                            continue

                        # Eligibility filter: only high-sold, non-VAANI offers.
                        # Runs before sampling/budget so ineligible offers don't
                        # consume a slot. DB error → discard (fail closed).
                        try:
                            eligible = await asyncio.to_thread(is_offer_eligible, REDASH_API_KEY, offer_id)
                        except Exception as e:
                            log.warning("redash filter error offer_id=%s: %s: %s; discarding", offer_id, type(e).__name__, e)
                            await message.ack()
                            continue
                        if not eligible:
                            log.info("offer_id=%s filtered out (not high-sold or vaani); consumed without audit", offer_id)
                            await message.ack()
                            continue

                        # Acquire NFS lock and read fresh state — safe for multiple consumers.
                        acquire_lock()
                        try:
                            today = today_str()
                            audited, seen = load_daily_state()

                            # Budget spent: consume but never audit. Ack = discarded.
                            if audited >= MAX_OFFERS:
                                release_lock()
                                await message.ack()
                                log.info("audit limit %d reached; offer_id=%s consumed without audit", MAX_OFFERS, offer_id)
                                continue

                            # Sample the 1st of every SAMPLE_EVERY valid offers;
                            # ack & discard the rest.
                            seen += 1
                            if (seen - 1) % SAMPLE_EVERY != 0:
                                save_daily_state(today, audited, seen)
                                release_lock()
                                await message.ack()
                                log.info("sampled out offer_id=%s (pos %d in group of %d); consumed without audit", offer_id, ((seen - 1) % SAMPLE_EVERY) + 1, SAMPLE_EVERY)
                                continue

                            # Will audit — persist incremented seen before releasing lock.
                            save_daily_state(today, audited, seen)
                        finally:
                            release_lock()

                        # Audit runs outside the lock (can be slow).
                        await audit_offer(message, client, offer_id)

                        # Re-acquire lock to increment audited count.
                        acquire_lock()
                        try:
                            today = today_str()
                            audited, seen = load_daily_state()
                            audited += 1
                            save_daily_state(today, audited, seen)
                        finally:
                            release_lock()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("consumer crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()
