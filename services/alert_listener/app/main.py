"""Alert Listener Service - receives syslog and SNMP traps.

Runs UDP listeners for syslog (port 5514) and SNMP traps (port 1162),
normalizes them, and forwards to the NetAgent API for triage.
"""

import asyncio
import logging
import os
import signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Start all listeners."""
    logger.info("Starting NetAgent Alert Listener Service")

    transports = []
    tasks = []

    # Start syslog listener
    try:
        from syslog_listener import start_syslog_listener
        syslog_transport, consumer_task = await start_syslog_listener()
        transports.append(syslog_transport)
        tasks.append(consumer_task)
        logger.info("Syslog listener started")
    except Exception as e:
        logger.error(f"Failed to start syslog listener: {e}")

    # Start SNMP trap listener
    try:
        from snmp_listener import start_snmp_listener
        snmp_transport = await start_snmp_listener()
        transports.append(snmp_transport)
        logger.info("SNMP trap listener started")
    except Exception as e:
        logger.error(f"Failed to start SNMP listener: {e}")

    if not transports:
        logger.error("No listeners started, exiting")
        return

    logger.info("All listeners running. Waiting for alerts...")

    # Wait forever (or until signal)
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    await stop_event.wait()

    # Cleanup
    logger.info("Shutting down listeners...")
    for transport in transports:
        transport.close()
    for task in tasks:
        task.cancel()

    logger.info("Alert Listener Service stopped")


if __name__ == "__main__":
    asyncio.run(main())
