from typing import Any, Literal, Optional, Union
from uuid import uuid4

from langchain_core.messages import AnyMessage
from typing_extensions import TypedDict

from langgraph.constants import CONF, CONFIG_KEY_SEND
from langgraph.utils.config import get_config, get_stream_writer


class UIMessage(TypedDict):
    """A message type for UI updates in LangGraph.

    This TypedDict represents a UI message that can be sent to update the UI state.
    It contains information about the UI component to render and its properties.

    Attributes:
        type: Literal type indicating this is a UI message.
        id: Unique identifier for the UI message.
        name: Name of the UI component to render.
        props: Properties to pass to the UI component.
        metadata: Additional metadata about the UI message.
    """

    type: Literal["ui"]
    id: str
    name: str
    props: dict[str, Any]
    metadata: dict[str, Any]


class RemoveUIMessage(TypedDict):
    """A message type for removing UI components in LangGraph.

    This TypedDict represents a message that can be sent to remove a UI component
    from the current state.

    Attributes:
        type: Literal type indicating this is a remove-ui message.
        id: Unique identifier of the UI component to remove.
    """

    type: Literal["remove-ui"]
    id: str


AnyUIMessage = Union[UIMessage, RemoveUIMessage]


def push_ui_message(
    name: str,
    props: dict[str, Any],
    *,
    id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    message: Optional[AnyMessage] = None,
    state_key: str = "ui",
) -> UIMessage:
    """Push a new UI message to update the UI state.

    This function creates and sends a UI message that will be rendered in the UI.
    It also updates the graph state with the new UI message.

    Args:
        name: Name of the UI component to render.
        props: Properties to pass to the UI component.
        id: Optional unique identifier for the UI message.
            If not provided, a random UUID will be generated.
        metadata: Optional additional metadata about the UI message.
        message: Optional message object to associate with the UI message.
        state_key: Key in the graph state where the UI messages are stored.
            Defaults to "ui".

    Returns:
        The created UI message.

    Example:

    .. code-block:: python

        message = push_ui_message(
            name="component-name",
            props={"content": "Hello world"},
        )

    """

    writer = get_stream_writer()
    config = get_config()

    message_id = None
    if message:
        if isinstance(message, dict) and "id" in message:
            message_id = message.get("id")
        elif hasattr(message, "id"):
            message_id = message.id

    evt: UIMessage = {
        "type": "ui",
        "id": id or str(uuid4()),
        "name": name,
        "props": props,
        "metadata": {
            **(config.get("metadata") or {}),
            "tags": config.get("tags", None),
            "name": config.get("run_name", None),
            "run_id": config.get("run_id", None),
            **(metadata or {}),
            **({"message_id": message_id} if message_id else {}),
        },
    }

    writer(evt)
    config[CONF][CONFIG_KEY_SEND]([(state_key, evt)])

    return evt


def remove_ui_message(id: str, *, state_key: str = "ui") -> RemoveUIMessage:
    """Delete a UI message by ID from the UI state.

    This function creates and sends a message to remove a UI component from the current state.
    It also updates the graph state to remove the UI message.

    Args:
        id: Unique identifier of the UI component to remove.
        state_key: Key in the graph state where the UI messages are stored. Defaults to "ui".

    Returns:
        The remove UI message.

    Example:

    .. code-block:: python

        remove_message = remove_ui_message("message-123")

    """
    writer = get_stream_writer()
    config = get_config()

    evt: RemoveUIMessage = {"type": "remove-ui", "id": id}

    writer(evt)
    config[CONF][CONFIG_KEY_SEND]([(state_key, evt)])

    return evt


def reduce_ui_messages(
    left: Union[list[AnyUIMessage], AnyUIMessage],
    right: Union[list[AnyUIMessage], AnyUIMessage],
) -> list[AnyUIMessage]:
    """Merge two lists of UI messages, supporting removing UI messages.

    This function combines two lists of UI messages, handling both regular UI messages
    and remove-ui messages. When a remove-ui message is encountered, it removes any
    UI message with the matching ID from the current state.

    Args:
        left: First list of UI messages or single UI message.
        right: Second list of UI messages or single UI message.

    Returns:
        Combined list of UI messages with removals applied.

    Example:

    .. code-block:: python

        messages = reduce_ui_messages(
            [{"type": "ui", "id": "1", "name": "Chat", "props": {}}],
            {"type": "remove-ui", "id": "1"}
        )

    """
    if not isinstance(left, list):
        left = [left]

    if not isinstance(right, list):
        right = [right]

    new_state = left.copy()
    for m in right:
        if m.get("type") == "remove-ui":
            new_state = [m for m in new_state if m.get("id") != m.get("id")]
        else:
            new_state.append(m)

    return new_state
