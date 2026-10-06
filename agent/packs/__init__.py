"""
Packs = jobs the agent knows how to do. To teach Shift a new job, add a pack here.
The core (loop.py, brain.py, policy.py, tools.py) never changes.
"""
from agent.packs import files, returns, store_ops

DEFAULT_PACKS = [files.PACK, store_ops.PACK, returns.PACK]
