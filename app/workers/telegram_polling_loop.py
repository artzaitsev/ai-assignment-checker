from __future__ import annotations

import re

from app.domain.models import WorkItemClaim
from app.workers.loop import WorkerLoop


_PROCESSED_RE = re.compile(r"^processed\s+(\d+)\s+telegram events")


class TelegramPollingWorkerLoop(WorkerLoop):
    async def run_once(self) -> bool:
        # Telegram ingest is transport-facing and checkpoint-based: no DB claim
        # acquisition is needed for poll ticks.
        result = await self.process(WorkItemClaim(item_id="poll-tick", stage=self.stage, attempt=0))
        if not result.success:
            raise RuntimeError(result.detail)
        match = _PROCESSED_RE.match(result.detail)
        if match is None:
            return False
        return int(match.group(1)) > 0
