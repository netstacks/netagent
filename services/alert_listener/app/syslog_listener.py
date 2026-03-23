"""Syslog listener - receives syslog messages via UDP/TCP and forwards to API."""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

API_URL = os.getenv("API_URL", "http://api:8001")
SYSLOG_PORT = int(os.getenv("SYSLOG_PORT", "5514"))


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    """UDP syslog receiver."""

    def __init__(self):
        self.transport = None
        self._queue = asyncio.Queue(maxsize=10000)

    def connection_made(self, transport):
        self.transport = transport
        logger.info(f"Syslog UDP listener ready on port {SYSLOG_PORT}")

    def datagram_received(self, data, addr):
        try:
            message = data.decode("utf-8", errors="replace").strip()
            source_ip = addr[0]

            # Parse priority and severity from syslog header
            facility = 0
            severity = 6
            if message.startswith("<"):
                end = message.index(">")
                pri = int(message[1:end])
                facility = pri >> 3
                severity = pri & 0x07
                message = message[end + 1:]

            try:
                self._queue.put_nowait({
                    "raw": message,
                    "facility": facility,
                    "severity": severity,
                    "source_ip": source_ip,
                })
            except asyncio.QueueFull:
                logger.warning("Syslog queue full, dropping message")

        except Exception as e:
            logger.error(f"Error processing syslog UDP message: {e}")


async def _forward_syslog_batch(messages: list):
    """Forward a batch of syslog messages to the API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        for msg in messages:
            try:
                response = await client.post(
                    f"{API_URL}/api/alerts/ingest/syslog",
                    json=msg,
                )
                if response.status_code != 200:
                    logger.warning(f"API returned {response.status_code} for syslog ingest")
            except Exception as e:
                logger.error(f"Failed to forward syslog to API: {e}")


async def _syslog_consumer(protocol: SyslogUDPProtocol):
    """Consumer that batches syslog messages and forwards to API."""
    batch = []
    batch_interval = 1.0  # seconds

    while True:
        try:
            # Drain queue with timeout
            try:
                msg = await asyncio.wait_for(protocol._queue.get(), timeout=batch_interval)
                batch.append(msg)

                # Drain more if available (up to batch size)
                while len(batch) < 50:
                    try:
                        msg = protocol._queue.get_nowait()
                        batch.append(msg)
                    except asyncio.QueueEmpty:
                        break

            except asyncio.TimeoutError:
                pass

            # Forward batch
            if batch:
                await _forward_syslog_batch(batch)
                logger.debug(f"Forwarded {len(batch)} syslog messages")
                batch = []

        except Exception as e:
            logger.error(f"Syslog consumer error: {e}")
            batch = []
            await asyncio.sleep(1)


async def start_syslog_listener():
    """Start the syslog UDP listener and consumer."""
    loop = asyncio.get_event_loop()

    transport, protocol = await loop.create_datagram_endpoint(
        SyslogUDPProtocol,
        local_addr=("0.0.0.0", SYSLOG_PORT),
    )

    logger.info(f"Syslog listener started on UDP port {SYSLOG_PORT}")

    # Start consumer task
    consumer_task = asyncio.create_task(_syslog_consumer(protocol))

    return transport, consumer_task
