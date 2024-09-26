from typing_extensions import TypedDict

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pprint import pformat
from typing import (
    Any,
    Iterable,
    Iterator,
    Literal,
    Mapping,
    Optional,
    Sequence,
    
    Union,
)
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig
from langchain_core.utils.input import get_bolded_text, get_colored_text

from langgraph.channels.base import BaseChannel
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata, PendingWrite
from langgraph.constants import ERROR, INTERRUPT, TAG_HIDDEN
from langgraph.pregel.io import read_channels
from langgraph.types import PregelExecutableTask, PregelTask, StateSnapshot


class TaskPayload(TypedDict):
    id: str
    name: str
    input: Any
    triggers: list[str]


class TaskResultPayload(TypedDict):
    id: str
    name: str
    error: Optional[str]
    interrupts: list[dict]
    result: list[tuple[str, Any]]


class CheckpointTask(TypedDict):
    id: str
    name: str
    error: Optional[str]
    interrupts: list[dict]


class CheckpointPayload(TypedDict):
    config: Optional[RunnableConfig]
    metadata: CheckpointMetadata
    values: dict[str, Any]
    next: list[str]
    tasks: list[CheckpointTask]


class DebugOutputBase(TypedDict):
    timestamp: str
    step: int


class DebugOutputTask(DebugOutputBase):
    type: Literal["task"]
    payload: TaskPayload


class DebugOutputTaskResult(DebugOutputBase):
    type: Literal["task_result"]
    payload: TaskResultPayload


class DebugOutputCheckpoint(DebugOutputBase):
    type: Literal["checkpoint"]
    payload: CheckpointPayload


DebugOutput = Union[DebugOutputTask, DebugOutputTaskResult, DebugOutputCheckpoint]


TASK_NAMESPACE = UUID("6ba7b831-9dad-11d1-80b4-00c04fd430c8")


def map_debug_tasks(
    step: int, tasks: Iterable[PregelExecutableTask]
) -> Iterator[DebugOutputTask]:
    """Produce "task" events for stream_mode=debug."""
    ts = datetime.now(timezone.utc).isoformat()
    for task in tasks:
        if task.config is not None and TAG_HIDDEN in task.config.get("tags", []):
            continue

        yield {
            "type": "task",
            "timestamp": ts,
            "step": step,
            "payload": {
                "id": task.id,
                "name": task.name,
                "input": task.input,
                "triggers": task.triggers,
            },
        }


def map_debug_task_results(
    step: int,
    task_tup: tuple[PregelExecutableTask, Sequence[tuple[str, Any]]],
    stream_keys: Union[str, Sequence[str]],
) -> Iterator[DebugOutputTaskResult]:
    """Produce "task_result" events for stream_mode=debug."""
    stream_channels_list = (
        [stream_keys] if isinstance(stream_keys, str) else stream_keys
    )
    task, writes = task_tup
    yield {
        "type": "task_result",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "step": step,
        "payload": {
            "id": task.id,
            "name": task.name,
            "error": next((w[1] for w in writes if w[0] == ERROR), None),
            "result": [w for w in writes if w[0] in stream_channels_list],
            "interrupts": [asdict(w[1]) for w in writes if w[0] == INTERRUPT],
        },
    }


def map_debug_checkpoint(
    step: int,
    config: RunnableConfig,
    channels: Mapping[str, BaseChannel],
    stream_channels: Union[str, Sequence[str]],
    metadata: CheckpointMetadata,
    checkpoint: Checkpoint,
    tasks: Iterable[PregelExecutableTask],
    pending_writes: list[PendingWrite],
) -> Iterator[DebugOutputCheckpoint]:
    """Produce "checkpoint" events for stream_mode=debug."""
    yield {
        "type": "checkpoint",
        "timestamp": checkpoint["ts"],
        "step": step,
        "payload": {
            "config": config,
            "values": read_channels(channels, stream_channels),
            "metadata": metadata,
            "next": [t.name for t in tasks],
            "tasks": [
                {
                    "id": t.id,
                    "name": t.name,
                    "error": t.error,
                }
                if t.error
                else {
                    "id": t.id,
                    "name": t.name,
                    "interrupts": tuple(asdict(i) for i in t.interrupts),
                }
                for t in tasks_w_writes(tasks, pending_writes, None)
            ],
        },
    }


def print_step_tasks(step: int, next_tasks: list[PregelExecutableTask]) -> None:
    n_tasks = len(next_tasks)
    print(
        f"{get_colored_text(f'[{step}:tasks]', color='blue')} "
        + get_bolded_text(
            f"Starting step {step} with {n_tasks} task{'s' if n_tasks != 1 else ''}:\n"
        )
        + "\n".join(
            f"- {get_colored_text(task.name, 'green')} -> {pformat(task.input)}"
            for task in next_tasks
        )
    )


def print_step_writes(
    step: int, writes: Sequence[tuple[str, Any]], whitelist: Sequence[str]
) -> None:
    by_channel: dict[str, list[Any]] = defaultdict(list)
    for channel, value in writes:
        if channel in whitelist:
            by_channel[channel].append(value)
    print(
        f"{get_colored_text(f'[{step}:writes]', color='blue')} "
        + get_bolded_text(
            f"Finished step {step} with writes to {len(by_channel)} channel{'s' if len(by_channel) != 1 else ''}:\n"
        )
        + "\n".join(
            f"- {get_colored_text(name, 'yellow')} -> {', '.join(pformat(v) for v in vals)}"
            for name, vals in by_channel.items()
        )
    )


def print_step_checkpoint(
    metadata: CheckpointMetadata,
    channels: Mapping[str, BaseChannel],
    whitelist: Sequence[str],
) -> None:
    step = metadata["step"]
    print(
        f"{get_colored_text(f'[{step}:checkpoint]', color='blue')} "
        + get_bolded_text(f"State at the end of step {step}:\n")
        + pformat(read_channels(channels, whitelist), depth=3)
    )


def tasks_w_writes(
    tasks: Iterable[Union[PregelTask, PregelExecutableTask]],
    pending_writes: Optional[list[PendingWrite]],
    states: Optional[dict[str, Union[RunnableConfig, StateSnapshot]]],
) -> tuple[PregelTask, ...]:
    """Apply writes / subgraph states to tasks to be returned in a StateSnapshot."""
    pending_writes = pending_writes or []
    return tuple(
        PregelTask(
            task.id,
            task.name,
            task.path,
            next(
                (
                    exc
                    for tid, n, exc in pending_writes
                    if tid == task.id and n == ERROR
                ),
                None,
            ),
            tuple(
                v for tid, n, v in pending_writes if tid == task.id and n == INTERRUPT
            ),
            states.get(task.id) if states else None,
        )
        for task in tasks
    )
