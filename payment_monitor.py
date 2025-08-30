import logging
import asyncio
from datetime import datetime
from database import get_payment, update_payment_status

class PaymentMonitor:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    async def monitor_payments(self):
        while True:
            try:
                self.logger.info("Checking pending payments...")
                await asyncio.sleep(300)
            except Exception as e:
                self.logger.error(f"Payment monitoring error: {e}")
                await asyncio.sleep(60)

payment_monitor = PaymentMonitor()